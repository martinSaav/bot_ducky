"""Worker de embeddings para el historial de chat.

Genera embeddings de los mensajes persistidos en PostgreSQL usando la API
compatible con OpenAI (Gemini text-embedding-004) y los guarda en la tabla
chat_embeddings para búsqueda semántica.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

log = logging.getLogger("chat_embeddings")

# Dimensión de text-embedding-004 de Gemini.
EMBEDDING_DIM = 768


class EmbeddingWorker:
    """Genera y persiste embeddings de mensajes de chat en background."""

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
        """Asegura el schema y arranca el worker."""
        await self._ensure_schema()
        self._worker = asyncio.create_task(self._run(), name="embedding-worker")

    def enqueue(self, message_id: int, text: str) -> None:
        """Encola un mensaje para embeber. No bloquea."""
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
        """Busca mensajes semánticamente similares a *query* dentro de una
        ventana temporal.  Devuelve lista de dicts con display_name, text y
        created_at, o lista vacía si no hay suficientes embeddings o la API
        falla.
        """
        query_vec = await self._embed_single(query)
        if query_vec is None:
            return []
        async with self._pool.acquire() as conn:
            # Exige al menos 10 filas embebidas en la ventana antes de usarlas,
            # para no degradar el resumen con muy poco contexto.
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
        # Reordenar cronológicamente para que el LLM reciba el hilo en orden.
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
        """Crea la tabla chat_embeddings con la dimensión correcta si no existe."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS chat_embeddings (
                    id BIGSERIAL PRIMARY KEY,
                    message_id BIGINT NOT NULL
                        REFERENCES chat_messages(id) ON DELETE CASCADE,
                    embedding VECTOR({EMBEDDING_DIM}) NOT NULL,
                    CONSTRAINT chat_embeddings_message_id_key UNIQUE (message_id)
                );
                CREATE INDEX IF NOT EXISTS chat_embeddings_message_id_idx
                    ON chat_embeddings (message_id);
                CREATE INDEX IF NOT EXISTS chat_embeddings_embedding_hnsw_idx
                    ON chat_embeddings USING hnsw (embedding vector_cosine_ops);
                """
            )

    async def _run(self) -> None:
        """Worker principal: consume la cola en lotes y persiste embeddings."""
        while True:
            # Espera el primer item.
            first = await self._queue.get()
            if first is None:
                self._queue.task_done()
                return
            batch: list[tuple[int, str]] = [first]

            # Drena lo que haya sin bloquear hasta alcanzar batch_size.
            while len(batch) < self._batch_size:
                try:
                    item = self._queue.get_nowait()
                    if item is None:
                        # Señal de stop en medio de un batch: procesamos lo que hay
                        # y salimos.
                        await self._process_batch(batch)
                        self._queue.task_done()
                        for _ in batch:
                            self._queue.task_done()
                        return
                    batch.append(item)
                except asyncio.QueueEmpty:
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
        """Llama a /embeddings y devuelve los vectores en el mismo orden."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {"model": self._model, "input": texts}
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
    """Convierte una lista de floats al formato literal de pgvector: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{v:.8g}" for v in vec) + "]"
