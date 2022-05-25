import asyncio
import sys
from helpers.graceful_exit import GracefulExit, cancel_tasks
from scrobbling_protocol import ScrobblingProtocol


async def _play_handler():
    """The main task initializes the pyatv library and starts the scrobbling protocol."""
    listener = ScrobblingProtocol()
    try:
        await listener.setup()
    finally:  # pragma: no cover
        if not listener.is_setup:
            await listener.print("\nSetup cancelled, shutting down...")
            await listener.shutdown()
            return 1

    # sleep forever by 1 hour intervals,
    # on Windows before Python 3.8 wake up every 1 second to handle
    # Ctrl+C smoothly
    try:
        if sys.platform == "win32" and sys.version_info < (3, 8):
            delay = 1
        else:
            delay = 3600

        while True:
            await asyncio.sleep(delay)
    finally:
        await listener.print("\nShutdown requested, cleaning up...")
        await listener.cleanup()


def main():
    """Application start here."""
    loop = asyncio.get_event_loop()
    main_task = loop.create_task(
        _play_handler()
    )
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main_task)
    except (GracefulExit, KeyboardInterrupt):  # pragma: no cover
        pass
    finally:
        cancel_tasks({main_task}, loop)
        cancel_tasks(asyncio.all_tasks(loop), loop)
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
        asyncio.set_event_loop(None)
        print("Shutdown complete.")


if __name__ == "__main__":
    sys.exit(main())
