import asyncio
import re
from json import JSONDecodeError
from typing import Optional, Tuple
import aiohttp
from dateutil import parser
from lxml import etree
from io import BytesIO
import json

from atv.trakt_scrobbler import TraktScrobbler


class ScrobblingProtocol(TraktScrobbler):
    def __init__(self):
        self.netflix_titles = {}
        self.itunes_titles = {}
        self.amazon_titles = {}
        self.now_playing_description = None
        self.app_handlers = {'com.apple.TVShows': self.handle_tvshows,
                             'com.apple.TVWatchList': self.handle_tv_app,
                             'com.apple.TVMovies': self.handle_movies,
                             'com.netflix.Netflix': self.handle_netflix,
                             'com.amazon.aiv.AIVApp': self.handle_amazon}
        self.pending_scrobble = None
        self.session: Optional[aiohttp.ClientSession] = None
        super(ScrobblingProtocol, self).__init__()

    async def cleanup(self, **kwargs) -> None:
        """ Cancels any pending scrobble and closes the aiohttp session """
        if self.pending_scrobble:
            self.pending_scrobble.cancel()
            self.pending_scrobble = None
        await self.session.close()
        await super(ScrobblingProtocol, self).cleanup(**kwargs)

    async def setup(self, **kwargs) -> None:
        """ Sets up the aiohttp session  """
        self.session = aiohttp.ClientSession()
        await super(ScrobblingProtocol, self).setup(**kwargs)

    def playstatus_changed(self):
        """ Called by the playstatus tracker when the play-state changes """
        # handle apps in app_handlers and any idle states
        if self.curr_state.app not in self.app_handlers and not self.curr_state.is_idle():
            # if we're currently scrobbling, we should handle any pause states
            if self.curr_state.is_playing() or not self.currently_scrobbling:
                task = self.print_debug(f"Ignoring Untracked {self.curr_state}", prefix="SCROBBLER")
                asyncio.get_event_loop().create_task(task)
                return False
            task = self.print_info("Handling untracked pause to stop currently scrobbling")
            asyncio.get_event_loop().create_task(task)

        self.handle_state_change()

    def handle_state_change(self):
        """ Cancels any pending scrobble and creates a new one """
        if self.pending_scrobble:
            # tasks sleep for 1 second before handling a state change
            # this is to allow cancelling rapid state changes (like when skipping)
            # also, apps can send multiple state changes in a row where the last one is the one we want
            task = self.print_debug(f"Pending scrobble was replaced {self.prev_state}", prefix="SCROBBLER")
            asyncio.get_event_loop().create_task(task)
            self.pending_scrobble.cancel()
            self.pending_scrobble = None

        self.pending_scrobble = asyncio.create_task(
            self.post_trakt_update()
        )
        # add a done callback to the task so task errors are logged
        self.pending_scrobble.add_done_callback(self._handle_task_result)

    async def post_trakt_update(self, after=1) -> None:
        """ Uses the correct handler for the current app to post the scrobble

        :param after: The time to wait before handling a state change"""
        await asyncio.sleep(after)
        handler = self.app_handlers.get(self.curr_state.app)

        if self.curr_state.is_playing() and handler:
            await self.print_info(f"Start for {self.curr_state}", prefix="SCROBBLER")
            await handler()
        else:
            if self.currently_scrobbling:
                await self.print_info(f"Stop for {self.curr_state}", prefix="SCROBBLER")
                await self.stop_scrobbling()

        self.pending_scrobble = None

    async def handle_tvshows(self) -> None:
        """ Starts scrobbling iTunes shows """
        if self.curr_state.has_tv_info():
            season_number = self.curr_state.season_number
            episode_number = self.curr_state.episode_number
        else:
            info = await self.get_itunes_title(self.curr_state.content_identifier)
            if info is None:
                await self.print("OOPS")
                return
            if type(info) is dict and 'kind' in info:
                movie = {'title': dict(info)['trackName'],
                         'year': parser.parse(dict(info)['releaseDate']).year}
                await self.print(f"Playing iTunes Movie: {movie['title']}")
                await self.start_scrobbling(movie=movie, progress=self.curr_state.progress)
                return

            season_number, episode_number = info
        await self.print(f"Playing iTunes Show: {self.curr_state.get_title()} S{season_number}E{episode_number}")
        await self.start_scrobbling(show={'title': self.curr_state.get_title()},
                                    episode={'season': season_number, 'number': episode_number},
                                    progress=self.curr_state.progress)

    async def get_itunes_title(self, content_identifier) -> (int, int):
        """ Returns the season and episode number of the given content identifier."""
        known = self.itunes_titles.get(content_identifier)
        if known:
            return known['season'], known['episode']

        try:
            async with self.session.get(f'https://itunes.apple.com/lookup?id={content_identifier}') as resp:
                result = await resp.json(content_type=None)
        except JSONDecodeError:
            await self.print_warning("JSON ERROR")
            result = {'resultCount': 0}

        if result.get('resultCount') == 0 or 'errorMessage' in result:
            await self.print('no result')
            result = await self.get_apple_tv_plus_info(self.curr_state.get_title())
            if not result:
                return None
            season, episode = result
        else:
            result = result['results'][0]  # type: ignore
            if result['kind'] == 'feature-movie':
                return result
            match = re.match("^Season (\\d\\d?), Episode (\\d\\d?): ", result['trackName'])
            if match is not None:
                season = int(match.group(1))
                episode = int(match.group(2))
            else:
                season = int(re.match(".*, Season (\\d\\d?)( \\(Uncensored\\))?$", result['collectionName']).group(1))
                episode = int(result['trackNumber'])
        self.itunes_titles[content_identifier] = {'season': season, 'episode': episode}
        return season, episode

    async def get_apple_tv_plus_info(self, title: str) -> (int, int):
        """ Returns the season and episode number of the given title."""
        data = await self.search_by_description("site:tv.apple.com " + title)
        if not data:
            return None

        match = re.search('(https://tv\\.apple\\.com/(../)?episode/.*?)\"', data)
        if not match:
            return None

        async with self.session.get(match.group(1)) as resp:
            data = await resp.read()

        xml = etree.parse(BytesIO(data), etree.HTMLParser())
        for script in xml.xpath('//script'):
            if not script.text:
                continue
            try:
                for d in list(json.loads(script.text).values()):
                    if type(d) is not str:
                        continue
                    try:
                        d = json.loads(d)
                        if 'd' in d and 'data' in d['d'] and 'content' in d['d']['data']:
                            info = d['d']['data']['content']
                            if 'seasonNumber' in info:
                                return info['seasonNumber'], info['episodeNumber']
                    except JSONDecodeError:
                        continue
            except JSONDecodeError:
                continue

        return None

    async def search_by_description(self, query):
        """ Searches for the given query on bing.com and returns the first result."""
        self.now_playing_description = await self.request_now_playing_description()

        query += ' "' + self.now_playing_description + '"'
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_6) AppleWebKit/605.1.15 '
                          '(KHTML, like Gecko) Version/14.0 Safari/605.1.15'
        }
        async with self.session.get(
                'https://www.bing.com/search', params={'q': query}, headers=headers) as response:
            return await response.text()

    async def handle_tv_app(self) -> None:
        await self.handle_tvshows()
        return

    async def handle_movies(self) -> None:
        """ Start scrobbling iTunes movies."""
        movie = {}
        match = re.search('(.*) \\((\\d\\d\\d\\d)\\)', self.curr_state.metadata.title)
        if match is None:
            movie['title'] = self.curr_state.title
        else:
            movie['title'] = match.group(1)
            movie['year'] = match.group(2)
        await self.print(f"Playing iTunes Movie: {movie['title']}")
        await self.start_scrobbling(movie=movie, progress=self.curr_state.progress)

    async def handle_netflix(self) -> None:
        """ Start scrobbling Netflix."""
        if not self.curr_state.title:
            await asyncio.sleep(1)
        match = re.match('^S(\\d\\d?): E(\\d\\d?) (.*)', self.curr_state.title)
        if match is not None:
            key = self.curr_state.title + str(self.curr_state.total_time)
            title = self.netflix_titles.get(key)
            if not title:
                if self.curr_state.content_identifier:
                    title = await self.get_netflix_title(self.curr_state.content_identifier)
                else:
                    title = await self.get_netflix_title_from_description(match.group(1), match.group(3))
                    if not title:
                        await self.print("Error: Netflix title not found")
                        return
                self.netflix_titles[key] = title
            if title:
                await self.print(f"Playing Netflix Show: {title} S{match.group(1)}E{match.group(2)}")
                await self.start_scrobbling(show={'title': title},
                                            episode={'season': match.group(1), 'number': match.group(2)},
                                            progress=self.curr_state.progress)
        else:
            await self.print("Netflix Movie:", self.curr_state.title)
            await self.start_scrobbling(movie={'title': self.curr_state.title}, progress=self.curr_state.progress)

    async def handle_amazon(self) -> None:
        """ Start scrobbling Amazon."""
        amazon_settings = self.settings.get('amazon')
        if amazon_settings and amazon_settings.get('enabled'):
            title, season, episode = await self.get_amazon_details(self.curr_state.content_identifier)
            await self.start_scrobbling(show={'title': title},
                                        episode={'season': season, 'number': episode},
                                        progress=self.curr_state.progress)

    async def get_amazon_details(self, content_identifier) -> Tuple[str, str, str]:
        """ Get the Amazon details for the given content identifier."""
        content_identifier = content_identifier.rsplit(':', 1)[0]
        known = self.amazon_titles.get(content_identifier)
        if known:
            return known['title'], known['season'], known['episode']
        url = self.settings['amazon']['get_playback_resources_url'] % content_identifier
        r = await self.session.request(url=url, method='GET', headers={'Cookie': self.settings['amazon']['cookie']})
        data = await r.json()
        title = None
        season = None
        episode = data['catalogMetadata']['catalog']['episodeNumber']
        for f in data['catalogMetadata']['family']['tvAncestors']:
            if f['catalog']['type'] == 'SEASON':
                season = f['catalog']['seasonNumber']
            elif f['catalog']['type'] == 'SHOW':
                title = f['catalog']['title'].replace("[OV/OmU]", "").replace("[OV]", "").replace("[Ultra HD]", "") \
                    .replace("[dt./OV]", "").replace("(4K UHD)", "").strip()
        self.amazon_titles[content_identifier] = {'title': title, 'season': season, 'episode': episode}
        return title, season, episode

    async def get_netflix_title_from_description(self, season, episode_title) -> Optional[str]:
        """ Searches for the given season and episode title on bing.com and returns the title."""
        data = await self.search_by_description("site:netflix.com Season " + season + " " + episode_title)
        if not data:
            return None

        match = re.search('netflix\\.com/(.+/)?title/(\\d+)', data)
        if not match:
            return None
        content_identifier = match.group(2)
        title = await self.get_netflix_title(content_identifier)
        return title

    async def get_netflix_title(self, content_identifier):
        """ Gets the title from netflix.com of the given content identifier."""
        # data = urlopen('https://www.netflix.com/title/' + content_identifier).read()
        async with self.session.get('https://www.netflix.com/title/' + content_identifier) as r:
            data = await r.read()
        xml = etree.parse(BytesIO(data), etree.HTMLParser())
        info = json.loads(xml.xpath('//script')[0].text)
        return info['name']
