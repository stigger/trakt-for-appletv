import asyncio
from zeroconf import ServiceStateChange, ServiceBrowser, Zeroconf
from scrobbling import ScrobblingRemoteProtocol
import yaml
import uuid
from random import randint

info = None


def on_service_state_change(zeroconf, service_type, name, state_change):
    global info
    if state_change is ServiceStateChange.Added:
        zeroconf.remove_all_service_listeners()
        info = zeroconf.get_service_info(service_type, name)
        zeroconf.close()


def load_config():
    config = yaml.load(open('config.yml', 'r'))

    changed = False
    if 'unique_identifier' not in config['device_info']:
        config['device_info']['unique_identifier'] = str(uuid.uuid1())
        changed = True
    if 'device_id' not in config['device_info']:
        config['device_info']['device_id'] = "%02x:%02x:%02x:%02x:%02x:%02x" % (randint(0, 255), randint(0, 255),
                                                                                randint(0, 255), randint(0, 255),
                                                                                randint(0, 255), randint(0, 255))
        changed = True
    if changed:
        yaml.dump(config, open('config.yml', 'w'), default_flow_style=False)
    return config


def launch(tv_protocol):
    sb = ServiceBrowser(Zeroconf(), '_mediaremotetv._tcp.local.', handlers=[on_service_state_change])
    sb.join()

    loop = asyncio.get_event_loop()
    asyncio.async(loop.create_connection(lambda: tv_protocol, info.server, info.port))
    if not loop.is_running():
        loop.run_forever()
        loop.close()


if __name__ == "__main__":
    launch(ScrobblingRemoteProtocol(load_config()))
