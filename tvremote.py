import asyncio
import threading
import time
import paho.mqtt.client as mqtt
from protobuf_gen import ProtocolMessage_pb2, RegisterHIDDeviceMessage_pb2, RegisterHIDDeviceResultMessage_pb2, \
    SendButtonEventMessage_pb2, SendVirtualTouchEventMessage_pb2, SetStateMessage_pb2, CommandInfo_pb2, \
    SendCommandMessage_pb2
from scrobbling import ScrobblingRemoteProtocol
from tvscrobbler import launch, load_config


class ControllingRemoteProtocol(ScrobblingRemoteProtocol):
    def __init__(self, config) -> None:
        super().__init__(config)
        self.hid_device_id = None
        self.skip_command_supported = False

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
        time.sleep(0.005)
        button_event.buttonDown = False
        self.send(msg)

    def sendLightTouchEvent(self, x, y):
        self.sendTouchEvent(x, y, 1)
        time.sleep(0.005)
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
            self.send_command(CommandInfo_pb2.PreviousChapter)

    def nextChapter(self):
        if self.skip_command_supported:
            self.send_command(CommandInfo_pb2.NextChapter)


tv_protocol = ControllingRemoteProtocol(load_config())
lastCommand = 0
loop = asyncio.get_event_loop()

def command_handler(client, userdata, message):
    global lastCommand
    if time.time() - lastCommand < 0.2:
        return
    oldCommandTIme = lastCommand
    lastCommand = time.time()

    action = None
    if b'23eae8c2' == message.payload:  # 0
        action = lambda: tv_protocol.sendLightTouchEvent(500, 500)
    elif b'94f37ee4' == message.payload:  # 1
        action = lambda: tv_protocol.swipe(500, 500, 500, 250)
    elif b'f61d79de' == message.payload:  # 2
        action = lambda: tv_protocol.sendButton(0xc, 0x60)
    elif b'81772f84' == message.payload:  # 3
        action = lambda: tv_protocol.skipBackward()
    elif b'4d91bbbe' == message.payload:  # 4
        action = lambda: tv_protocol.skipForward()
    elif b'c7695f20' == message.payload:  # 5
        action = lambda: tv_protocol.prevChapter()
    elif b'8ac8fa2' == message.payload:  # 6
        action = lambda: tv_protocol.nextChapter()
    else:
        lastCommand = oldCommandTIme

    if action is not None:
        loop.call_soon_threadsafe(action)


client = mqtt.Client()
client.connect('192.168.132.243')
client.subscribe("ir_sensor")
client.on_message = command_handler

thread = threading.Thread(target=lambda: client.loop_forever(), daemon=True)
thread.start()

launch(tv_protocol)
