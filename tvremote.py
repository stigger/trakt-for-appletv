import asyncio
import threading
import time
import paho.mqtt.client as mqtt
from pyatv.protocols.mrp.messages import create
from pyatv.protocols.mrp.protobuf import ProtocolMessage, SendButtonEventMessage_pb2, CommandInfo_pb2
from scrobbling import ScrobblingRemoteProtocol
from tvscrobbler import getInfo, launch, load_config
from urllib.request import Request, urlopen
from lxml import etree
import struct


class ControllingRemoteProtocol(ScrobblingRemoteProtocol):
    def __init__(self, config) -> None:
        super().__init__(config)
        self.hid_device_id = None
        self.skip_command_supported = False
        self.next_up_with_swipe = False

    async def connect(self, atv):
        await super().connect(atv)

        msg = create(ProtocolMessage.REGISTER_HID_DEVICE_MESSAGE)
        descriptor = msg.inner().deviceDescriptor
        descriptor.screenSizeWidth = 1000
        descriptor.screenSizeHeight = 1000
        descriptor.absolute = False
        descriptor.integratedDisplay = False

        result = await self.protocol.send_and_receive(msg)
        if result.type == ProtocolMessage.REGISTER_HID_DEVICE_RESULT_MESSAGE:
            self.hid_device_id = result.inner().deviceIdentifier

    async def message_received(self, msg, d):
        await super().message_received(msg, d)

        if msg.type == ProtocolMessage.SET_STATE_MESSAGE:
            for command in msg.inner().supportedCommands.supportedCommands:
                if command.command == CommandInfo_pb2.SkipForward:
                    self.skip_command_supported = command.enabled
                    break

    async def sendButton(self, usagePage, usage):
        msg = create(ProtocolMessage.SEND_BUTTON_EVENT)
        button_event = msg.Extensions[SendButtonEventMessage_pb2.sendButtonEventMessage]
        button_event.usagePage = usagePage
        button_event.usage = usage
        button_event.buttonDown = True
        await self.protocol.send(msg)
        button_event.buttonDown = False
        await self.protocol.send(msg)

    async def sendLightTouchEvent(self, x, y):
        await self.sendTouchEvent(x, y, 1)
        await asyncio.sleep(.1)
        await self.sendTouchEvent(x, y, 4)

    async def sendTouchEvent(self, x, y, phase):
        msg = create(ProtocolMessage.SEND_PACKED_VIRTUAL_TOUCH_EVENT_MESSAGE)
        touch_event_message = msg.inner()
        # phases: 0; Unknown, 1; Began, 2; Moved, 3; Stationary, 4; Ended, 5; Canceled
        touch_event_message.data = struct.pack("<5H", int(x), int(y), phase, self.hid_device_id, 0)
        await self.protocol.send(msg)

    async def swipe(self, x1, y1, x2, y2):
        await self.sendTouchEvent(x1, y1, 1)
        deltaX = (x2 - x1) / 15
        deltaY = (y2 - y1) / 15
        for i in range(0, 15):
            await asyncio.sleep(0.005)
            x1 += deltaX
            y1 += deltaY
            await self.sendTouchEvent(x1, y1, 2)
        await self.sendTouchEvent(x1, y1, 4)

    async def send_command(self, command, skip_interval=None):
        msg = create(ProtocolMessage.SEND_COMMAND_MESSAGE)
        send_command = msg.inner()
        send_command.command = command
        if skip_interval is not None:
            send_command.options.skipInterval = skip_interval
        await self.protocol.send(msg)

    async def skipBackward(self):
        if self.skip_command_supported:
            await self.send_command(CommandInfo_pb2.SkipBackward, 10)

    async def skipForward(self):
        if self.skip_command_supported:
            await self.send_command(CommandInfo_pb2.SkipForward, 10)

    async def prevChapter(self):
        if self.skip_command_supported:
            if self.current_player == 'com.plexapp.plex':
                await self.chapterPlex(False)
            else:
                await self.send_command(CommandInfo_pb2.PreviousChapter)

    async def nextChapter(self):
        if self.skip_command_supported:
            intro_lengths = self.config['intro_lengths']
            title = self.get_title()
            if intro_lengths is not None and title in intro_lengths:
                offset = intro_lengths[title]
                if offset is not None:
                    await self.send_command(CommandInfo_pb2.SkipForward, offset)
                    return
            elif self.current_player == 'com.plexapp.plex':
                await self.chapterPlex(True)
            else:
                await self.send_command(CommandInfo_pb2.NextChapter)

    async def chapterPlex(self, forward):
        res = urlopen(
            Request('http://' + getInfo().address.compressed + ':32500/player/timeline/poll?wait=0',
                    headers={'X-Plex-Target-Client-Identifier': self.config['plex_target_client_identifier'],
                             'X-Plex-Device-Name': 'trakt', 'X-Plex-Client-Identifier': 'trakt'}))
        xml = etree.parse(res)
        tl = xml.xpath("Timeline[@type='video']")[0]
        time = int(tl.attrib['time'])
        xml = etree.parse(urlopen(
            'http://' + tl.attrib['address'] + ':' + tl.attrib['port'] + tl.attrib['key'] + '?includeChapters=1'))
        chapters = xml.xpath('Video/Chapter')
        for c in chapters:
            start = int(c.attrib['startTimeOffset'])
            end = int(c.attrib['endTimeOffset'])
            if start < time < end:
                if forward:
                    await self.send_command(CommandInfo_pb2.SkipForward, (end - time) / 1000)
                else:
                    await self.send_command(CommandInfo_pb2.SkipBackward, (time - start) / 1000)
                break

    async def doUp(self):
        if self.now_playing_metadata is None and not self.next_up_with_swipe:
            await self.sendButton(0x1, 0x8c)
        else:
            self.next_up_with_swipe = False
            await self.swipe(500, 850, 500, 150)


tv_protocol = ControllingRemoteProtocol(load_config())
lastCommand = 0
loop = asyncio.get_event_loop()


def command_handler(client, userdata, message):
    global lastCommand
    if time.time() - lastCommand < 0.2:
        return
    oldCommandTime = lastCommand
    lastCommand = time.time()

    action = None
    if b'2070b' == message.payload:    # 0
        action = tv_protocol.sendLightTouchEvent(500, 500)
    elif b'20702' == message.payload:  # 1
        action = tv_protocol.doUp()
    elif b'20703' == message.payload:  # 2
        tv_protocol.next_up_with_swipe = True
        action = tv_protocol.sendButton(0xc, 0x60)
    elif b'20704' == message.payload:  # 3
        action = tv_protocol.skipBackward()
    elif b'20705' == message.payload:  # 4
        action = tv_protocol.skipForward()
    elif b'20706' == message.payload:  # 5
        action = tv_protocol.prevChapter()
    elif b'20707' == message.payload:   # 6
        action = tv_protocol.nextChapter()
    elif b'20708' == message.payload:  # 7
        pass
    elif b'20709' == message.payload:  # 8
        pass
    elif b'2070a' == message.payload:  # 9
        pass
    else:
        lastCommand = oldCommandTime

    if action is not None:
        asyncio.run_coroutine_threadsafe(action, loop)


client = mqtt.Client()
client.connect('192.168.132.243')
client.subscribe("ir_sensor")
client.on_message = command_handler

thread = threading.Thread(target=lambda: client.loop_forever(), daemon=True)
thread.start()


async def _launch():
    global loop
    loop = asyncio.get_event_loop()
    await launch(tv_protocol)

asyncio.run(_launch())
