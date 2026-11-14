"""Google Drive upload with a Service Account and resumable upload.

The resumable upload is not a luxury: clips weigh tens of MB, and a brief
network drop halfway through a simple upload means starting over.
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

# Google requires every chunk (except the last) to be a multiple of 256 KiB.
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
                raise DriveError("google-auth is missing: pip install -r requirements.txt") from exc
            if not cfg.sa_path.exists():
                raise DriveError(f"No service account JSON at {cfg.sa_path}")
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
        # google-auth is synchronous; run it off the event loop.
        return await asyncio.to_thread(self._token_sync)

    async def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self.token()}"}

    # ------------------------------------------------------------------
    async def find(self, name: str) -> dict[str, Any] | None:
        """Looks for a file by name in the destination folder."""
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
        """Uploads a file and returns the created metadata."""
        if not cfg.gdrive_folder_id:
            raise DriveError("GDRIVE_FOLDER_ID is not configured")
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
                        "The service account has no storage quota of its own in Drive. "
                        "The destination folder must be in a Shared Drive, "
                        "with the service account as Content manager. "
                        "See the README, Google Drive section."
                    )
                raise DriveError(f"Could not start the upload ({resp.status}): {body}")
            location = resp.headers.get("Location")
        if not location:
            raise DriveError("Google returned no Location for the upload session")
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
                            # Google confirms up to which byte it received; continue from there.
                            offset = self._resume_offset(resp.headers.get("Range"), end)
                            attempts = 0
                            continue
                        if status in (408, 429, 500, 502, 503, 504):
                            raise aiohttp.ClientError(f"HTTP {status} recuperable")
                        raise DriveError(
                            f"Upload failed ({status}): {await resp.text()}"
                        )
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    attempts += 1
                    if attempts > MAX_RETRIES:
                        raise DriveError(
                            f"Upload of {path.name} aborted after {MAX_RETRIES} retries: {exc}"
                        ) from exc
                    wait = 2 ** attempts
                    log.warning(
                        "Upload interrupted for %s (%s). Retry %d/%d in %ss",
                        path.name, exc, attempts, MAX_RETRIES, wait,
                    )
                    await asyncio.sleep(wait)
                    offset = await self._query_offset(session_uri, size, fallback=offset)
        raise DriveError(f"The upload of {path.name} ended without confirmation from Google")

    @staticmethod
    def _resume_offset(range_header: str | None, end: int) -> int:
        if range_header and "-" in range_header:
            try:
                return int(range_header.split("-")[-1]) + 1
            except ValueError:
                pass
        return end + 1

    async def _query_offset(self, session_uri: str, size: int, *, fallback: int) -> int:
        """Asks Google how many bytes it has before resuming."""
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
