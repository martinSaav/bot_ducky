"""Twitch OAuth2: code exchange, automatic refresh and app token."""
from __future__ import annotations

import asyncio
import logging
import time
import urllib.parse
from typing import Any

import aiohttp

from ..config import cfg
from ..storage import JsonStore

log = logging.getLogger("twitch.auth")

AUTH_BASE = "https://id.twitch.tv/oauth2"

# Scopes per role. The broadcaster authorizes once and it is stored.
SCOPES = {
    "broadcaster": [
        "channel:manage:broadcast",
        "channel:manage:predictions",
        "clips:edit",
    ],
    "bot": ["chat:read", "chat:edit"],
}

# Margin before the real expiry, to renew without interrupting requests.
REFRESH_MARGIN = 300


class TwitchAuthError(RuntimeError):
    pass


def authorize_url(role: str, state: str) -> str:
    """URL the streamer (or the bot account) opens just once."""
    scopes = SCOPES.get(role)
    if not scopes:
        raise TwitchAuthError(f"Unknown role: {role}")
    params = {
        "client_id": cfg.twitch_client_id,
        "redirect_uri": cfg.twitch_redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        # force_verify forces choosing an account: avoids authorizing by
        # accident with whatever Twitch session is already open in the browser.
        "force_verify": "true",
    }
    return f"{AUTH_BASE}/authorize?{urllib.parse.urlencode(params)}"


class TwitchAuth:
    """Manages the user tokens (broadcaster / bot) and the app token."""

    def __init__(self, session: aiohttp.ClientSession, store: JsonStore):
        self.session = session
        self.store = store
        self._app_token: str | None = None
        self._app_expires_at: float = 0.0

    # ------------------------------------------------------------------
    # user tokens
    # ------------------------------------------------------------------
    def has(self, role: str) -> bool:
        return bool((self.store.get(role) or {}).get("refresh_token"))

    def info(self, role: str) -> dict[str, Any]:
        return dict(self.store.get(role) or {})

    def user_id(self, role: str) -> str:
        uid = (self.store.get(role) or {}).get("user_id")
        if not uid:
            raise TwitchAuthError(
                f"No user_id for '{role}'. Run: python tools/auth_twitch.py {role}"
            )
        return str(uid)

    async def bearer(self, role: str) -> str:
        """Valid access token for the role, refreshing it if needed."""
        entry = self.store.get(role) or {}
        if not entry.get("access_token"):
            raise TwitchAuthError(
                f"'{role}' is not authorized yet. Run: python tools/auth_twitch.py {role}"
            )
        if time.time() >= float(entry.get("expires_at", 0)) - REFRESH_MARGIN:
            await self.refresh(role)
            entry = self.store.get(role) or {}
        return entry["access_token"]

    async def refresh(self, role: str) -> str:
        entry = self.store.get(role) or {}
        refresh_token = entry.get("refresh_token")
        if not refresh_token:
            raise TwitchAuthError(
                f"No refresh_token for '{role}'. Authorize again: "
                f"python tools/auth_twitch.py {role}"
            )
        payload = {
            "client_id": cfg.twitch_client_id,
            "client_secret": cfg.twitch_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        async with self.session.post(f"{AUTH_BASE}/token", data=payload) as resp:
            body = await resp.json(content_type=None)
            if resp.status != 200:
                # 400 with "Invalid refresh token" = the streamer revoked access
                # or changed the password. The web flow has to be done again.
                raise TwitchAuthError(
                    f"Could not refresh the token for '{role}' ({resp.status}): {body}. "
                    f"Authorize again with: python tools/auth_twitch.py {role}"
                )
        updated = await self.store.update(
            role,
            {
                "access_token": body["access_token"],
                # Twitch rotates the refresh token on every use.
                "refresh_token": body.get("refresh_token", refresh_token),
                "expires_at": time.time() + float(body.get("expires_in", 14400)),
                "scopes": body.get("scope", entry.get("scopes", [])),
            },
        )
        log.info("Token for '%s' refreshed", role)
        return updated["access_token"]

    # ------------------------------------------------------------------
    # app access token (client credentials) for public endpoints
    # ------------------------------------------------------------------
    async def app_bearer(self) -> str:
        if self._app_token and time.time() < self._app_expires_at - REFRESH_MARGIN:
            return self._app_token
        payload = {
            "client_id": cfg.twitch_client_id,
            "client_secret": cfg.twitch_client_secret,
            "grant_type": "client_credentials",
        }
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                async with self.session.post(f"{AUTH_BASE}/token", data=payload) as resp:
                    body = await resp.json(content_type=None)
                    if resp.status != 200:
                        raise TwitchAuthError(f"App token failed ({resp.status}): {body}")
                self._app_token = body["access_token"]
                self._app_expires_at = time.time() + float(body.get("expires_in", 5_000_000))
                return self._app_token
            except aiohttp.ClientConnectionError as exc:
                last_exc = exc
                wait = 2 ** attempt
                log.warning("app_bearer: network error (%s), retrying in %ss (attempt %d/3)", exc, wait, attempt + 1)
                await asyncio.sleep(wait)
        raise TwitchAuthError(f"app_bearer failed after 3 attempts: {last_exc}")


async def exchange_code(session: aiohttp.ClientSession, code: str) -> dict[str, Any]:
    """Exchanges the redirect's ?code= for an access + refresh token."""
    payload = {
        "client_id": cfg.twitch_client_id,
        "client_secret": cfg.twitch_client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": cfg.twitch_redirect_uri,
    }
    async with session.post(f"{AUTH_BASE}/token", data=payload) as resp:
        body = await resp.json(content_type=None)
        if resp.status != 200:
            raise TwitchAuthError(f"Code exchange failed ({resp.status}): {body}")
    return body


async def validate(session: aiohttp.ClientSession, access_token: str) -> dict[str, Any]:
    """GET /oauth2/validate — returns the token's login, user_id and scopes."""
    headers = {"Authorization": f"OAuth {access_token}"}
    async with session.get(f"{AUTH_BASE}/validate", headers=headers) as resp:
        body = await resp.json(content_type=None)
        if resp.status != 200:
            raise TwitchAuthError(f"Invalid token ({resp.status}): {body}")
    return body
