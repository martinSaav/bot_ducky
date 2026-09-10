"""Optional PostgreSQL persistence for Twitch chat history."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .twitch.chat import ChatMessage

log = logging.getLogger("chat_history_db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_messages (
    id BIGSERIAL PRIMARY KEY,
    twitch_message_id VARCHAR(255) UNIQUE NOT NULL,
    author_id VARCHAR(255) NOT NULL,
    author_login VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS chat_messages_created_at_idx ON chat_messages (created_at);
CREATE INDEX IF NOT EXISTS chat_messages_author_id_created_at_idx
    ON chat_messages (author_id, created_at);
"""


class PostgresChatHistory:
    """Stores messages asynchronously without blocking the IRC callback."""

    def __init__(
        self,
        database_url: str,
        queue_size: int = 2_000,
        ignored_authors: set[str] | None = None,
    ):
        self.database_url = database_url
        self._queue: asyncio.Queue[ChatMessage | None] = asyncio.Queue(maxsize=queue_size)
        self._ignored_authors = {author.lower() for author in (ignored_authors or set())}
        self._pool = None
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        import asyncpg

        self._pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=4)
        async with self._pool.acquire() as connection:
            await connection.execute(_SCHEMA)
        self._worker = asyncio.create_task(self._run(), name="chat-history-db")

    def enqueue(self, message: ChatMessage) -> None:
        if message.author.lower() in self._ignored_authors:
            return
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            log.warning("Chat history queue full; dropping message %s", message.message_id)

    async def _run(self) -> None:
        while True:
            message = await self._queue.get()
            if message is None:
                self._queue.task_done()
                return
            try:
                await self._insert(message)
            except Exception:  # noqa: BLE001 - persistence must not kill the bot
                log.exception("Could not persist chat message %s", message.message_id)
            finally:
                self._queue.task_done()

    async def _insert(self, message: ChatMessage) -> None:
        if self._pool is None:
            return
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO chat_messages
                    (twitch_message_id, author_id, author_login, content, created_at)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (twitch_message_id) DO NOTHING
                """,
                message.message_id,
                message.author_id,
                message.author,
                message.text,
                datetime.now(timezone.utc),
            )

    async def recent(self, seconds: int, limit: int = 2_000) -> list[dict[str, str]]:
        if self._pool is None:
            return []
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT author_login, content, EXTRACT(EPOCH FROM created_at) AS created_at
                FROM chat_messages
                WHERE created_at >= NOW() - ($1 * INTERVAL '1 second')
                ORDER BY created_at ASC
                LIMIT $2
                """,
                seconds,
                limit,
            )
        return [
            {
                "display_name": row["author_login"],
                "text": row["content"],
                "created_at": float(row["created_at"]),
            }
            for row in rows
        ]

    async def stop(self) -> None:
        if self._worker is not None:
            await self._queue.put(None)
            await self._worker
            self._worker = None
        if self._pool is not None:
            await self._pool.close()
            self._pool = None