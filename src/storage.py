"""Simple JSON persistence with atomic writes."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any


class JsonStore:
    """Dictionary backed by a JSON file.

    Writes are atomic (tmp + os.replace), so a crash halfway through a save
    cannot leave the tokens file corrupted.
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()
        self._data: dict[str, Any] = self._read()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) or {}
        except (json.JSONDecodeError, OSError):
            # The broken file is not lost: it is moved to .bak for inspection.
            try:
                self.path.replace(self.path.with_suffix(self.path.suffix + ".bak"))
            except OSError:
                pass
            return {}

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        # On Windows the tokens are protected by the user's ACL; on POSIX the
        # permissions are set to 600 because the file is sensitive.
        if os.name == "posix":
            os.chmod(self.path, 0o600)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def all(self) -> dict[str, Any]:
        return dict(self._data)

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            self._data[key] = value
            self._write()

    async def update(self, key: str, patch: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            current = dict(self._data.get(key) or {})
            current.update(patch)
            self._data[key] = current
            self._write()
            return current

    def set_sync(self, key: str, value: Any) -> None:
        """For scripts without an asyncio loop (e.g. tools/auth_twitch.py)."""
        self._data[key] = value
        self._write()
