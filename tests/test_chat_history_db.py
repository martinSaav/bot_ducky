"""Tests for src/chat_history_db.py without a real database.

The embeddings side (indexing and search) is covered in test_chat_qa.py.

Naming: [MethodName]_[Scenario/State]_[ExpectedResult]
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.chat_history_db import PostgresChatHistory
from tests.conftest import make_message


class TestFlush:

    async def test_flush_WhenDatabaseNeverStarted_DoesNotHang(self):
        # Postgres was down at startup: messages pile up in the queue with no
        # worker. The end-of-stream summary must not get stuck waiting.
        history = PostgresChatHistory("postgresql://unused")
        history.enqueue(make_message())

        await asyncio.wait_for(history.flush(), timeout=1)


class TestSessionSummary:

    async def test_setSessionSummary_UpdatesTheStreamRow(self):
        conn = MagicMock()
        conn.execute = AsyncMock()
        pool = MagicMock()
        pool.acquire.return_value.__aenter__.return_value = conn
        history = PostgresChatHistory("postgresql://unused")
        history._pool = pool

        await history.set_session_summary("s1", "- Juego: VALORANT")

        sql, *params = conn.execute.await_args.args
        assert "UPDATE stream_sessions" in sql
        assert params == ["s1", "- Juego: VALORANT"]
