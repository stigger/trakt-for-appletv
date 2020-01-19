import asyncio
import pyatv
from scrobbling import ScrobblingRemoteProtocol
import yaml

atv = None


def load_config():
    return yaml.load(open('data/config.yml', 'r'), Loader=yaml.FullLoader)


def getInfo():
    return atv


async def launch(tv_protocol):
    global atv
    loop = asyncio.get_event_loop()

    atv_id = None
    if 'apple_tv_identifier' in tv_protocol.config:
        atv_id = tv_protocol.config['apple_tv_identifier']

    atvs = await pyatv.scan(loop, identifier=atv_id, protocol=pyatv.Protocol.AirPlay)
    atv = next(filter(lambda x: x.device_info.operating_system == pyatv.const.OperatingSystem.TvOS, atvs))

    if atv_id != atv.identifier:
        tv_protocol.config['apple_tv_identifier'] = atv.identifier
        yaml.dump(tv_protocol.config, open('data/config.yml', 'w'), default_flow_style=False)

    await tv_protocol.connect(atv)

    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(launch(ScrobblingRemoteProtocol(load_config())))
