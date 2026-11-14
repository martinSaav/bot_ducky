"""Embeddings for the chat memory behind !pregunta (questions about past streams).

Two things are embedded with the OpenAI-compatible API (Gemini):
  * chat messages that carry content (should_embed), in the background;
  * each stream summary, one row per stream.

A single stream (~150 messages) fits in the LLM prompt, so summaries do not
use retrieval: it is only for questions that span every stream.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import aiohttp

log = logging.getLogger("chat_embeddings")

#: Vector size requested from the API; both tables are created with it.
DIMENSIONS = 768

#: Messages with fewer words carry no searchable content ("jajaja", "gg wp").
MIN_WORDS = 4


def should_embed(text: str, min_words: int = MIN_WORDS) -> bool:
    """Whether a chat message is worth indexing: no commands, no short reactions."""
    text = text.strip()
    return not text.startswith("!") and len(text.split()) >= min_words


class EmbeddingWorker:
    """Generates and stores chat message embeddings in the background."""

    def __init__(
        self,
        pool: Any,  # asyncpg.Pool
        session: aiohttp.ClientSession,
        api_key: str,
        model: str,
        base_url: str,
        batch_size: int = 20,
        queue_size: int = 5_000,
    ) -> None:
        self._pool = pool
        self._session = session
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._batch_size = batch_size
        self._queue: asyncio.Queue[tuple[int, str] | None] = asyncio.Queue(
            maxsize=queue_size
        )
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Ensures the schema exists and starts the worker."""
        await self._ensure_schema()
        self._worker = asyncio.create_task(self._run(), name="embedding-worker")

    def enqueue(self, message_id: int, text: str) -> None:
        """Queues a message for embedding. Non-blocking."""
        try:
            self._queue.put_nowait((message_id, text))
        except asyncio.QueueFull:
            log.warning("Embeddings queue full; dropping message id=%d", message_id)

    async def embed_session(self, stream_id: str, summary: str) -> None:
        """Stores the embedding of a stream summary (one row per stream).

        Never raises: a summary that cannot be embedded only means that stream
        is harder to find with !pregunta.
        """
        vec = await self._embed_single(summary)
        if vec is None:
            log.warning("Could not embed the summary of stream %s", stream_id)
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO session_embeddings (stream_id, embedding)
                    VALUES ($1, $2::vector)
                    ON CONFLICT (stream_id) DO UPDATE SET embedding = EXCLUDED.embedding
                    """,
                    stream_id,
                    _vec_to_pg(vec),
                )
        except Exception:  # noqa: BLE001 - see docstring
            log.exception("Could not store the summary embedding of stream %s", stream_id)

    async def backfill_sessions(self) -> int:
        """Embeds the stored summaries that have no embedding yet. Returns how many."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT s.twitch_stream_id, s.summary
                FROM stream_sessions s
                LEFT JOIN session_embeddings e ON e.stream_id = s.twitch_stream_id
                WHERE s.summary IS NOT NULL AND e.stream_id IS NULL
                """
            )
        for row in rows:
            await self.embed_session(row["twitch_stream_id"], row["summary"])
        return len(rows)

    async def prune_noise(self, min_words: int = MIN_WORDS) -> int:
        """Deletes the embeddings of messages should_embed() now skips.

        Until November 2026 every message was embedded, commands and
        one-word reactions included. Returns how many rows were deleted.
        """
        async with self._pool.acquire() as conn:
            status = await conn.execute(
                r"""
                DELETE FROM chat_embeddings ce
                USING chat_messages cm
                WHERE cm.id = ce.message_id
                  AND (cm.content LIKE '!%'
                       OR array_length(regexp_split_to_array(trim(cm.content), '\s+'), 1) < $1)
                """,
                min_words,
            )
        try:
            return int(str(status).rsplit(" ", 1)[-1])
        except ValueError:
            return 0

    async def search_history(
        self, question: str, *, sessions: int = 3, messages: int = 25
    ) -> dict[str, list[dict[str, str]]]:
        """Stream summaries and chat messages closest to the question, across
        every stream, with their (local) dates."""
        empty: dict[str, list[dict[str, str]]] = {"sessions": [], "messages": []}
        vec = await self._embed_single(question)
        if vec is None:
            return empty
        literal = _vec_to_pg(vec)
        async with self._pool.acquire() as conn:
            session_rows = await conn.fetch(
                """
                SELECT s.started_at, s.game_name, s.summary
                FROM session_embeddings e
                JOIN stream_sessions s ON s.twitch_stream_id = e.stream_id
                ORDER BY e.embedding <=> $1::vector
                LIMIT $2
                """,
                literal,
                sessions,
            )
            message_rows = await conn.fetch(
                """
                SELECT cm.created_at, cm.author_login, cm.content
                FROM chat_embeddings ce
                JOIN chat_messages cm ON cm.id = ce.message_id
                ORDER BY ce.embedding <=> $1::vector
                LIMIT $2
                """,
                literal,
                messages,
            )
        found_messages = [
            {"date": _local_date(row["created_at"]), "author": row["author_login"], "text": row["content"]}
            for row in sorted(message_rows, key=lambda row: row["created_at"])
        ]
        return {
            "sessions": [
                {"date": _local_date(row["started_at"]), "game": row["game_name"] or "", "summary": row["summary"]}
                for row in session_rows
            ],
            # Chronological, so the LLM reads the thread in order.
            "messages": found_messages,
        }

    async def stop(self) -> None:
        if self._worker is not None:
            await self._queue.put(None)
            await self._worker
            self._worker = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _ensure_schema(self) -> None:
        """Creates the embeddings tables if they do not exist."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS chat_embeddings (
                    id BIGSERIAL PRIMARY KEY,
                    message_id BIGINT NOT NULL
                        REFERENCES chat_messages(id) ON DELETE CASCADE,
                    embedding VECTOR({DIMENSIONS}) NOT NULL,
                    CONSTRAINT chat_embeddings_message_id_key UNIQUE (message_id)
                );
                CREATE INDEX IF NOT EXISTS chat_embeddings_message_id_idx
                    ON chat_embeddings (message_id);
                CREATE INDEX IF NOT EXISTS chat_embeddings_embedding_hnsw_idx
                    ON chat_embeddings USING hnsw (embedding vector_cosine_ops);
                -- One row per stream: few rows, no index needed.
                CREATE TABLE IF NOT EXISTS session_embeddings (
                    stream_id VARCHAR(255) PRIMARY KEY
                        REFERENCES stream_sessions(twitch_stream_id) ON DELETE CASCADE,
                    embedding VECTOR({DIMENSIONS}) NOT NULL
                );
                """
            )

    async def _run(self) -> None:
        """Main worker: consumes the queue in batches and stores the embeddings."""
        while True:
            # Waits indefinitely for the first item.
            first = await self._queue.get()
            if first is None:
                self._queue.task_done()
                return
            batch: list[tuple[int, str]] = [first]

            # Build the batch, waiting up to 5 seconds to collect more messages
            # before hitting the API.
            start_time = asyncio.get_running_loop().time()
            timeout = 5.0

            while len(batch) < self._batch_size:
                elapsed = asyncio.get_running_loop().time() - start_time
                if elapsed >= timeout:
                    break
                try:
                    item = await asyncio.wait_for(
                        self._queue.get(), timeout=timeout - elapsed
                    )
                    if item is None:
                        # Stop signal in the middle of a batch
                        await self._process_batch(batch)
                        self._queue.task_done()
                        for _ in batch:
                            self._queue.task_done()
                        return
                    batch.append(item)
                except asyncio.TimeoutError:
                    break

            try:
                await self._process_batch(batch)
            except Exception:  # noqa: BLE001
                log.exception("Error processing embeddings batch (%d items)", len(batch))
            finally:
                for _ in batch:
                    self._queue.task_done()

    async def _process_batch(self, batch: list[tuple[int, str]]) -> None:
        ids = [item[0] for item in batch]
        texts = [item[1] for item in batch]
        vectors = await self._embed_batch(texts)
        if vectors is None:
            return
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO chat_embeddings (message_id, embedding)
                VALUES ($1, $2::vector)
                ON CONFLICT (message_id) DO NOTHING
                """,
                [(mid, _vec_to_pg(vec)) for mid, vec in zip(ids, vectors)],
            )
        log.debug("Embeddings saved for %d messages", len(ids))

    async def _embed_batch(self, texts: list[str]) -> list[list[float]] | None:
        """Calls /embeddings and returns the vectors in the same order."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {"model": self._model, "input": texts, "dimensions": DIMENSIONS}
        for attempt in range(4):
            try:
                async with self._session.post(
                    f"{self._base_url}/embeddings",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    body = await resp.json(content_type=None)
                    if resp.status == 429 or resp.status >= 500:
                        log.warning(
                            "Embeddings API returned HTTP %s (attempt %d)",
                            resp.status, attempt + 1,
                        )
                        await asyncio.sleep(2 ** attempt)
                        continue
                    if resp.status >= 400:
                        log.warning(
                            "Embeddings API failed HTTP %s: %s", resp.status, body
                        )
                        return None
                    data = body.get("data") or []
                    # Gemini returns [{index, embedding}, …]
                    sorted_data = sorted(data, key=lambda d: d.get("index", 0))
                    return [item["embedding"] for item in sorted_data]
            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                log.warning("Network error in embeddings (attempt %d): %s", attempt + 1, exc)
                await asyncio.sleep(2 ** attempt)
        log.error("Embeddings API did not respond after retries")
        return None

    async def _embed_single(self, text: str) -> list[float] | None:
        result = await self._embed_batch([text])
        return result[0] if result else None


def _local_date(value: datetime) -> str:
    """Date as the streamer lived it (container TZ), not UTC: a 23:00 stream
    in Argentina is already the next day in UTC."""
    return value.astimezone().date().isoformat()


def _vec_to_pg(vec: list[float]) -> str:
    """Converts a list of floats to pgvector's literal format: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{v:.8g}" for v in vec) + "]"
