import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from google.protobuf.json_format import MessageToDict
from pyatv import const
from pyatv.interface import Playing
from pyatv.protocols.mrp.messages import create
from pyatv.protocols.mrp.protobuf import ContentItemMetadata, ProtocolMessage

from atv.tv_protocol import TVProtocol


@dataclass(frozen=True)
class PlaybackState:
    """ A class to represent the current playback state """
    app: Optional[str] = None
    metadata: Optional[ContentItemMetadata] = field(repr=False, default=None, compare=False, hash=False)
    device_state: const.DeviceState = const.DeviceState.Idle,
    title: Optional[str] = None,
    progress: Optional[int] = field(init=False, compare=False, hash=False),
    total_time: Optional[int] = None,
    position: Optional[int] = field(default=0, compare=False, hash=False),
    series_name: Optional[str] = None,
    season_number: Optional[int] = None,
    episode_number: Optional[int] = None,
    content_identifier: Optional[str] = None,
    time: float = field(repr=False, default=0, compare=False, hash=False)

    def __post_init__(self):
        """ Calculate and initialize the progress percentage """
        total_time = self.total_time if type(self.total_time) == int else 1
        position = self.position if type(self.position) == int else 0
        object.__setattr__(self, 'progress', round(position * 100 / total_time, 1))

    def __eq__(self, other):
        return (self.title == other.title and
                self.device_state == other.device_state and
                self.app == other.app and
                self.total_time == other.total_time and
                self.series_name == other.series_name and
                self.season_number == other.season_number and
                self.episode_number == other.episode_number and
                self.content_identifier == other.content_identifier)

    def is_playing(self) -> bool:
        """ Check if the device is playing """
        return self.device_state == const.DeviceState.Playing

    def is_idle(self) -> bool:
        """ Check if the device is idle """
        return self.device_state == const.DeviceState.Idle

    def is_paused(self) -> bool:
        """ Check if the playback state is paused """
        return self.device_state == const.DeviceState.Paused

    def has_valid_metadata(self) -> bool:
        """ Check if the metadata is valid """
        return (self.title or
                self.series_name or
                self.season_number or
                self.episode_number or
                self.device_state == const.DeviceState.Idle)

    def get_title(self) -> str:
        """ Get the title of the playback state """
        return self.series_name or self.title

    def has_tv_info(self) -> bool:
        """ Check if the playback state has TV information """
        return self.season_number is not None and self.episode_number is not None


class PlayStatusTracker(TVProtocol):
    """ Track the current playback state of the Apple TV """
    curr_state: PlaybackState
    prev_state: PlaybackState

    def __init__(self):
        self.curr_state = PlaybackState(position=0, time=0)
        self.prev_state = PlaybackState(position=0, time=0)
        super().__init__()

    def playstatus_update(self, updater, playstatus: Playing):
        """ Update the current playback state if the state is valid """
        new_state = self._make_state(updater, playstatus)
        if new_state.has_valid_metadata():
            self.prev_state = self.curr_state
            self.curr_state = new_state
            self._register_change_notification()
        else:
            task = self.print_debug(f"Not Changing for Invalid State {new_state}", prefix="STATUS")
            asyncio.get_event_loop().create_task(task)

    def _register_change_notification(self):
        """ Register a change notification if the state has changed """
        if self._states_differ() or self._positions_differ():
            self.playstatus_changed()
        else:
            task = self.print_debug(f"Not Registering Redundant Change {self.curr_state}", prefix="STATUS")
            asyncio.get_event_loop().create_task(task)

    def _states_differ(self) -> bool:
        """Compares equality of previous and current playback states ignoring position, time, and metadata properties.
        :return: True if states differ, False otherwise
        """

        return self.prev_state != self.curr_state

    def _positions_differ(self, sec_threshold=9) -> bool:
        """Determines if playback position differs from the time passed if both prev and curr states are playing
        otherwise if playback position difference is more than the sec_threshold
        intended to reduce meaningless playstatus updates some apps send

        :param sec_threshold: amount of seconds required before difference is registered
        :return: True if position differs, False otherwise
        """

        curr_pos = self.curr_state.position or 0
        prev_pos = self.prev_state.position or 0
        pos_diff = abs(curr_pos - prev_pos)
        if self.curr_state.is_playing() and self.prev_state.is_playing():
            time_passed = int(self.curr_state.time - self.prev_state.time)
            return pos_diff - (time_passed + 1) > 0
        return pos_diff - sec_threshold > 0

    def _make_state(self, updater, playstatus: Playing) -> PlaybackState:
        """ Create a playback state from the playstatus """
        return PlaybackState(app=self.atv.metadata.app.identifier if self.atv.metadata.app else None,
                             metadata=updater.psm.playing.metadata,
                             device_state=playstatus.device_state,
                             title=playstatus.title,
                             total_time=playstatus.total_time,
                             position=playstatus.position,
                             series_name=playstatus.series_name,
                             season_number=playstatus.season_number,
                             episode_number=playstatus.episode_number,
                             content_identifier=playstatus.content_identifier,
                             time=time.monotonic())

    def playstatus_changed(self):
        raise NotImplementedError

    async def request_now_playing_description(self) -> Optional[str]:
        """ Request a description of the currently playing media """
        msg = create(ProtocolMessage.PLAYBACK_QUEUE_REQUEST_MESSAGE)
        req = msg.inner()
        req.location = 0
        req.length = 1
        req.includeInfo = True
        response = await self.protocol.send_and_receive(msg)
        state_msg = response.inner()
        state_dict: dict = MessageToDict(state_msg)
        try:
            return state_dict['playbackQueue']['contentItems'][0]['info']
        except KeyError:
            return None

    async def send_receive(self, msg: ProtocolMessage.type):
        msg = create(msg)
        response = await self.protocol.send_and_receive(msg)
        state_dict: dict = MessageToDict(response)
        return state_dict

    async def cleanup(self, **kwargs) -> None:
        """ Cleanup the playback state manager """
        self.curr_state = PlaybackState(position=0, time=0)
        self.prev_state = PlaybackState(position=0, time=0)
        await super().cleanup(**kwargs)

    @staticmethod
    def _handle_task_result(task: asyncio.Task) -> None:
        """ Logs any exceptions that occurred in the task """
        # noinspection PyBroadException
        try:
            task.result()
        except asyncio.CancelledError:
            pass  # Task cancellation should not be logged as an error.
        except Exception:
            logging.exception('Exception raised by task = %r', task)
