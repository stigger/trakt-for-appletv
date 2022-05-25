from helpers.trakt_oauth import TraktOAuth
import aiohttp
from async_retrying import retry


class TraktAPI(TraktOAuth):
    def __init__(self, client_id, client_secret, redirect_uri, on_token_refresh=None):
        super(TraktAPI, self).__init__(client_id, client_secret, redirect_uri)
        self.on_token_refresh = on_token_refresh

    @retry(attempts=3)
    async def post(self, path, data):
        # check if expired
        new_oauth = await self.update_token_if_expired()
        if self.access_token is None:
            raise ValueError('No access token')
        # callback
        if self.on_token_refresh and new_oauth:
            self.on_token_refresh(new_oauth)

        # build headers
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
            'trakt-api-version': '2',
            'trakt-api-key': self.client_id
        }

        # build url
        url = f'https://api.trakt.tv/{path}'

        # build request
        data = self.dict_filter_empty(data)
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=data, headers=headers) as response:
                if response.status not in self.ok_codes:
                    raise ValueError(f'Trakt API returned status code {response.status}')
                return await response.json()

    async def scrobble(self, action, **kwargs):
        path = 'scrobble/{}'.format(action)
        return await self.post(path, kwargs)

    @staticmethod
    def dict_filter_empty(d):
        return {k: v for k, v in d.items() if v}
