import asyncio
import sys
import os
from typing import cast

import pyatv
import pyatv.core.relayer


class MediaRemoteProtocol(pyatv.interface.DeviceListener):
    def __init__(self, config) -> None:
        super().__init__()
        self.config = config
        self.atv = None
        self.protocol = None

    async def connect(self, atv):
        loop = asyncio.get_event_loop()

        if not os.path.exists('data/pairing.state'):
            pairing = await pyatv.pair(atv, pyatv.Protocol.AirPlay, loop)
            await pairing.begin()

            code = input("Enter code displayed by Apple TV: ")
            pairing.pin(code)

            await pairing.finish()
            if pairing.has_paired:
                with open("data/pairing.state", "w") as f:
                    f.write(pairing.service.credentials)
            else:
                print("Could not pair", file=sys.stderr)
                exit(1)
        else:
            with open("data/pairing.state", "r") as f:
                atv.set_credentials(pyatv.Protocol.AirPlay, f.read())

        self.atv = await pyatv.connect(atv, loop)
        self.atv.listener = self
        self.protocol = cast(pyatv.protocols.mrp_proto.MrpProtocol,
                             cast(pyatv.core.relayer.Relayer, self.atv.remote_control).main_instance.protocol)

        print("ready!")

    def connection_lost(self, exception: Exception) -> None:
        loop = asyncio.get_event_loop()
        asyncio.run_coroutine_threadsafe(pyatv.connect(self.atv, loop), loop)

    def connection_closed(self) -> None:
        pass
