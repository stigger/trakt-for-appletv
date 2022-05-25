# https://github.com/aio-libs/aiohttp/blob/master/aiohttp/web_runner.py
# https://github.com/aio-libs/aiohttp/blob/f5ff95efe278c470a2ff65cabbb5f5f08ba07416/aiohttp/web.py#L437
import asyncio
from typing import (Set, Any)


class GracefulExit(SystemExit):
    code = 1


def cancel_tasks(to_cancel: Set["asyncio.Task[Any]"], loop: asyncio.AbstractEventLoop) -> None:
    if not to_cancel:
        return

    for task in to_cancel:
        task.cancel()

    loop.run_until_complete(asyncio.gather(*to_cancel, return_exceptions=True))

    for task in to_cancel:
        if task.cancelled():
            continue
        if task.exception() is not None:
            loop.call_exception_handler(
                {
                    "message": "unhandled exception during asyncio.run() shutdown",
                    "exception": task.exception(),
                    "task": task,
                }
            )
