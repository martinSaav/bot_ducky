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


class TestSummarizeLater:
    """If the LLM fails when the stream ends (Gemini 503 on Nov 1), the
    summary is retried later instead of being lost."""

    async def test_summarizeLater_WhenRetrySucceeds_SavesAndAnnouncesIt(self):
        from unittest.mock import AsyncMock, MagicMock

        llm = MagicMock()
        llm.summarize = AsyncMock(side_effect=[None, "- Juego: VALORANT"])
        database = MagicMock()
        database.set_session_summary = AsyncMock()
        chat = MagicMock()
        chat.say = AsyncMock()
        sleep = AsyncMock()

        summary = await run.summarize_later(
            llm, [], "VALORANT", "3h",
            stream_id="s1", database_history=database, chat=chat,
            delays=(300, 900), sleep=sleep,
        )

        assert summary == "- Juego: VALORANT"
        assert [call.args[0] for call in sleep.await_args_list] == [300, 900]
        database.set_session_summary.assert_awaited_once_with("s1", "- Juego: VALORANT")
        chat.say.assert_awaited_once()

    async def test_summarizeLater_WhenEveryRetryFails_GivesUpWithoutSaving(self):
        from unittest.mock import AsyncMock, MagicMock

        llm = MagicMock()
        llm.summarize = AsyncMock(return_value=None)
        database = MagicMock()
        database.set_session_summary = AsyncMock()

        summary = await run.summarize_later(
            llm, [], "VALORANT", "3h",
            stream_id="s1", database_history=database, chat=None,
            delays=(1, 1, 1), sleep=AsyncMock(),
        )

        assert summary is None
        assert llm.summarize.await_count == 3
        database.set_session_summary.assert_not_called()


class TestMonitorStreamFeedsDashboard:

    async def test_monitorStream_WhenChecked_PassesTheStreamToTheCallback(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from src.chat_history import ChatHistory

        stream = {"id": "1", "title": "ranked", "viewer_count": 42,
                  "started_at": "2026-11-07T04:00:00Z", "game_name": "VALORANT"}
        helix = MagicMock()
        helix.get_stream = AsyncMock(return_value=stream)
        stop = asyncio.Event()
        seen: list = []

        def on_stream(value):
            seen.append(value)
            stop.set()

        with patch("run.cfg") as cfg:
            cfg.presence_check_seconds = 0.01
            await asyncio.wait_for(
                run.monitor_stream(helix, ChatHistory(), None, MagicMock(), MagicMock(), stop,
                                   on_stream=on_stream),
                timeout=1,
            )

        assert seen == [stream]
