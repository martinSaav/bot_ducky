"""OAuth2 de Twitch: intercambio de code, refresh automático y app token."""
from __future__ import annotations

import logging
import time
import urllib.parse
from typing import Any

import aiohttp

from ..config import cfg
from ..storage import JsonStore

log = logging.getLogger("twitch.auth")

AUTH_BASE = "https://id.twitch.tv/oauth2"

# Scopes por rol. El broadcaster autoriza una vez y queda guardado.
SCOPES = {
    "broadcaster": ["channel:manage:broadcast", "clips:edit"],
    "bot": ["chat:read", "chat:edit"],
}

# Margen antes del vencimiento real para renovar sin cortar peticiones.
REFRESH_MARGIN = 300


class TwitchAuthError(RuntimeError):
    pass


def authorize_url(role: str, state: str) -> str:
    """URL a la que el streamer (o la cuenta del bot) entra una sola vez."""
    scopes = SCOPES.get(role)
    if not scopes:
        raise TwitchAuthError(f"Rol desconocido: {role}")
    params = {
        "client_id": cfg.twitch_client_id,
        "redirect_uri": cfg.twitch_redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        # force_verify obliga a elegir cuenta: evita autorizar sin querer
        # con la sesión de Twitch que ya esté abierta en el navegador.
        "force_verify": "true",
    }
    return f"{AUTH_BASE}/authorize?{urllib.parse.urlencode(params)}"


class TwitchAuth:
    """Administra los tokens de usuario (broadcaster / bot) y el app token."""

    def __init__(self, session: aiohttp.ClientSession, store: JsonStore):
        self.session = session
        self.store = store
        self._app_token: str | None = None
        self._app_expires_at: float = 0.0

    # ------------------------------------------------------------------
    # tokens de usuario
    # ------------------------------------------------------------------
    def has(self, role: str) -> bool:
        return bool((self.store.get(role) or {}).get("refresh_token"))

    def info(self, role: str) -> dict[str, Any]:
        return dict(self.store.get(role) or {})

    def user_id(self, role: str) -> str:
        uid = (self.store.get(role) or {}).get("user_id")
        if not uid:
            raise TwitchAuthError(
                f"No hay user_id para '{role}'. Ejecuta: python tools/auth_twitch.py {role}"
            )
        return str(uid)

    async def bearer(self, role: str) -> str:
        """Access token vigente para el rol, refrescando si hace falta."""
        entry = self.store.get(role) or {}
        if not entry.get("access_token"):
            raise TwitchAuthError(
                f"Falta autorizar '{role}'. Ejecuta: python tools/auth_twitch.py {role}"
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
                f"No hay refresh_token para '{role}'. Reautoriza: "
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
                # 400 con "Invalid refresh token" = el streamer revocó el acceso
                # o cambió la contraseña. Hay que volver a pasar por el flujo web.
                raise TwitchAuthError(
                    f"No se pudo refrescar el token de '{role}' ({resp.status}): {body}. "
                    f"Reautoriza con: python tools/auth_twitch.py {role}"
                )
        updated = await self.store.update(
            role,
            {
                "access_token": body["access_token"],
                # Twitch rota el refresh token en cada uso.
                "refresh_token": body.get("refresh_token", refresh_token),
                "expires_at": time.time() + float(body.get("expires_in", 14400)),
                "scopes": body.get("scope", entry.get("scopes", [])),
            },
        )
        log.info("Token de '%s' refrescado", role)
        return updated["access_token"]

    # ------------------------------------------------------------------
    # app access token (client credentials) para endpoints públicos
    # ------------------------------------------------------------------
    async def app_bearer(self) -> str:
        if self._app_token and time.time() < self._app_expires_at - REFRESH_MARGIN:
            return self._app_token
        payload = {
            "client_id": cfg.twitch_client_id,
            "client_secret": cfg.twitch_client_secret,
            "grant_type": "client_credentials",
        }
        async with self.session.post(f"{AUTH_BASE}/token", data=payload) as resp:
            body = await resp.json(content_type=None)
            if resp.status != 200:
                raise TwitchAuthError(f"App token falló ({resp.status}): {body}")
        self._app_token = body["access_token"]
        self._app_expires_at = time.time() + float(body.get("expires_in", 5_000_000))
        return self._app_token


async def exchange_code(session: aiohttp.ClientSession, code: str) -> dict[str, Any]:
    """Canjea el ?code= del redirect por access + refresh token."""
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
            raise TwitchAuthError(f"Canje de code falló ({resp.status}): {body}")
    return body


async def validate(session: aiohttp.ClientSession, access_token: str) -> dict[str, Any]:
    """GET /oauth2/validate — devuelve login, user_id y scopes del token."""
    headers = {"Authorization": f"OAuth {access_token}"}
    async with session.get(f"{AUTH_BASE}/validate", headers=headers) as resp:
        body = await resp.json(content_type=None)
        if resp.status != 200:
            raise TwitchAuthError(f"Token inválido ({resp.status}): {body}")
    return body
