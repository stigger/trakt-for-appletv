import uuid

from srptools import SRPContext, SRPClientSession
from srptools.constants import PRIME_3072, PRIME_3072_GEN
import varint
import ed25519
import hashlib
import pickle
import binascii
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from protobuf_gen import ProtocolMessage_pb2, DeviceInfoMessage_pb2, CryptoPairingMessage_pb2

kTLVType_Method = b'\x00'
kTLVType_Identifier = b'\x01'
kTLVType_Salt = b'\x02'
kTLVType_PublicKey = b'\x03'
kTLVType_Proof = b'\x04'
kTLVType_EncryptedData = b'\x05'
kTLVType_State = b'\x06'
kTLVType_Error = b'\x07'
kTLVType_RetryDelay = b'\x08'
kTLVType_Certificate = b'\x09'
kTLVType_Signature = b'\x0a'
kTLVType_Permissions = b'\x0b'
kTLVType_FragmentData = b'\x0c'
kTLVType_FragmentLast = b'\x0d'
kTLVType_Separator = b'\xff'


def introduce(socket, config):
    msg = ProtocolMessage_pb2.ProtocolMessage()
    msg.type = ProtocolMessage_pb2.ProtocolMessage.DEVICE_INFO_MESSAGE
    msg.identifier = str(uuid.uuid1())
    device_info = msg.Extensions[DeviceInfoMessage_pb2.deviceInfoMessage]
    device_info.uniqueIdentifier = config['unique_identifier']
    device_info.name = config['name']
    device_info.systemBuildVersion = '13G36'
    device_info.applicationBundleIdentifier = 'gr.stig.appletv-scrobbler'
    device_info.protocolVersion = 1
    device_info.lastSupportedMessageType = 72

    send(msg, socket)
    receive(socket)


def receive(socket):
    bytez = socket.recv(2)
    if bytez == '':
        return None
    length = varint.decode_bytes(bytez)
    if length <= 127:
        bytez = bytez[1:]
    else:
        bytez = bytes()
    bytez = bytez + socket.recv(length - len(bytez))

    msg = ProtocolMessage_pb2.ProtocolMessage()
    msg.ParseFromString(bytez)
    return msg


def send(msg, socket):
    data = msg.SerializeToString()
    socket.sendall(varint.encode(len(data)))
    socket.send(data)


def tlv_build(d):
    b = b""
    for k, v in d.items():
        while len(v) > 0:
            b += k
            length = min(255, len(v))
            b += bytes([length])
            b += v[:length]
            v = v[length:]
    return b


def tlv_parse(b):
    d = {}
    pos = 0
    while pos < len(b):
        tag = bytes([b[pos]])
        pos += 1
        length = b[pos]
        pos += 1
        have_data = True
        data = b""
        while have_data:
            have_data = False
            data += b[pos:pos + length]
            pos += length
            if length == 255 and pos < len(b) and bytes([b[pos]]) == tag:
                pos += 1
                length = b[pos]
                have_data = True
                pos += 1
        d[tag] = data
    return d


def pairing(socket, config):
    msg = ProtocolMessage_pb2.ProtocolMessage()
    msg.type = ProtocolMessage_pb2.ProtocolMessage.CRYPTO_PAIRING_MESSAGE
    msg.Extensions[CryptoPairingMessage_pb2.cryptoPairingMessage].status = 0
    msg.Extensions[CryptoPairingMessage_pb2.cryptoPairingMessage].pairingData = tlv_build({kTLVType_Method: b'\x00',
                                                                                           kTLVType_State: b'\x01'})
    msg.Extensions[CryptoPairingMessage_pb2.cryptoPairingMessage].state = 2
    send(msg, socket)
    msg = receive(socket)
    parsed = tlv_parse(msg.Extensions[CryptoPairingMessage_pb2.cryptoPairingMessage].pairingData)

    appletv_public = parsed[kTLVType_PublicKey]
    salt = parsed[kTLVType_Salt]

    code = input("Enter code displayed by Apple TV: ")
    session = SRPClientSession(SRPContext("Pair-Setup", code, PRIME_3072, PRIME_3072_GEN, hashlib.sha512,
                                          bits_random=256, bits_salt=128))
    session.process(binascii.hexlify(appletv_public), binascii.hexlify(salt))
    our_public = binascii.unhexlify(session.public)
    key_proof = binascii.unhexlify(session.key_proof)

    msg = ProtocolMessage_pb2.ProtocolMessage()
    msg.type = ProtocolMessage_pb2.ProtocolMessage.CRYPTO_PAIRING_MESSAGE
    msg.Extensions[CryptoPairingMessage_pb2.cryptoPairingMessage].status = 0
    tlv = tlv_build({kTLVType_State: b'\x03', kTLVType_PublicKey: our_public, kTLVType_Proof: key_proof})
    msg.Extensions[CryptoPairingMessage_pb2.cryptoPairingMessage].pairingData = tlv

    send(msg, socket)
    msg = receive(socket)
    parsed = tlv_parse(msg.Extensions[CryptoPairingMessage_pb2.cryptoPairingMessage].pairingData)

    proof = parsed[kTLVType_Proof]
    if not session.verify_proof(binascii.hexlify(proof)):
        print("proof does not match!")
        exit(1)

    ltsk, ltpk = ed25519.create_keypair()

    hkdf = HKDF(hashes.SHA512(), 32, b"Pair-Setup-Controller-Sign-Salt", b"Pair-Setup-Controller-Sign-Info",
                default_backend())
    x = hkdf.derive(binascii.unhexlify(session.key))

    device_id = bytes(config['device_id'], 'utf-8')
    info = x + device_id + ltpk.to_bytes()
    subtlv = tlv_build(
        {kTLVType_Identifier: device_id, kTLVType_PublicKey: ltpk.to_bytes(), kTLVType_Signature: ltsk.sign(info)})

    hkdf = HKDF(hashes.SHA512(), 32, b"Pair-Setup-Encrypt-Salt", b"Pair-Setup-Encrypt-Info",
                default_backend())
    x = hkdf.derive(binascii.unhexlify(session.key))

    cha_cha_poly = ChaCha20Poly1305(x)
    encrypted = cha_cha_poly.encrypt(b"\0\0\0\0PS-Msg05", subtlv, None)

    msg = ProtocolMessage_pb2.ProtocolMessage()
    msg.type = ProtocolMessage_pb2.ProtocolMessage.CRYPTO_PAIRING_MESSAGE
    msg.Extensions[CryptoPairingMessage_pb2.cryptoPairingMessage].status = 0
    msg.Extensions[CryptoPairingMessage_pb2.cryptoPairingMessage].pairingData = tlv_build({kTLVType_State: b'\x05',
                                                                                           kTLVType_EncryptedData: encrypted})
    send(msg, socket)
    msg = receive(socket)
    parsed = tlv_parse(msg.Extensions[CryptoPairingMessage_pb2.cryptoPairingMessage].pairingData)

    encrypted = parsed[kTLVType_EncryptedData]
    subtlv = tlv_parse(cha_cha_poly.decrypt(b"\0\0\0\0PS-Msg06", encrypted, None))

    hkdf = HKDF(hashes.SHA512(), 32, b"Pair-Setup-Accessory-Sign-Salt", b"Pair-Setup-Accessory-Sign-Info",
                default_backend())
    x = hkdf.derive(binascii.unhexlify(session.key))

    info = x + subtlv[kTLVType_Identifier] + subtlv[kTLVType_PublicKey]
    ed25519.VerifyingKey(subtlv[kTLVType_PublicKey]).verify(subtlv[kTLVType_Signature], info)

    pickle.dump({"seed": ltsk.to_seed(),
                 "peer_id": subtlv[kTLVType_Identifier],
                 "peer_public_key": subtlv[kTLVType_PublicKey]},
                open("data/pairing.state", "wb"))


