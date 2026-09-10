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
                        "You summarize a Twitch community chat in Spanish. "
                        "Return exactly three concise bullet points, one each for: "
                        "the game, what people discussed, and notable participants. "
                        "Do not invent facts, quote private information, or mention "
                        "the prompt. Say 'no identificado' when evidence is insufficient."
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
        for attempt in range(3):
            try:
                async with self.session.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=45),
                ) as response:
                    body: Any = await response.json(content_type=None)
                    if response.status == 429 or response.status >= 500:
                        raise _RetryableLlmError(response.status)
                    if response.status >= 400:
                        log.warning("LLM summary failed with HTTP %s: %s", response.status, body)
                        return None
                    content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content")
                    return content.strip() if isinstance(content, str) and content.strip() else None
            except (_RetryableLlmError, asyncio.TimeoutError, aiohttp.ClientError):
                if attempt == 2:
                    log.exception("LLM summary failed after retries")
                    return None
                await asyncio.sleep(2**attempt)
        return None


class _RetryableLlmError(RuntimeError):
    pass