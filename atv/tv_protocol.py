# https://github.com/aio-libs/aiohttp/blob/936e682d1ab6c833b3e5f0cc3596882cb9cb2444/aiohttp/web_runner.py#L274
import asyncio
import os
import signal
from typing import cast, Optional, List

import pyatv
from pyatv.const import FeatureState, FeatureName

from helpers.graceful_exit import GracefulExit
from pyatv.core.relayer import Relayer
from pyatv.interface import PushListener, DeviceListener, AppleTV, Playing
from pyatv.protocols.mrp import MrpProtocol
import yaml

from helpers.async_logger import AsyncLogger


def _raise_graceful_exit() -> None:
    raise GracefulExit()


class TVProtocol(AsyncLogger, PushListener, DeviceListener):
    atv: Optional[AppleTV]

    def __init__(self):
        self.atv = None
        self.device = None
        self.protocol = None
        self._config_file = 'data/config.yml'
        self._pairing_file = 'data/pairing.state'
        self.settings = self._read_settings()
        self.protocol = None
        self.is_setup = False
        super(TVProtocol, self).__init__(self.settings)

    def playstatus_update(self, updater, playstatus: Playing):
        title = playstatus.title if playstatus.series_name is None else playstatus.series_name
        print('{}: {} is {}'.format(self.atv.metadata.app.name, title, playstatus.device_state.name))

    def playstatus_error(self, updater, exception):
        print(exception)
        # Error in exception

    def connection_lost(self, exception: Exception) -> None:
        """ Called when the connection to the Apple TV is lost. """
        loop = asyncio.get_event_loop()
        asyncio.run_coroutine_threadsafe(self._reconnect('Connection lost'), loop)

    def connection_closed(self) -> None:
        """ Called when the connection to the Apple TV is closed. """
        loop = asyncio.get_event_loop()
        asyncio.run_coroutine_threadsafe(self._reconnect('Connection closed'), loop)

    async def _reconnect(self, reason: str) -> None:
        """ Reconnect to the Apple TV. """
        self.atv = None
        await self.print_warning(f'{reason}: reconnecting...', failure=True)
        await self.cleanup(signal_handlers=False)
        await self.setup(signal_handlers=False)

    async def shutdown(self) -> None:
        """ Gracefully shutdown the Apple TV before connection is complete, ignores subclasses."""
        await self.cleanup()

    async def setup(self, signal_handlers=True) -> None:
        """ Add signal handlers to gracefully exit then connect to the Apple TV starting the push updater."""
        await super(TVProtocol, self).setup()
        loop = asyncio.get_event_loop()
        try:
            if signal_handlers:
                loop.add_signal_handler(signal.SIGINT, _raise_graceful_exit)
                loop.add_signal_handler(signal.SIGTERM, _raise_graceful_exit)
        except NotImplementedError:  # pragma: no cover
            # add_signal_handler is not implemented on Windows
            pass
        await self._startup()
        self.is_setup = True

    async def cleanup(self, signal_handlers=True) -> None:
        """ Cleanup the Apple TV connection and remove signal handlers."""
        loop = asyncio.get_event_loop()
        try:
            if signal_handlers:
                loop.remove_signal_handler(signal.SIGINT)
                loop.remove_signal_handler(signal.SIGTERM)
        except NotImplementedError:  # pragma: no cover
            # remove_signal_handler is not implemented on Windows
            pass

        if self.atv is not None:
            self.atv.push_updater.stop()
            self.atv.listener = None
            remaining_tasks = self.atv.close()
            await asyncio.wait_for(asyncio.gather(*remaining_tasks), 10.0)
        self.is_setup = False

    async def _startup(self, delay=None) -> None:
        """ Connect to the Apple TV and start the push updater.

        :param delay: Delay before connecting to the Apple TV.
        """

        if delay:
            await asyncio.sleep(delay)
        await self._connect()
        self.atv.listener = self
        self.atv.push_updater.listener = self
        self.atv.push_updater.start()
        self.protocol = cast(
            MrpProtocol,
            cast(Relayer, self.atv.remote_control).main_instance.protocol
        )
        await self.print('Listening for Apple TV events...')

    async def _connect(self) -> None:
        """ Connect to the Apple TV and store connection information."""
        loop = asyncio.get_event_loop()
        settings_atv = self.settings.get('apple_tv') or {}
        atv_id = settings_atv.get('id')
        devices = await self._scan_for_devices(settings_atv)
        device = await self._choose_device(devices)

        if atv_id != device.identifier:
            self.settings['apple_tv'] = {}
            self.settings['apple_tv']['id'] = device.identifier
            self.settings['apple_tv']['name'] = device.name
            yaml.dump(self.settings, open(self._config_file, 'w'), default_flow_style=False)
            try:
                os.remove(self._pairing_file)
            except FileNotFoundError:
                pass

        await self._pair_device(device)
        await self.print(f"Connecting to {device.address}")
        self.atv = await pyatv.connect(device, loop)
        if not self.atv.features.in_state(FeatureState.Available, FeatureName.PushUpdates):
            await self.print_warning("Push updates are not supported (no protocol supports it)", failure=True)
            _raise_graceful_exit()
        self.device = device

    async def _pair_device(self, device: pyatv.interface.BaseConfig) -> None:
        """ Pair with the Apple TV and store pairing information."""
        loop = asyncio.get_event_loop()
        if not os.path.exists(self._pairing_file):
            pairing = await pyatv.pair(device, pyatv.Protocol.AirPlay, loop)
            await pairing.begin()

            code = await self.input("Enter code displayed by Apple TV: ")
            pairing.pin(code)

            await pairing.finish()
            await pairing.close()
            if pairing.has_paired:
                with open(self._pairing_file, "w") as f:
                    f.write(pairing.service.credentials)
            else:
                await self.print_warning("Pairing failed", failure=True)
                _raise_graceful_exit()
        else:
            with open(self._pairing_file, "r") as f:
                device.set_credentials(pyatv.Protocol.AirPlay, f.read())

    async def _scan_for_devices(self, atv_settings: dict) -> List[pyatv.interface.BaseConfig]:
        """ Scan for Apple TVs and return a list of devices."""
        async def _perform_scan(loop, identifier=None):
            name = atv_settings.get('name') if identifier else "Apple TV's"
            await self.print(f"Discovering {name} on network...")
            scan_result = await pyatv.scan(loop, identifier=identifier, protocol=pyatv.Protocol.AirPlay)
            return list(
                filter(lambda x: x.device_info.operating_system == pyatv.const.OperatingSystem.TvOS, scan_result))

        async def _fetch_devices():
            atv_id = atv_settings.get('id')
            devices = await _perform_scan(asyncio.get_event_loop(), atv_id)
            scan_for_all = True
            if atv_id and not devices:
                await self.print_warning(f"Saved Apple TV with identifier {atv_id} could not be found")
                # ask user if they wish to scan for all devices
                # if no response is given after 10 seconds, repeat scan for saved device
                scan_for_all = await self.prompt_new(30)
                if not scan_for_all:
                    devices = await _perform_scan(asyncio.get_event_loop(), atv_id)
                else:
                    devices = await _perform_scan(asyncio.get_event_loop())
            if not devices:
                message = "Saved Apple TV seems to be offline" if not scan_for_all else "No Apple TVs found on network"
                message += "... retrying press ctrl+c to exit"
                await self.print_warning(message, failure=True)
            return devices

        final_devices = await _fetch_devices()
        while not final_devices:
            final_devices = await _fetch_devices()

        return final_devices

    async def prompt_new(self, timeout: int) -> bool:
        """ Ask the user if they wish to retry or search for a new Apple TV.

        :param timeout: Timeout in seconds
        :return: True if the user wants to search for a new Apple TV, False otherwise
        """

        prompt = f"Retrying in {timeout} seconds, Enter 'n' or 'new' to scan for a new device: "
        timeout_msg = "Timed out, retrying saved Apple TV"

        answer = await self.input(
            prompt=prompt,
            timeout_secs=timeout,
            timeout_msg=timeout_msg
        )
        return answer.lower() == 'n' or answer.lower() == 'new'

    async def _choose_device(self, devices: list) -> pyatv.interface.BaseConfig:
        """ Choose a device from a list of devices."""
        if len(devices) == 1:
            return devices[0]
        await self.print("Found multiple Apple TVs, please choose one:")
        for i, device in enumerate(devices):
            await self.print(f"{i + 1}: {device.name}")
        choice = int(await self.input("Enter number: "))
        return devices[choice - 1]

    def _read_settings(self) -> dict:
        """ Reads the settings from the config file."""
        return yaml.load(open(self._config_file, 'r'), Loader=yaml.FullLoader) or {}
