import asyncio
import sys
import os
import uuid

import varint
from pairing import introduce, pairing, setup_keys, verify
from protobuf_gen import ProtocolMessage_pb2


class MediaRemoteProtocol(asyncio.Protocol):
    def __init__(self, config) -> None:
        super().__init__()
        self.config = config
        self.pending_data = bytes()
        self.output_key = None
        self.output_nonce = 0
        self.input_key = None
        self.input_nonce = 0
        self.transport = None

    @staticmethod
    def do_encryption_operation(operation, data, nonce):
        res = operation(b'\0\0\0\0' + nonce.to_bytes(8, sys.byteorder), data, None)
        nonce += 1
        if nonce == 0xFFFFFFFF:
            nonce = 0
        return res, nonce

    def encrypt(self, data):
        res, self.output_nonce = self.do_encryption_operation(self.output_key.encrypt, data, self.output_nonce)
        return res

    def decrypt(self, data):
        res, self.input_nonce = self.do_encryption_operation(self.input_key.decrypt, data, self.input_nonce)
        return res

    def connection_made(self, transport):
        self.transport = transport
        socket = transport.get_extra_info('socket')

        socket.setblocking(True)
        transport.pause_reading()

        introduce(socket, self.config['device_info'])

        if not os.path.exists('data/pairing.state'):
            pairing(socket, self.config['device_info'])

        self.output_key, self.input_key = setup_keys(verify(socket, self.config['device_info']))
        self.output_nonce = 0
        self.input_nonce = 0

        socket.setblocking(False)
        transport.resume_reading()

        msg = ProtocolMessage_pb2.ProtocolMessage()
        msg.type = ProtocolMessage_pb2.ProtocolMessage.SET_READY_STATE_MESSAGE
        self.send(msg)
        print("ready!")

    def data_received(self, data):
        self.pending_data += data

        while len(self.pending_data) > 0:
            length = varint.decode_bytes(self.pending_data)
            length_bytes = len(varint.encode(length))

            if len(self.pending_data) < length + length_bytes:
                break
            data = self.pending_data[length_bytes:length_bytes + length]
            self.pending_data = self.pending_data[length_bytes + length:]

            msg = ProtocolMessage_pb2.ProtocolMessage()
            decrypted = self.decrypt(data)
            msg.ParseFromString(decrypted)

            self.message_received(msg)

    def message_received(self, msg):
        pass

    def send(self, msg):
        msg.identifier = str(uuid.uuid1())
        data = self.encrypt(msg.SerializeToString())
        self.transport.write(varint.encode(len(data)))
        self.transport.write(data)
