"""Pipeline diario de clips: Helix -> yt-dlp -> Google Drive."""
from __future__ import annotations

import asyncio
import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import cfg
from ..drive import DriveError, DriveUploader
from ..services import Services

log = logging.getLogger("clips")

# Windows no admite estos caracteres en nombres de archivo.
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

STATE_KEY = "uploaded_clips"
RETENTION_DAYS = 45


def safe_filename(title: str, limit: int = 70) -> str:
    cleaned = _ILLEGAL.sub("", title or "").strip().rstrip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:limit].strip() or "clip"


class ClipPipeline:
    def __init__(self, svc: Services, drive: DriveUploader):
        self.svc = svc
        self.drive = drive
        self.running = False

    # ------------------------------------------------------------------
    def _uploaded(self) -> dict[str, Any]:
        return dict(self.svc.state.get(STATE_KEY) or {})

    async def _mark(self, clip_id: str, payload: dict[str, Any]) -> None:
        await self.svc.state.update(STATE_KEY, {clip_id: payload})

    async def _prune(self) -> None:
        """Evita que state.json crezca para siempre."""
        cutoff = time.time() - RETENTION_DAYS * 86400
        current = self._uploaded()
        keep = {k: v for k, v in current.items() if float(v.get("at", 0)) >= cutoff}
        if len(keep) != len(current):
            await self.svc.state.set(STATE_KEY, keep)
            log.debug("Purged %d old history entries", len(current) - len(keep))

    # ------------------------------------------------------------------
    async def select_clips(self) -> list[dict[str, Any]]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=cfg.clips_lookback_hours)
        clips = await self.svc.helix.get_clips(
            self.svc.broadcaster_id, started_at=start, ended_at=end, limit=100
        )
        log.info("Twitch returned %d clips from the last %dh", len(clips), cfg.clips_lookback_hours)

        done = self._uploaded()
        fresh = [
            c for c in clips
            if c["id"] not in done and int(c.get("view_count", 0)) >= cfg.clips_min_views
        ]
        fresh.sort(key=lambda c: int(c.get("view_count", 0)), reverse=True)
        return fresh[: cfg.clips_max]

    # ------------------------------------------------------------------
    async def download(self, clip: dict[str, Any]) -> Path | None:
        """Descarga el clip con yt-dlp. Twitch solo expone la URL, no el mp4."""
        created = (clip.get("created_at") or "")[:10] or datetime.now().strftime("%Y-%m-%d")
        views = int(clip.get("view_count", 0))
        stem = f"{created}_{views:04d}v_{safe_filename(clip.get('title', ''))}_{clip['id']}"
        target = cfg.download_dir / f"{stem}.mp4"
        if target.exists():
            return target

        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--no-playlist", "--no-progress", "--no-warnings",
            "--retries", "5", "--fragment-retries", "5",
            "-f", "best[ext=mp4]/best",
            "-o", str(target),
            clip["url"],
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0 or not target.exists():
            log.error(
                "yt-dlp failed on %s (code %s): %s",
                clip["id"], proc.returncode, (out or b"").decode(errors="replace")[-500:],
            )
            return None
        return target

    # ------------------------------------------------------------------
    async def run_once(self) -> dict[str, int]:
        """Una corrida completa. Devuelve el resumen para loguear/anunciar."""
        if self.running:
            log.info("A run is already in progress, skipping")
            return {"skipped": 1}
        self.running = True
        summary = {"encontrados": 0, "subidos": 0, "fallidos": 0}
        try:
            if not self.drive.configured:
                log.error(
                    "Google Drive not configured (GDRIVE_FOLDER_ID / %s). Canceling run.",
                    cfg.sa_path,
                )
                return summary

            clips = await self.select_clips()
            summary["encontrados"] = len(clips)
            if not clips:
                log.info("No new clips to upload")
                return summary

            for clip in clips:
                clip_id = clip["id"]
                title = clip.get("title", "")
                log.info(
                    "Processing %s (%s views) - %s",
                    clip_id, clip.get("view_count"), title,
                )
                path = await self.download(clip)
                if path is None:
                    summary["fallidos"] += 1
                    continue
                try:
                    description = (
                        f"{title}\n"
                        f"Clipeado por: {clip.get('creator_name', '?')}\n"
                        f"Vistas: {clip.get('view_count')}\n"
                        f"Fecha: {clip.get('created_at')}\n"
                        f"{clip.get('url')}"
                    )
                    uploaded = await self.drive.upload(path, description=description)
                    await self._mark(
                        clip_id,
                        {
                            "at": time.time(),
                            "title": title,
                            "views": clip.get("view_count"),
                            "drive_id": uploaded.get("id"),
                            "file": path.name,
                        },
                    )
                    summary["subidos"] += 1
                except DriveError as exc:
                    log.error("Failed to upload %s: %s", clip_id, exc)
                    summary["fallidos"] += 1
                    # Un error de configuracion se repite con todos los clips.
                    if "Unidad compartida" in str(exc) or "GDRIVE_FOLDER_ID" in str(exc):
                        break
                    continue
                finally:
                    path.unlink(missing_ok=True)

            await self._prune()
            log.info(
                "Run completed: %d found, %d uploaded, %d failed",
                summary["encontrados"], summary["subidos"], summary["fallidos"],
            )
            return summary
        finally:
            self.running = False

    # ------------------------------------------------------------------
    def _seconds_until_run(self) -> float:
        try:
            hour, minute = (int(x) for x in cfg.clips_run_at.split(":", 1))
        except ValueError:
            log.warning("Invalid CLIPS_RUN_AT (%r), defaulting to 05:00", cfg.clips_run_at)
            hour, minute = 5, 0
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    async def scheduler(self) -> None:
        """Loop diario. Corre a la hora local de CLIPS_RUN_AT."""
        while True:
            wait = self._seconds_until_run()
            log.info(
                "Next clips run in %.1f h (%s)",
                wait / 3600, cfg.clips_run_at,
            )
            await asyncio.sleep(wait)
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("The entire clips run failed")
            # Nos corremos un minuto para no re-disparar en el mismo minuto.
            await asyncio.sleep(60)
