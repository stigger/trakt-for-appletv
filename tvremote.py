import asyncio
import socket
import threading
import time
import paho.mqtt.client as mqtt
from protobuf_gen import ProtocolMessage_pb2, RegisterHIDDeviceMessage_pb2, RegisterHIDDeviceResultMessage_pb2, \
    SendButtonEventMessage_pb2, SendVirtualTouchEventMessage_pb2, SetStateMessage_pb2, CommandInfo_pb2, \
    SendCommandMessage_pb2
from scrobbling import ScrobblingRemoteProtocol
from tvscrobbler import getInfo, launch, load_config
from urllib.request import Request, urlopen
from lxml import etree


class ControllingRemoteProtocol(ScrobblingRemoteProtocol):
    def __init__(self, config) -> None:
        super().__init__(config)
        self.hid_device_id = None
        self.skip_command_supported = False
        self.next_up_with_swipe = False

    def connection_lost(self, exc):
        super().connection_lost(exc)
        launch(self)

    def connection_made(self, transport):
        super().connection_made(transport)

        msg = ProtocolMessage_pb2.ProtocolMessage()
        msg.type = ProtocolMessage_pb2.ProtocolMessage.REGISTER_HID_DEVICE_MESSAGE
        descriptor = msg.Extensions[RegisterHIDDeviceMessage_pb2.registerHIDDeviceMessage].deviceDescriptor
        descriptor.screenSizeWidth = 1000
        descriptor.screenSizeHeight = 1000
        descriptor.absolute = False
        descriptor.integratedDisplay = False
        self.send(msg)

    def message_received(self, msg):
        if msg.type == ProtocolMessage_pb2.ProtocolMessage.REGISTER_HID_DEVICE_RESULT_MESSAGE:
            self.hid_device_id = msg.Extensions[
                RegisterHIDDeviceResultMessage_pb2.registerHIDDeviceResultMessage].deviceIdentifier
        else:
            super().message_received(msg)

            if msg.type == ProtocolMessage_pb2.ProtocolMessage.SET_STATE_MESSAGE:
                self.skip_command_supported = False
                for command in msg.Extensions[SetStateMessage_pb2.setStateMessage].supportedCommands.supportedCommands:
                    if command.command == CommandInfo_pb2.SkipForward:
                        self.skip_command_supported = True
                        break

    def sendButton(self, usagePage, usage):
        msg = ProtocolMessage_pb2.ProtocolMessage()
        msg.type = ProtocolMessage_pb2.ProtocolMessage.SEND_BUTTON_EVENT
        button_event = msg.Extensions[SendButtonEventMessage_pb2.sendButtonEventMessage]
        button_event.usagePage = usagePage
        button_event.usage = usage
        button_event.buttonDown = True
        self.send(msg)
        button_event.buttonDown = False
        self.send(msg)

    def sendLightTouchEvent(self, x, y):
        self.sendTouchEvent(x, y, 1)
        time.sleep(.1)
        self.sendTouchEvent(x, y, 4)

    def sendTouchEvent(self, x, y, phase):
        msg = ProtocolMessage_pb2.ProtocolMessage()
        msg.type = ProtocolMessage_pb2.ProtocolMessage.SEND_VIRTUAL_TOUCH_EVENT_MESSAGE
        touch_event_message = msg.Extensions[SendVirtualTouchEventMessage_pb2.sendVirtualTouchEventMessage]
        touch_event_message.deviceIdentifier = self.hid_device_id
        touch_event_message.event.x = x
        touch_event_message.event.y = y
        # phases: 0; Unknown, 1; Began, 2; Moved, 3; Stationary, 4; Ended, 5; Canceled
        touch_event_message.event.phase = phase
        self.send(msg)

    def swipe(self, x1, y1, x2, y2):
        self.sendTouchEvent(x1, y1, 1)
        deltaX = (x2 - x1) / 15
        deltaY = (y2 - y1) / 15
        for i in range(0, 15):
            time.sleep(0.005)
            x1 += deltaX
            y1 += deltaY
            self.sendTouchEvent(x1, y1, 2)
        self.sendTouchEvent(x1, y1, 4)

    def send_command(self, command, skip_interval=None):
        msg = ProtocolMessage_pb2.ProtocolMessage()
        msg.type = ProtocolMessage_pb2.ProtocolMessage.SEND_COMMAND_MESSAGE
        send_command = msg.Extensions[SendCommandMessage_pb2.sendCommandMessage]
        send_command.command = command
        if skip_interval is not None:
            send_command.options.skipInterval = skip_interval
        self.send(msg)

    def skipBackward(self):
        if self.skip_command_supported:
            self.send_command(CommandInfo_pb2.SkipBackward, 10)

    def skipForward(self):
        if self.skip_command_supported:
            self.send_command(CommandInfo_pb2.SkipForward, 10)

    def prevChapter(self):
        if self.skip_command_supported:
            if self.current_player == 'com.plexapp.plex':
                self.chapterPlex(False)
            else:
                self.send_command(CommandInfo_pb2.PreviousChapter)

    def nextChapter(self):
        if self.skip_command_supported:
            intro_lengths = self.config['intro_lengths']
            title = self.get_title()
            if intro_lengths is not None and title in intro_lengths:
                offset = intro_lengths[title]
                if offset is not None:
                    self.send_command(CommandInfo_pb2.SkipForward, offset)
                    return
            elif self.current_player == 'com.plexapp.plex':
                self.chapterPlex(True)
            else:
                self.send_command(CommandInfo_pb2.NextChapter)

    def chapterPlex(self, forward):
        res = urlopen(
            Request('http://' + socket.inet_ntoa(getInfo().addresses[0]) + ':32500/player/timeline/poll?wait=0',
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
                    self.send_command(CommandInfo_pb2.SkipForward, (end - time) / 1000)
                else:
                    self.send_command(CommandInfo_pb2.SkipBackward, (time - start) / 1000)
                break

    def doUp(self):
        if self.now_playing_metadata is None and not self.next_up_with_swipe:
            self.sendButton(0x1, 0x8c)
        else:
            self.next_up_with_swipe = False
            self.swipe(500, 500, 500, 150)


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
    if b'23eae8c2' == message.payload:    # 0
        action = lambda: tv_protocol.sendLightTouchEvent(500, 500)
    elif b'94f37ee4' == message.payload:  # 1
        action = lambda: tv_protocol.doUp()
    elif b'f61d79de' == message.payload:  # 2
        tv_protocol.next_up_with_swipe = True
        action = lambda: tv_protocol.sendButton(0xc, 0x60)
    elif b'81772f84' == message.payload:  # 3
        action = lambda: tv_protocol.skipBackward()
    elif b'4d91bbbe' == message.payload:  # 4
        action = lambda: tv_protocol.skipForward()
    elif b'c7695f20' == message.payload:  # 5
        action = lambda: tv_protocol.prevChapter()
    elif b'8ac8fa2' == message.payload:   # 6
        action = lambda: tv_protocol.nextChapter()
    elif b'95d2e7e4' == message.payload:  # 7
        pass
    elif b'1353935e' == message.payload:  # 8
        pass
    elif b'cc7e81c8' == message.payload:  # 9
        pass
    else:
        lastCommand = oldCommandTime

    if action is not None:
        loop.call_soon_threadsafe(action)


client = mqtt.Client()
client.connect('192.168.132.243')
client.subscribe("ir_sensor")
client.on_message = command_handler

thread = threading.Thread(target=lambda: client.loop_forever(), daemon=True)
thread.start()

launch(tv_protocol)
