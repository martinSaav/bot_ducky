"""Subida a Google Drive con Service Account y upload resumible.

El upload resumible no es un lujo: los clips pesan decenas de MB y un
microcorte a mitad de una subida simple obliga a empezar de cero.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import aiohttp

from .config import cfg

log = logging.getLogger("drive")

SCOPES = ["https://www.googleapis.com/auth/drive"]
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
FILES_URL = "https://www.googleapis.com/drive/v3/files"

# Google exige que cada trozo (salvo el ultimo) sea multiplo de 256 KiB.
CHUNK = 8 * 256 * 1024  # 2 MiB
MAX_RETRIES = 5


class DriveError(RuntimeError):
    pass


class DriveUploader:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self._creds: Any = None

    @property
    def configured(self) -> bool:
        return bool(cfg.gdrive_folder_id) and cfg.sa_path.exists()

    def _load_creds(self) -> Any:
        if self._creds is None:
            try:
                from google.oauth2 import service_account
            except ImportError as exc:  # pragma: no cover
                raise DriveError("Falta google-auth: pip install -r requirements.txt") from exc
            if not cfg.sa_path.exists():
                raise DriveError(f"No existe el JSON de service account en {cfg.sa_path}")
            self._creds = service_account.Credentials.from_service_account_file(
                str(cfg.sa_path), scopes=SCOPES
            )
        return self._creds

    def _token_sync(self) -> str:
        from google.auth.transport.requests import Request

        creds = self._load_creds()
        if not creds.valid:
            creds.refresh(Request())
        return creds.token

    async def token(self) -> str:
        # google-auth es sincrono; lo sacamos del event loop.
        return await asyncio.to_thread(self._token_sync)

    async def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self.token()}"}

    # ------------------------------------------------------------------
    async def find(self, name: str) -> dict[str, Any] | None:
        """Busca un archivo por nombre dentro de la carpeta destino."""
        safe = name.replace("\\", "\\\\").replace("'", "\\'")
        params = {
            "q": f"name = '{safe}' and '{cfg.gdrive_folder_id}' in parents and trashed = false",
            "fields": "files(id,name,size)",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "corpora": "allDrives",
        }
        async with self.session.get(FILES_URL, headers=await self._headers(), params=params) as r:
            body = await r.json(content_type=None)
            if r.status >= 400:
                raise DriveError(f"files.list -> HTTP {r.status}: {body}")
        files = body.get("files") or []
        return files[0] if files else None

    async def upload(
        self, path: Path, *, name: str | None = None, description: str = ""
    ) -> dict[str, Any]:
        """Sube un archivo y devuelve los metadatos creados."""
        if not cfg.gdrive_folder_id:
            raise DriveError("GDRIVE_FOLDER_ID no esta configurado")
        name = name or path.name
        size = path.stat().st_size

        session_uri = await self._start_session(name, size, description)
        return await self._upload_chunks(session_uri, path, size)

    async def _start_session(self, name: str, size: int, description: str) -> str:
        headers = await self._headers()
        headers.update(
            {
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(size),
            }
        )
        metadata = {
            "name": name,
            "parents": [cfg.gdrive_folder_id],
            "description": description,
        }
        params = {"uploadType": "resumable", "supportsAllDrives": "true"}
        async with self.session.post(
            UPLOAD_URL, headers=headers, params=params, json=metadata
        ) as resp:
            if resp.status >= 400:
                body = await resp.text()
                if "storageQuotaExceeded" in body:
                    raise DriveError(
                        "La service account no tiene cuota propia en Drive. "
                        "La carpeta destino tiene que estar en una Unidad compartida "
                        "con la service account como Administrador de contenido. "
                        "Ver el README, seccion Google Drive."
                    )
                raise DriveError(f"No se pudo iniciar la subida ({resp.status}): {body}")
            location = resp.headers.get("Location")
        if not location:
            raise DriveError("Google no devolvio la Location de la sesion de subida")
        return location

    async def _upload_chunks(self, session_uri: str, path: Path, size: int) -> dict[str, Any]:
        offset = 0
        attempts = 0
        with path.open("rb") as fh:
            while offset < size:
                fh.seek(offset)
                chunk = fh.read(CHUNK)
                end = offset + len(chunk) - 1
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{end}/{size}",
                }
                try:
                    async with self.session.put(
                        session_uri, headers=headers, data=chunk
                    ) as resp:
                        status = resp.status
                        if status in (200, 201):
                            log.info("Uploaded %s (%.1f MB)", path.name, size / 1e6)
                            return await resp.json(content_type=None)
                        if status == 308:
                            # Google confirma hasta que byte recibio; seguimos desde ahi.
                            offset = self._resume_offset(resp.headers.get("Range"), end)
                            attempts = 0
                            continue
                        if status in (408, 429, 500, 502, 503, 504):
                            raise aiohttp.ClientError(f"HTTP {status} recuperable")
                        raise DriveError(
                            f"Subida fallo ({status}): {await resp.text()}"
                        )
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    attempts += 1
                    if attempts > MAX_RETRIES:
                        raise DriveError(
                            f"Subida de {path.name} abortada tras {MAX_RETRIES} reintentos: {exc}"
                        ) from exc
                    wait = 2 ** attempts
                    log.warning(
                        "Upload interrupted for %s (%s). Retry %d/%d in %ss",
                        path.name, exc, attempts, MAX_RETRIES, wait,
                    )
                    await asyncio.sleep(wait)
                    offset = await self._query_offset(session_uri, size, fallback=offset)
        raise DriveError(f"La subida de {path.name} termino sin confirmacion de Google")

    @staticmethod
    def _resume_offset(range_header: str | None, end: int) -> int:
        if range_header and "-" in range_header:
            try:
                return int(range_header.split("-")[-1]) + 1
            except ValueError:
                pass
        return end + 1

    async def _query_offset(self, session_uri: str, size: int, *, fallback: int) -> int:
        """Pregunta a Google cuantos bytes tiene antes de reanudar."""
        headers = {"Content-Length": "0", "Content-Range": f"bytes */{size}"}
        try:
            async with self.session.put(session_uri, headers=headers) as resp:
                if resp.status in (200, 201):
                    return size
                if resp.status == 308:
                    return self._resume_offset(resp.headers.get("Range"), fallback - 1)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
        return fallback
