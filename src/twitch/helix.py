"""Cliente de la API Helix de Twitch."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp

from ..config import cfg
from .auth import TwitchAuth

log = logging.getLogger("twitch.helix")

BASE = "https://api.twitch.tv/helix"


class HelixError(RuntimeError):
    def __init__(self, status: int, body: Any, path: str):
        self.status = status
        self.body = body
        super().__init__(f"{path} -> HTTP {status}: {body}")


def rfc3339(dt: datetime) -> str:
    """Twitch exige RFC3339 en UTC con sufijo Z."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Helix:
    def __init__(self, session: aiohttp.ClientSession, auth: TwitchAuth):
        self.session = session
        self.auth = auth

    async def _headers(self, role: str) -> dict[str, str]:
        token = await self.auth.app_bearer() if role == "app" else await self.auth.bearer(role)
        return {
            "Client-Id": cfg.twitch_client_id,
            "Authorization": f"Bearer {token}",
        }

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        role: str = "app",
        _attempt: int = 0,
    ) -> Any:
        headers = await self._headers(role)
        url = f"{BASE}{path}"
        async with self.session.request(
            method, url, headers=headers, params=params, json=json_body
        ) as resp:
            status = resp.status
            if status == 204:
                return None
            body = await resp.json(content_type=None)

            if status == 401 and _attempt == 0 and role != "app":
                # El token pudo invalidarse antes de su expiracion nominal.
                log.warning("401 en %s, refrescando token de '%s'", path, role)
                await self.auth.refresh(role)
                return await self.request(
                    method, path, params=params, json_body=json_body,
                    role=role, _attempt=_attempt + 1,
                )

            if status == 429 and _attempt < 3:
                wait = 5.0
                reset = resp.headers.get("Ratelimit-Reset")
                if reset:
                    try:
                        wait = max(1.0, float(reset) - time.time())
                    except ValueError:
                        pass
                log.warning("Rate limit en %s, esperando %.1fs", path, wait)
                await asyncio.sleep(min(wait, 60))
                return await self.request(
                    method, path, params=params, json_body=json_body,
                    role=role, _attempt=_attempt + 1,
                )

            if status >= 500 and _attempt < 3:
                wait = 2 ** _attempt
                log.warning("HTTP %s en %s, reintento en %ss", status, path, wait)
                await asyncio.sleep(wait)
                return await self.request(
                    method, path, params=params, json_body=json_body,
                    role=role, _attempt=_attempt + 1,
                )

            if status >= 400:
                raise HelixError(status, body, path)
            return body

    # ------------------------------------------------------------------
    # usuarios / juegos
    # ------------------------------------------------------------------
    async def get_user(self, login: str) -> dict[str, Any] | None:
        body = await self.request("GET", "/users", params={"login": login})
        data = (body or {}).get("data") or []
        return data[0] if data else None

    async def get_game_by_name(self, name: str) -> dict[str, Any] | None:
        """Busca la categoria por nombre exacto."""
        body = await self.request("GET", "/games", params={"name": name})
        data = (body or {}).get("data") or []
        return data[0] if data else None

    async def search_categories(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Busqueda difusa: util cuando el nombre de Discord no calza exacto."""
        body = await self.request(
            "GET", "/search/categories", params={"query": query, "first": limit}
        )
        return (body or {}).get("data") or []

    # ------------------------------------------------------------------
    # canal
    # ------------------------------------------------------------------
    async def get_channel(self, broadcaster_id: str) -> dict[str, Any] | None:
        body = await self.request(
            "GET", "/channels", params={"broadcaster_id": broadcaster_id}
        )
        data = (body or {}).get("data") or []
        return data[0] if data else None

    async def modify_channel(
        self,
        broadcaster_id: str,
        *,
        game_id: str | None = None,
        title: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """PATCH /helix/channels - requiere scope channel:manage:broadcast."""
        payload: dict[str, Any] = {}
        if game_id is not None:
            payload["game_id"] = str(game_id)
        if title is not None:
            payload["title"] = title[:140]
        if tags is not None:
            payload["tags"] = tags[:10]
        if not payload:
            return
        await self.request(
            "PATCH", "/channels",
            params={"broadcaster_id": broadcaster_id},
            json_body=payload,
            role="broadcaster",
        )

    async def get_stream(self, user_login: str) -> dict[str, Any] | None:
        """Devuelve el stream si esta en vivo, None si esta offline."""
        body = await self.request("GET", "/streams", params={"user_login": user_login})
        data = (body or {}).get("data") or []
        return data[0] if data else None

    # ------------------------------------------------------------------
    # clips
    # ------------------------------------------------------------------
    async def get_clips(
        self,
        broadcaster_id: str,
        *,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Pagina /helix/clips hasta juntar `limit` clips del rango pedido."""
        params: dict[str, Any] = {"broadcaster_id": broadcaster_id, "first": 100}
        if started_at:
            params["started_at"] = rfc3339(started_at)
        if ended_at:
            params["ended_at"] = rfc3339(ended_at)

        out: list[dict[str, Any]] = []
        cursor: str | None = None
        while len(out) < limit:
            page_params = dict(params)
            if cursor:
                page_params["after"] = cursor
            body = await self.request("GET", "/clips", params=page_params)
            data = (body or {}).get("data") or []
            out.extend(data)
            cursor = ((body or {}).get("pagination") or {}).get("cursor")
            if not cursor or not data:
                break
        return out[:limit]

    async def create_clip(self, broadcaster_id: str) -> dict[str, Any] | None:
        """POST /helix/clips - requiere clips:edit y el canal en vivo."""
        body = await self.request(
            "POST", "/clips",
            params={"broadcaster_id": broadcaster_id},
            role="broadcaster",
        )
        data = (body or {}).get("data") or []
        return data[0] if data else None
