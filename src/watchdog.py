"""Restarts the bot when its event loop hangs.

If the asyncio loop freezes, nothing running inside it can react, and Docker
only restarts a container whose process exits. A thread outside the loop
watches a heartbeat the loop updates every few seconds; when it goes stale,
the process ends and Docker (restart: unless-stopped) starts it again.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Callable

log = logging.getLogger("watchdog")


def _hard_exit() -> None:
    # os._exit and not sys.exit: from a thread, sys.exit would only end the
    # thread, and a frozen loop would never run a clean shutdown anyway.
    logging.shutdown()
    os._exit(1)


class LoopWatchdog:
    def __init__(
        self,
        timeout: float = 60.0,
        exit: Callable[[], None] = _hard_exit,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.timeout = timeout
        self._exit = exit
        self._clock = clock
        self._last = clock()
        self._fired = False

    def beat(self) -> None:
        """Called from the loop: it is alive."""
        self._last = self._clock()

    def check(self) -> bool:
        """True (and ends the process, once) when the loop stopped beating."""
        stalled = self._clock() - self._last
        if stalled <= self.timeout:
            return False
        if not self._fired:
            self._fired = True
            log.critical("Event loop unresponsive for %.0fs; exiting so Docker restarts the bot", stalled)
            self._exit()
        return True

    def start(self, interval: float = 10.0) -> None:
        """Checks from a daemon thread, outside the loop it watches."""
        def run() -> None:
            while not self.check():
                time.sleep(interval)

        threading.Thread(target=run, name="loop-watchdog", daemon=True).start()


async def beat_forever(watchdog: LoopWatchdog, interval: float = 5.0) -> None:
    """The heartbeat: only runs while the loop does."""
    while True:
        watchdog.beat()
        await asyncio.sleep(interval)
