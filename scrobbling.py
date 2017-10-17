import pickle
import os
import re

from trakt import Trakt
from media_remote import MediaRemoteProtocol
from protobuf_gen import ProtocolMessage_pb2, ClientUpdatesConfigMessage_pb2, SetStateMessage_pb2, ContentItem_pb2, \
    TransactionMessage_pb2


class ScrobblingRemoteProtocol(MediaRemoteProtocol):
    def __init__(self, config) -> None:
        super().__init__(config)
        self.now_playing_metadata = None
        self.valid_player = False
        self.playback_rate = None
        self.last_elapsed_time = None
        self.last_elapsed_time_timestamp = None

        Trakt.configuration.defaults.client(id='dc705f550f50706bdd7bd55db120235cc68899dbbfb4fbc171384c1c1d30d7d4',
                                            secret='f9aba211b886ea9f31a57c952cd0b5ab702501808db50584a24a5cc07466179d')
        Trakt.on('oauth.token_refreshed', self.on_trakt_token_refreshed)
        self.authenticate_trakt()

    def authenticate_trakt(self):
        if os.path.exists('trakt.auth'):
            response = pickle.load(open('trakt.auth', 'rb'))
        else:
            print('Navigate to %s' % Trakt['oauth'].authorize_url('urn:ietf:wg:oauth:2.0:oob'))
            pin = input('Authorization code: ')
            response = Trakt['oauth'].token(pin, 'urn:ietf:wg:oauth:2.0:oob')
            self.on_trakt_token_refreshed(response)
        Trakt.configuration.defaults.oauth.from_response(response, refresh=True)

    def connection_made(self, transport):
        super().connection_made(transport)

        msg = ProtocolMessage_pb2.ProtocolMessage()
        msg.type = ProtocolMessage_pb2.ProtocolMessage.CLIENT_UPDATES_CONFIG_MESSAGE
        msg.Extensions[ClientUpdatesConfigMessage_pb2.clientUpdatesConfigMessage].nowPlayingUpdates = True
        msg.Extensions[ClientUpdatesConfigMessage_pb2.clientUpdatesConfigMessage].artworkUpdates = True
        self.send(msg)

    def message_received(self, msg):
        super().message_received(msg)

        if msg.type == ProtocolMessage_pb2.ProtocolMessage.SET_STATE_MESSAGE:
            state_msg = msg.Extensions[SetStateMessage_pb2.setStateMessage]
            self.valid_player = state_msg.displayID in self.config['scrobble_apps']
            if not state_msg.nowPlayingInfo.HasField('title'):
                if self.valid_player:
                    self.stop_scrobbling()
                self.now_playing_metadata = None
        elif msg.type == ProtocolMessage_pb2.ProtocolMessage.TRANSACTION_MESSAGE:
            transaction = ContentItem_pb2.ContentItem()
            transaction.ParseFromString(
                msg.Extensions[TransactionMessage_pb2.transactionMessage].packets.packets[0].packetData)
            self.now_playing_metadata = transaction.metadata
            if self.valid_player:
                self.update_scrobbling()

    def post_trakt_update(self, operation):
        progress = self.now_playing_metadata.elapsedTime * 100 / self.now_playing_metadata.duration
        if self.now_playing_metadata.HasField('seriesName'):
            operation(show={'title': self.now_playing_metadata.seriesName},
                      episode={'season': self.now_playing_metadata.seasonNumber,
                               'number': self.now_playing_metadata.episodeNumber},
                      progress=progress)
        else:
            movie = {}
            match = re.search('(.*) \((\d\d\d\d)\)', self.now_playing_metadata.title)
            if match is None:
                movie['title'] = self.now_playing_metadata.title
            else:
                movie['title'] = match.group(1)
                movie['year'] = match.group(2)
            operation(movie=movie, progress=progress)

    def is_valid_metadata(self):
        return self.now_playing_metadata is None or self.now_playing_metadata.HasField('inferredTimestamp') or \
               self.now_playing_metadata.duration < 300

    def update_scrobbling(self):
        if self.is_valid_metadata():
            return

        if self.now_playing_metadata.playbackRate == 1.0:
            if self.last_elapsed_time is not None:
                timestampDiff = self.now_playing_metadata.elapsedTimeTimestamp - self.last_elapsed_time_timestamp
                elapsedDiff = self.now_playing_metadata.elapsedTime - self.last_elapsed_time
                if abs(timestampDiff - elapsedDiff) > 5:
                    self.playback_rate = self.now_playing_metadata.playbackRate
                    self.post_trakt_update(Trakt['scrobble'].start)
            self.last_elapsed_time = self.now_playing_metadata.elapsedTime
            self.last_elapsed_time_timestamp = self.now_playing_metadata.elapsedTimeTimestamp

        if self.now_playing_metadata.playbackRate != self.playback_rate:
            if self.now_playing_metadata.playbackRate == 0.0 and self.playback_rate is not None:
                self.post_trakt_update(Trakt['scrobble'].pause)
            elif self.now_playing_metadata.playbackRate == 1.0:
                self.post_trakt_update(Trakt['scrobble'].start)
            self.playback_rate = self.now_playing_metadata.playbackRate

    def stop_scrobbling(self):
        self.playback_rate = None
        self.last_elapsed_time = None
        self.last_elapsed_time_timestamp = None
        if self.now_playing_metadata is not None and not self.is_valid_metadata():
            self.post_trakt_update(Trakt['scrobble'].stop)

    @staticmethod
    def on_trakt_token_refreshed(response):
        Trakt.configuration.defaults.oauth.from_response(response, refresh=True)
        pickle.dump(response, open('trakt.auth', 'wb'))
