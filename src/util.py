"""Utilidades chicas compartidas."""
from __future__ import annotations

import time
from typing import Any


class TTLCache:
    """Cache en memoria con vencimiento. Protege las cuotas de las APIs."""

    def __init__(self, ttl: float):
        self.ttl = ttl
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if not item:
            return None
        expires, value = item
        if time.monotonic() > expires:
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> Any:
        self._data[key] = (time.monotonic() + self.ttl, value)
        return value

    def clear(self) -> None:
        self._data.clear()


def fmt_duration(seconds: float) -> str:
    """320 -> '5m 20s'."""
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def split_riot_id(riot_id: str) -> tuple[str, str] | None:
    """'Chaar#LAS' -> ('Chaar', 'LAS')."""
    if "#" not in (riot_id or ""):
        return None
    name, _, tag = riot_id.rpartition("#")
    name, tag = name.strip(), tag.strip()
    if not name or not tag:
        return None
    return name, tag


def winrate(wins: int, losses: int) -> str:
    total = wins + losses
    return f"{(wins / total * 100):.0f}%" if total else "-"
