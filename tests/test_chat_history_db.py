"""Tests for src/chat_history_db.py and src/chat_embeddings.py without a real database.

Naming: [MethodName]_[Scenario/State]_[ExpectedResult]
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.chat_embeddings import EmbeddingWorker
from src.chat_history_db import PostgresChatHistory
from tests.conftest import make_message


class TestFlush:

    async def test_flush_WhenDatabaseNeverStarted_DoesNotHang(self):
        # Postgres was down at startup: messages pile up in the queue with no
        # worker. The end-of-stream summary must not get stuck waiting.
        history = PostgresChatHistory("postgresql://unused")
        history.enqueue(make_message())

        await asyncio.wait_for(history.flush(), timeout=1)


class TestEmbeddingSearch:

    async def test_search_WhenQueryEmbedded_PassesVectorAsPgvectorLiteral(self):
        conn = MagicMock()
        conn.fetchval = AsyncMock(return_value=50)
        conn.fetch = AsyncMock(return_value=[])
        pool = MagicMock()
        pool.acquire.return_value.__aenter__.return_value = conn

        worker = EmbeddingWorker(pool, MagicMock(), "key", "model", "http://llm")
        worker._embed_single = AsyncMock(return_value=[0.5, 0.25])

        await worker.search("que paso", 3600)

        # asyncpg has no codec for list -> vector: it must get the text literal.
        assert conn.fetch.await_args.args[2] == "[0.5,0.25]"


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
