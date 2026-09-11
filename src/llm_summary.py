"""OpenAI-compatible LLM client for concise chat summaries."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .chat_history import HistoryEntry

log = logging.getLogger("llm_summary")


class LlmSummary:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        model: str,
        base_url: str,
        max_messages: int,
    ):
        self.session = session
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_messages = max_messages

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def summarize(
        self, messages: list[HistoryEntry], game: str, window: str
    ) -> str | None:
        if not self.configured or not messages:
            return None
        selected = messages[-self.max_messages :]
        transcript = "\n".join(
            f"{message.display_name}: {message.text}" for message in selected
        )
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Resume un chat comunitario de Twitch en español argentino. "
                        "VALORANT es el nombre de un videojuego de disparos; "
                        "League of Legends es otro videojuego. Si el juego detectado "
                        "es VALORANT, escríbelo exactamente como VALORANT y no lo trates "
                        "como un tema de conversación. Devuelve exactamente tres líneas, "
                        "cada una empezando con '- Juego:', '- De qué se habló:' y "
                        "'- Participantes destacados:'. No uses Markdown adicional, "
                        "asteriscos, encabezados ni bloques de código. No inventes datos, "
                        "no cites información privada y usa 'no identificado' cuando no "
                        "haya evidencia suficiente."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Window: {window}\nGame detected by the bot: {game}\n"
                        f"Messages ({len(selected)} of {len(messages)}):\n{transcript}"
                    ),
                },
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        for attempt in range(5):
            try:
                async with self.session.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=45),
                ) as response:
                    body: Any = await response.json(content_type=None)
                    if response.status == 429 or response.status >= 500:
                        raise _RetryableLlmError(response.status, body)
                    if response.status >= 400:
                        log.warning("LLM summary failed with HTTP %s: %s", response.status, body)
                        return None
                    content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content")
                    return content.strip() if isinstance(content, str) and content.strip() else None
            except _RetryableLlmError as exc:
                if attempt == 4:
                    log.warning(
                        "LLM summary failed after retries: HTTP %s: %s",
                        exc.status,
                        exc.body,
                    )
                    return None
                await asyncio.sleep(2**attempt)
            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                if attempt == 4:
                    log.warning("LLM summary failed after retries: %s", exc)
                    return None
                await asyncio.sleep(2**attempt)
        return None


class _RetryableLlmError(RuntimeError):
    def __init__(self, status: int, body: Any):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body