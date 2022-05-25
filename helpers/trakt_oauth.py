import aiohttp
import pytz as pytz
from datetime import datetime, timedelta


class TraktOAuth:
    def __init__(self, client_id, client_secret, redirect_uri):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.expires_at = None
        self.access_token = None
        self.refresh_token = None
        self.ok_codes = [200, 201, 204, 409, 422]
        self.status_codes = {
            200: 'OK',
            201: 'Created',
            202: 'Accepted',
            204: 'No Content',
            400: 'Bad Request',
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Not Found',
            405: 'Method Not Allowed',
            409: 'Conflict',
            422: 'Unprocessable Entity',
            429: 'Too Many Requests',
            500: 'Internal Server Error',
            502: 'Bad Gateway',
            503: 'Service Unavailable',
            504: 'Gateway Timeout'
        }

    def get_authorize_url(self):
        return f'https://trakt.tv/oauth/authorize?response_type=code&client_id=' \
               f'{self.client_id}&redirect_uri={self.redirect_uri}'

    async def get_access_token(self, code):
        async with aiohttp.ClientSession() as session:
            async with session.post('https://api.trakt.tv/oauth/token',
                                    data={'code': code,
                                          'client_id': self.client_id,
                                          'client_secret': self.client_secret,
                                          'redirect_uri': self.redirect_uri,
                                          'grant_type': 'authorization_code'}) as resp:
                if resp.status not in self.ok_codes:
                    raise ValueError(f'{self.status_codes[resp.status] or resp.status}')
                data = await resp.json()
                await self.update_data(data)
                return data

    async def is_token_expired(self):
        if self.expires_at is None:
            return True
        return self.expires_at < datetime.now(pytz.utc)

    async def refresh_access_token(self):
        async with aiohttp.ClientSession() as session:
            async with session.post('https://api.trakt.tv/oauth/token',
                                    data={'refresh_token': self.refresh_token,
                                          'client_id': self.client_id,
                                          'client_secret': self.client_secret,
                                          'redirect_uri': self.redirect_uri,
                                          'grant_type': 'refresh_token'}) as resp:
                if resp.status not in self.ok_codes:
                    raise ValueError(f'{self.status_codes[resp.status] or resp.status}')
                data = await resp.json()
                await self.update_data(data)
                return data

    async def update_token_if_expired(self):
        if await self.is_token_expired():
            return await self.refresh_access_token()

    async def update_data(self, data):
        self.access_token = data['access_token']
        self.refresh_token = data['refresh_token']
        created_at = datetime.fromtimestamp(data['created_at'], pytz.utc)
        self.expires_at = created_at + timedelta(seconds=data['expires_in'])

    async def revoke_access_token(self):
        async with aiohttp.ClientSession() as session:
            async with session.post('https://api.trakt.tv/oauth/revoke',
                                    data={'token': self.access_token,
                                          'client_id': self.client_id,
                                          'client_secret': self.client_secret}) as resp:
                data = await resp.json()
                return data

    async def get_user_token(self):
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.trakt.tv/users/me',
                                   headers={'Authorization': f'Bearer {self.access_token}'}) as resp:
                data = await resp.json()
                return data
