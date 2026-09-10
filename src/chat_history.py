"""Bounded in-memory history used by the first chat summary MVP."""
from __future__ import annotations

import re
import time
from collections import Counter, deque
from dataclasses import dataclass

from .twitch.chat import ChatMessage


@dataclass(frozen=True)
class HistoryEntry:
    author_id: str
    display_name: str
    text: str
    created_at: float


class ChatHistory:
    """Keeps recent messages for the lifetime of the bot process."""

    _STOPWORDS = {
        "para", "pero", "como", "esta", "esto", "entre", "desde", "solo",
        "tiene", "sobre", "porque", "cuando", "donde", "quien", "que", "una",
        "uno", "unos", "unas", "los", "las", "del", "por", "con", "sin",
        "muy", "más", "mas", "sus", "son", "fue", "era", "hay", "está",
    }

    def __init__(self, max_messages: int = 10_000):
        self._messages: deque[HistoryEntry] = deque(maxlen=max_messages)

    def add(self, message: ChatMessage) -> None:
        text = " ".join(message.text.split())
        if not text:
            return
        self._messages.append(
            HistoryEntry(message.author_id, message.display_name, text, time.time())
        )

    def since(self, seconds: int) -> list[HistoryEntry]:
        cutoff = time.time() - seconds
        return [message for message in self._messages if message.created_at >= cutoff]

    def topic_words(self, messages: list[HistoryEntry], limit: int = 3) -> list[str]:
        words = Counter(
            word.lower()
            for message in messages
            for word in re.findall(r"[a-záéíóúüñ0-9]{4,}", message.text.lower())
            if word.lower() not in self._STOPWORDS
        )
        return [word for word, _ in words.most_common(limit)]

    def participants(self, messages: list[HistoryEntry], limit: int = 5) -> list[tuple[str, int]]:
        counts = Counter(message.display_name for message in messages)
        return counts.most_common(limit)