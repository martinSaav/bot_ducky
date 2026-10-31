"""Embeddings worker for the chat history.

Generates embeddings for the messages stored in PostgreSQL using the
OpenAI-compatible API (Gemini text-embedding-004) and saves them in the
chat_embeddings table for semantic search.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

log = logging.getLogger("chat_embeddings")



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

    async def search(
        self,
        query: str,
        seconds: int,
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        """Finds messages semantically similar to *query* within a time window.
        Returns a list of dicts with display_name, text and created_at, or an
        empty list if there are not enough embeddings or the API fails.
        """
        query_vec = await self._embed_single(query)
        if query_vec is None:
            return []
        async with self._pool.acquire() as conn:
            # Requires at least 10 embedded rows in the window before using them,
            # so the summary is not degraded by too little context.
            count = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM chat_messages cm
                JOIN chat_embeddings ce ON ce.message_id = cm.id
                WHERE cm.created_at >= NOW() - ($1 * INTERVAL '1 second')
                """,
                seconds,
            )
            if count < 10:
                return []
            rows = await conn.fetch(
                """
                SELECT cm.author_login, cm.content,
                       EXTRACT(EPOCH FROM cm.created_at) AS created_at
                FROM chat_messages cm
                JOIN chat_embeddings ce ON ce.message_id = cm.id
                WHERE cm.created_at >= NOW() - ($1 * INTERVAL '1 second')
                ORDER BY ce.embedding <=> $2::vector
                LIMIT $3
                """,
                seconds,
                query_vec,
                limit,
            )
        # Re-sort chronologically so the LLM gets the thread in order.
        result = [
            {
                "display_name": row["author_login"],
                "text": row["content"],
                "created_at": float(row["created_at"]),
            }
            for row in rows
        ]
        result.sort(key=lambda m: m["created_at"])
        return result

    async def stop(self) -> None:
        if self._worker is not None:
            await self._queue.put(None)
            await self._worker
            self._worker = None

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    async def _ensure_schema(self) -> None:
        """Creates the chat_embeddings table with the right dimension if it does not exist."""
        async with self._pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'chat_embeddings')"
            )
            if exists:
                return

            vec = await self._embed_single("test")
            if not vec:
                raise RuntimeError("Could not get a test embedding to compute the dimension.")
            dim = len(vec)

            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS chat_embeddings (
                    id BIGSERIAL PRIMARY KEY,
                    message_id BIGINT NOT NULL
                        REFERENCES chat_messages(id) ON DELETE CASCADE,
                    embedding VECTOR({dim}) NOT NULL,
                    CONSTRAINT chat_embeddings_message_id_key UNIQUE (message_id)
                );
                CREATE INDEX IF NOT EXISTS chat_embeddings_message_id_idx
                    ON chat_embeddings (message_id);
                CREATE INDEX IF NOT EXISTS chat_embeddings_embedding_hnsw_idx
                    ON chat_embeddings USING hnsw (embedding vector_cosine_ops);
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
        payload: dict[str, Any] = {"model": self._model, "input": texts, "dimensions": 768}
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
                    # Gemini devuelve [{index, embedding}, …]
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


def _vec_to_pg(vec: list[float]) -> str:
    """Converts a list of floats to pgvector's literal format: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{v:.8g}" for v in vec) + "]"
