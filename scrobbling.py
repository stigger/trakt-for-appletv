import pickle
import os
import re
from threading import Thread
from urllib.request import urlopen, Request

from io import BytesIO

import yaml
from lxml import etree
import json

from trakt import Trakt
from media_remote import MediaRemoteProtocol
from protobuf_gen import ProtocolMessage_pb2, ClientUpdatesConfigMessage_pb2, SetStateMessage_pb2, ContentItem_pb2, \
    TransactionMessage_pb2, CommandInfo_pb2


class ScrobblingRemoteProtocol(MediaRemoteProtocol):
    def __init__(self, config) -> None:
        super().__init__(config)
        self.now_playing_metadata = None
        self.valid_player = None
        self.playback_rate = None
        self.playback_state = None
        self.skip_command_supported = False
        self.last_elapsed_time = None
        self.last_elapsed_time_timestamp = None
        self.app_handlers = {'com.apple.TVShows': self.handle_tvshows,
                             'com.apple.TVWatchList': self.handle_tv_app,
                             'com.apple.TVMovies': self.handle_movies,
                             'com.netflix.Netflix': self.handle_netflix,
                             'com.amazon.aiv.AIVApp': self.handle_amazon}

        Trakt.configuration.defaults.client(id='dc705f550f50706bdd7bd55db120235cc68899dbbfb4fbc171384c1c1d30d7d4',
                                            secret='f9aba211b886ea9f31a57c952cd0b5ab702501808db50584a24a5cc07466179d')
        Trakt.on('oauth.token_refreshed', self.on_trakt_token_refreshed)
        self.authenticate_trakt()

    def authenticate_trakt(self):
        if os.path.exists('data/trakt.auth'):
            response = pickle.load(open('data/trakt.auth', 'rb'))
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
            self.skip_command_supported = False
            for command in msg.Extensions[SetStateMessage_pb2.setStateMessage].supportedCommands.supportedCommands:
                if command.command == CommandInfo_pb2.SkipForward:
                    self.skip_command_supported = True
                    break
            if not self.skip_command_supported and not state_msg.HasField('displayID'):
                self.stop_scrobbling()
                self.now_playing_metadata = None
            if state_msg.displayID in self.app_handlers:
                self.valid_player = state_msg.displayID
            else:
                self.valid_player = None
        elif msg.type == ProtocolMessage_pb2.ProtocolMessage.TRANSACTION_MESSAGE:
            transaction = ContentItem_pb2.ContentItem()
            transaction.ParseFromString(
                msg.Extensions[TransactionMessage_pb2.transactionMessage].packets.packets[0].packetData)
            self.now_playing_metadata = transaction.metadata
            if self.valid_player is not None:
                self.update_scrobbling()

    def post_trakt_update(self, operation, done=None):
        def inner():
            progress = self.now_playing_metadata.elapsedTime * 100 / self.now_playing_metadata.duration
            if self.valid_player is not None:
                handler = self.app_handlers[self.valid_player]
                if handler is not None:
                    handler(operation, progress)
                if done is not None:
                    done()
        Thread(target=lambda: inner()).start()

    def is_invalid_metadata(self):
        return self.now_playing_metadata is None or self.now_playing_metadata.duration < 300

    def update_scrobbling(self):
        if self.is_invalid_metadata():
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
        self.playback_state = None
        self.last_elapsed_time = None
        self.last_elapsed_time_timestamp = None
        if not self.is_invalid_metadata():
            def done():
                self.now_playing_metadata = None
            self.post_trakt_update(Trakt['scrobble'].stop, done)

    def handle_tv_app(self, operation, progress):
        self.handle_tvshows(operation, progress)

    def handle_tvshows(self, operation, progress):
        if self.now_playing_metadata.HasField('seasonNumber'):
            season_number = self.now_playing_metadata.seasonNumber
            episode_number = self.now_playing_metadata.episodeNumber
        else:
            season_number, episode_number = self.get_itunes_title(self.now_playing_metadata.contentIdentifier)
        operation(show={'title': self.get_title()},
                  episode={'season': season_number, 'number': episode_number},
                  progress=progress)

    def get_title(self):
        if self.now_playing_metadata is None:
            return None
        title = self.now_playing_metadata.seriesName
        if len(title) == 0:
            title = self.now_playing_metadata.title
        return title

    def handle_movies(self, operation, progress):
        movie = {}
        match = re.search('(.*) \((\d\d\d\d)\)', self.now_playing_metadata.title)
        if match is None:
            movie['title'] = self.now_playing_metadata.title
        else:
            movie['title'] = match.group(1)
            movie['year'] = match.group(2)
        operation(movie=movie, progress=progress)

    def get_itunes_title(self, contentIdentifier):
        known = self.config['itunes']['titles'].get(contentIdentifier)
        if known:
            return known['season'], known['episode']
        result = json.loads(urlopen('https://itunes.apple.com/lookup?country=de&id=' + contentIdentifier).read()
                            .decode('utf-8'))['results'][0]
        match = re.match("^Season (\d\d?), Episode (\d\d?): ", result['trackName'])
        if match is not None:
            season = int(match.group(1))
            episode = int(match.group(2))
        else:
            season = int(re.match(".*, Season (\d\d?)( \(Uncensored\))?$", result['collectionName']).group(1))
            episode = int(result['trackNumber'])
        self.config['itunes']['titles'][contentIdentifier] = {'season': season, 'episode': episode}
        yaml.dump(self.config, open('data/config.yml', 'w'), default_flow_style=False)
        return season, episode

    def handle_netflix(self, operation, progress):
        match = re.match('^S(\d\d?): E(\d\d?) ', self.now_playing_metadata.title)
        if match is not None:
            title = self.get_netflix_title(self.now_playing_metadata.contentIdentifier)
            operation(show={'title': title},
                      episode={'season': match.group(1), 'number': match.group(2)},
                      progress=progress)
        else:
            operation(movie={'title': self.now_playing_metadata.title}, progress=progress)

    def get_netflix_title(self, contentIdentifier):
        known = self.config['netflix']['titles'].get(contentIdentifier)
        if known:
            return known
        data = urlopen('https://www.netflix.com/title/' + contentIdentifier).read()
        xml = etree.parse(BytesIO(data), etree.HTMLParser())
        info = json.loads(xml.xpath('//script')[0].text)
        title = info['name']
        self.config['netflix']['titles'][contentIdentifier] = title
        yaml.dump(self.config, open('data/config.yml', 'w'), default_flow_style=False)
        return title

    def handle_amazon(self, operation, progress):
        title, season, episode = self.get_amazon_details(self.now_playing_metadata.contentIdentifier)
        operation(show={'title': title},
                  episode={'season': season, 'number': episode},
                  progress=progress)

    def get_amazon_details(self, contentIdentifier):
        contentIdentifier = contentIdentifier.replace(":DE", "")
        known = self.config['amazon']['titles'].get(contentIdentifier)
        if known:
            return known['title'], known['season'], known['episode']
        url = self.config['amazon']['get_playback_resources_url'] % contentIdentifier
        r = Request(url, None, {'Cookie': self.config['amazon']['cookie']})
        data = json.loads(urlopen(r).read().decode('utf-8'))
        title = None
        season = None
        episode = data['catalogMetadata']['catalog']['episodeNumber']
        for f in data['catalogMetadata']['family']['tvAncestors']:
            if f['catalog']['type'] == 'SEASON':
                season = f['catalog']['seasonNumber']
            elif f['catalog']['type'] == 'SHOW':
                title = f['catalog']['title'].replace("[OV/OmU]", "").replace("[OV]", "").replace("[Ultra HD]", "")\
                    .replace("[dt./OV]", "").replace("(4K UHD)", "").strip()
        self.config['amazon']['titles'][contentIdentifier] = {'title': title, 'season': season, 'episode': episode}
        yaml.dump(self.config, open('data/config.yml', 'w'), default_flow_style=False)
        return title, season, episode

    @staticmethod
    def on_trakt_token_refreshed(response):
        pickle.dump(response, open('data/trakt.auth', 'wb'))
