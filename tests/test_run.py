"""Tests for run.py — how the main process decides to shut down.

Naming: [MethodName]_[Scenario/State]_[ExpectedResult]
"""
from __future__ import annotations

import asyncio
import logging

import run


async def _forever() -> None:
    await asyncio.Event().wait()


class TestWaitForExit:

    async def test_waitForExit_WhenTaskFinishesCleanly_KeepsRunningUntilStop(self):
        # db-reconnect returns as soon as Postgres connects: that must not
        # bring the whole bot down.
        async def connects() -> None:
            return None

        stop = asyncio.Event()
        tasks = {
            "db-reconnect": asyncio.create_task(connects()),
            "chat": asyncio.create_task(_forever()),
        }
        waiter = asyncio.create_task(run.wait_for_exit(tasks, stop))
        await asyncio.sleep(0.05)

        assert not waiter.done()

        stop.set()
        await asyncio.wait_for(waiter, timeout=1)
        tasks["chat"].cancel()

    async def test_waitForExit_WhenTaskCrashes_ReturnsAndLogsIt(self, caplog):
        async def crashes() -> None:
            raise RuntimeError("boom")

        stop = asyncio.Event()
        tasks = {
            "chat": asyncio.create_task(crashes()),
            "discord": asyncio.create_task(_forever()),
        }
        with caplog.at_level(logging.ERROR, logger="run"):
            await asyncio.wait_for(run.wait_for_exit(tasks, stop), timeout=1)

        assert "Subsystem 'chat' died" in caplog.text
        tasks["discord"].cancel()

    async def test_waitForExit_WhenStopIsSet_Returns(self):
        stop = asyncio.Event()
        tasks = {"chat": asyncio.create_task(_forever())}
        stop.set()

        await asyncio.wait_for(run.wait_for_exit(tasks, stop), timeout=1)
        tasks["chat"].cancel()
