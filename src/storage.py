"""Persistencia simple en JSON con escritura atómica."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any


class JsonStore:
    """Diccionario respaldado por un archivo JSON.

    La escritura es atómica (tmp + os.replace) para que un corte a mitad
    de guardado no deje el archivo de tokens corrupto.
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
            # No perdemos el archivo roto: lo movemos a .bak para inspección.
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
        # En Windows los tokens quedan protegidos por ACL de usuario; en
        # POSIX ajustamos permisos a 600 porque el archivo es sensible.
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
        """Para scripts sin loop de asyncio (ej. tools/auth_twitch.py)."""
        self._data[key] = value
        self._write()