def verify(socket, config):
    data = pickle.load(open("data/pairing.state", "rb"))
    ltsk = ed25519.SigningKey(data['seed'])
    peer_id = data['peer_id']
    peer_public_key = ed25519.VerifyingKey(data['peer_public_key'])

    randpk = X25519PrivateKey.generate()

    msg = ProtocolMessage_pb2.ProtocolMessage()
    msg.type = ProtocolMessage_pb2.ProtocolMessage.CRYPTO_PAIRING_MESSAGE
    msg.Extensions[CryptoPairingMessage_pb2.cryptoPairingMessage].status = 0
    public_key_bytes = randpk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    msg.Extensions[CryptoPairingMessage_pb2.cryptoPairingMessage].pairingData = tlv_build(
        {kTLVType_PublicKey: public_key_bytes, kTLVType_State: b'\x01'})

    send(msg, socket)
    msg = receive(socket)
    parsed = tlv_parse(msg.Extensions[CryptoPairingMessage_pb2.cryptoPairingMessage].pairingData)

    shared_key = randpk.exchange(X25519PublicKey.from_public_bytes(parsed[kTLVType_PublicKey]))

    hkdf = HKDF(hashes.SHA512(), 32, b"Pair-Verify-Encrypt-Salt", b"Pair-Verify-Encrypt-Info",
                default_backend())
    x = hkdf.derive(shared_key)
    cha_cha_poly = ChaCha20Poly1305(x)

    encrypted = parsed[kTLVType_EncryptedData]
    subtlv = tlv_parse(cha_cha_poly.decrypt(b"\0\0\0\0PV-Msg02", encrypted, None))

    assert peer_id == subtlv[kTLVType_Identifier]

    info = parsed[kTLVType_PublicKey] + subtlv[kTLVType_Identifier] + public_key_bytes
    peer_public_key.verify(subtlv[kTLVType_Signature], info)

    device_id = bytes(config['device_id'], 'utf-8')
    info = public_key_bytes + device_id + parsed[kTLVType_PublicKey]
    subtlv = tlv_build({kTLVType_Identifier: device_id, kTLVType_Signature: ltsk.sign(info)})

    encrypted = cha_cha_poly.encrypt(b"\0\0\0\0PV-Msg03", subtlv, None)

    msg = ProtocolMessage_pb2.ProtocolMessage()
    msg.type = ProtocolMessage_pb2.ProtocolMessage.CRYPTO_PAIRING_MESSAGE
    msg.Extensions[CryptoPairingMessage_pb2.cryptoPairingMessage].status = 0
    msg.Extensions[CryptoPairingMessage_pb2.cryptoPairingMessage].pairingData = tlv_build({kTLVType_State: b'\x03',
                                                                                           kTLVType_EncryptedData: encrypted})
    send(msg, socket)
    msg = receive(socket)
    parsed = tlv_parse(msg.Extensions[CryptoPairingMessage_pb2.cryptoPairingMessage].pairingData)

    assert parsed[kTLVType_State] == b'\x04'
    return shared_key


def setup_keys(shared_key):
    output_key = ChaCha20Poly1305(HKDF(hashes.SHA512(), 32, b"MediaRemote-Salt", b"MediaRemote-Write-Encryption-Key",
                                       default_backend()).derive(shared_key))
    input_key = ChaCha20Poly1305(HKDF(hashes.SHA512(), 32, b"MediaRemote-Salt", b"MediaRemote-Read-Encryption-Key",
                                      default_backend()).derive(shared_key))
    return output_key, input_key
