# https://stackoverflow.com/questions/64303607/python-asyncio-how-to-read-stdin-and-write-to-stdout
import asyncio
import sys
from typing import Optional
from helpers.colors import Colors


class AsyncLogger:
    def __init__(self, settings: dict):
        logging = settings['logging'] if 'logging' in settings else {}
        self.level = self.calc_log_level(logging.get('level', 'info'))
        self.log_file = logging.get('file', None)
        self.headless = settings.get('headless', False)
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None

    async def setup(self):
        """ Setup asyncio. """
        try:
            await self.connect_stdin_stdout()
        except ValueError:
            print("Headless Mode Enabled")
            self.headless = True

    async def connect_stdin_stdout(self):
        """ Connect stdin and stdout to asyncio. """
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        w_transport, w_protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout)
        writer = asyncio.StreamWriter(w_transport, w_protocol, reader, loop)
        self.reader = reader
        self.writer = writer

    async def close(self):
        """ Close connection to user. """
        self.writer.close()

    async def readline(self):
        """ Get input from user. """
        response = await self.reader.readline()
        return response.decode('utf-8').strip()

    async def print(self, msg: str, end: str = '\n'):
        """ Print message to user.

        :param msg: Message to print.
        :param end: End character.
        """
        if self.headless:
            print(msg, end=end)
            return
        msg = msg + end
        self.writer.write(msg.encode('utf-8'))
        await self.writer.drain()

    async def input(self,
                    prompt: str = "",
                    timeout_secs: int = sys.maxsize,
                    timeout_msg: str = 'Timeout!') -> str:
        """ Get input from user with timeout.

        :param prompt: Prompt to display to user.
        :param timeout_secs: Timeout in seconds.
        :param timeout_msg: Message to display when timeout occurs.
        :return: Input from user.
        """
        await self.print(prompt, end='')
        if self.headless:
            return ''
        try:
            return await asyncio.wait_for(self.readline(), timeout_secs)
        except asyncio.TimeoutError:
            await self.print(timeout_msg)
            return ''

    async def print_colors(self, message: str, color: str = Colors.WHITE):
        """ Print message with color.

        :param message: Message to print.
        :param color: Color to print message in.
        """
        await self.print(f'{color}{message}{Colors.RESET}')

    async def print_warning(self, message: str, failure: bool = False):
        """ Print warning message to user.

        :param message: Message to print.
        :param failure: If True, print failure message.
        """
        if failure:
            await self.print_colors(f"'ERROR: {message}", Colors.RED)
        else:
            await self.print_colors(f"'WARNING: {message}", Colors.YELLOW)

    async def print_info(self, message: str, success: bool = False, prefix: str = 'INFO'):
        """ Print info message to user.

        :param message: Message to print.
        :param success: If True, print success message.
        :param prefix: Prefix to print before message.
        """
        if self.level > 0:
            color = Colors.GREEN if success else Colors.CYAN
            await self.print_colors(f'{prefix}: {message}', color)

    async def print_debug(self, message: str, prefix: str = 'DEBUG'):
        """ Print debug message to user.

        :param message: Message to print.
        :param prefix: Prefix to print before message.
        """
        if self.level > 1:
            await self.print_colors(f'{prefix}: {message}', Colors.BLUE)

    @staticmethod
    def calc_log_level(level: str) -> int:
        """ Calculate log level.

        :param level: Log level.
        :return: Log level.
        """
        level = level.lower()
        if level == 'off':
            return 0
        elif level == 'info':
            return 1
        elif level == 'debug':
            return 2
        else:
            return 0
