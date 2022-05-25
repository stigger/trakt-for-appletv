import os
import pickle
from abc import ABC
from datetime import datetime, timedelta
from typing import Optional, Tuple

import pytz as pytz
from helpers.trakt_api import TraktAPI
from atv.playstatus_tracker import PlayStatusTracker


class TraktScrobbler(PlayStatusTracker, ABC):
    def __init__(self):
        self.watched_percent = 90
        self.currently_scrobbling: Optional[Tuple[dict, datetime, datetime]] = None
        self.auth_file = 'data/trakt.auth'
        self.api = TraktAPI(client_id='34ea1338c16b79f0067b31f4a8e6a35a5e6b9ccc6476e4e7e7300f059b4514d7',
                            client_secret='8453ad791caa51872a2fd9d18b273f55348297cf14885dbc94cb91123ce08d18',
                            redirect_uri='urn:ietf:wg:oauth:2.0:oob',
                            on_token_refresh=self.on_trakt_oauth_changed)
        super().__init__()

    async def setup(self, **kwargs) -> None:
        await self.authenticate_trakt()
        await super().setup(**kwargs)

    async def cleanup(self, **kwargs) -> None:
        await self.stop_scrobbling()
        await super().cleanup(**kwargs)

    async def authenticate_trakt(self):
        """ Authenticate with trakt.tv"""
        if os.path.exists(self.auth_file):
            response = pickle.load(open(self.auth_file, 'rb'))
            await self.api.update_data(response)
        else:
            auth_url = self.api.get_authorize_url()
            print(f'Navigate to {auth_url}')
            pin = input('Authorization code: ')
            new_oauth = await self.api.get_access_token(pin)
            self.on_trakt_oauth_changed(new_oauth)

    def on_trakt_oauth_changed(self, new_oauth):
        """ Passed as callback to TraktAPI, saves the new oauth data to file """
        pickle.dump(new_oauth, open(self.auth_file, 'wb'))

    async def start_scrobbling(self, **kwargs):
        now = datetime.now(pytz.utc)
        secs_left = self.curr_state.total_time - self.curr_state.position
        started_at = now - timedelta(seconds=self.curr_state.position)
        expires_at = now + timedelta(seconds=secs_left)
        self.currently_scrobbling = kwargs, started_at, expires_at
        try:
            await self.api.scrobble('start', **kwargs)
            await self.print_info(f'Started {kwargs}', prefix='TRAKT', success=True)
        except Exception as e:
            await self.print_warning(f'Failed to start scrobble {e}')

    async def stop_scrobbling(self):
        if self.currently_scrobbling is None:
            await self.print_debug('No scrobble to stop', prefix='TRAKT')
            return
        current = await self.calculate_current_scrobble()
        self.currently_scrobbling = None
        if current:
            progress = current.get('progress')
            if progress and progress > self.watched_percent:
                await self.api.scrobble('stop', **current)
                await self.print_info(f'Stopped {current}', prefix='TRAKT', success=True)
            elif progress:
                await self.api.scrobble('pause', **current)
                await self.print_info(f'Paused {current}', prefix='TRAKT', success=True)

    async def calculate_current_scrobble(self):
        kwargs, started_at, expires_at = self.currently_scrobbling
        now = datetime.now(pytz.utc)
        progress = (now - started_at).seconds / (expires_at - started_at).seconds * 100
        kwargs['progress'] = round(progress, 1)
        return kwargs
