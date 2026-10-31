"""Valorant via HenrikDev (wrapper comunitario) y API local del cliente.

Riot no da acceso publico a la API de Valorant, asi que se usa el estandar
de facto de la comunidad: https://docs.henrikdev.xyz
La key se pide en su Discord y va en el header Authorization.

Las respuestas se parsean de forma defensiva y con fallback de version,
porque es una API de terceros que cambia sin previo aviso.

Ademas, para saber si hay una partida activa, se lee la presencia local del
cliente (/chat/v4/presences), cuando el bot corre en la misma PC que el juego.
El cliente escribe un lockfile con el puerto y la contrasena para conectarse a
https://127.0.0.1:{port}. En el uso normal esto lo hace el agente en la PC de
la streamer; esta copia es el respaldo.
"""
from __future__ import annotations

import base64
import json
import logging
import ssl
import time
from pathlib import Path
from typing import Any

import aiohttp

from ..config import cfg
from ..util import TTLCache, split_riot_id

# Ruta del lockfile que Valorant escribe mientras esta abierto.
_LOCKFILE = Path.home() / "AppData" / "Local" / "Riot Games" / "Riot Client" / "Config" / "lockfile"

# Contexto SSL que acepta el certificado auto-firmado del cliente local.
_SSL_CTX = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

log = logging.getLogger("riot.valorant")

BASE = "https://api.henrikdev.xyz/valorant"
PLATFORM = "pc"


class ValorantError(RuntimeError):
    pass


def _dig(data: Any, *path: str, default: Any = None) -> Any:
    """Navega dicts anidados sin explotar si falta una clave."""
    cur = data
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


_PHASES = {"MENUS": "menus", "PREGAME": "pregame", "INGAME": "in_game"}


def _first(data: Any, *paths: tuple[str, ...]) -> Any:
    """Primer valor no nulo entre varias rutas posibles dentro de un dict."""
    for path in paths:
        cur = data
        for key in path:
            cur = cur.get(key) if isinstance(cur, dict) else None
        if cur is not None:
            return cur
    return None


def decode_presence_private(encoded: Any) -> dict[str, Any] | None:
    """Decodifica el campo `private` de una presencia (base64 con JSON)."""
    if not isinstance(encoded, str) or not encoded:
        return None
    try:
        padded = encoded.replace("-", "+").replace("_", "/") + "=" * (-len(encoded) % 4)
        data = json.loads(base64.b64decode(padded))
    except ValueError:  # binascii.Error, JSONDecodeError y UnicodeDecodeError lo son
        return None
    return data if isinstance(data, dict) else None


def parse_presence_phase(private: Any) -> str | None:
    """Fase de la partida en la presencia privada: menus, pregame o in_game.

    Riot reestructuro la presencia: el formato viejo es plano
    (`sessionLoopState`) y el nuevo agrupa por objeto (`matchPresenceData`).
    Se prueban los dos. Un estado desconocido se devuelve tal cual.
    """
    raw = _first(
        private,
        ("matchPresenceData", "sessionLoopState"),
        ("sessionLoopState",),
        ("partyPresenceData", "partyOwnerSessionLoopState"),
        ("partyOwnerSessionLoopState",),
    )
    if not isinstance(raw, str) or not raw:
        return None
    return _PHASES.get(raw.upper(), raw)


class ValorantLocalClient:
    """Accede a la API local del cliente de Valorant via lockfile.

    No requiere configuracion adicional; funciona mientras el juego esta
    abierto en la misma maquina donde corre el bot.
    """

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self._puuid: str | None = None
        self._match_id: str | None = None

    @property
    def configured(self) -> bool:
        """True si el lockfile de Valorant existe (cliente abierto)."""
        return _LOCKFILE.exists()

    def _read_lockfile(self) -> tuple[int, str] | None:
        """Devuelve (puerto, contrasena) del lockfile, o None si no existe."""
        try:
            parts = _LOCKFILE.read_text(encoding="utf-8").strip().split(":")
            # formato: name:pid:port:password:protocol
            if len(parts) < 5:
                return None
            return int(parts[2]), parts[3]
        except (OSError, ValueError):
            return None

    def _auth_header(self, password: str) -> str:
        token = base64.b64encode(f"riot:{password}".encode()).decode()
        return f"Basic {token}"

    async def _local_get(self, port: int, password: str, path: str) -> Any | None:
        url = f"https://127.0.0.1:{port}{path}"
        headers = {"Authorization": self._auth_header(password)}
        try:
            conn = aiohttp.TCPConnector(ssl=_SSL_CTX)
            async with aiohttp.ClientSession(connector=conn) as s:
                async with s.get(url, headers=headers) as resp:
                    if resp.status == 404:
                        return None
                    if resp.status >= 400:
                        return None
                    return await resp.json(content_type=None)
        except Exception:  # noqa: BLE001
            return None

    async def local_puuid(self) -> str | None:
        """PUUID de la cuenta logueada en el cliente local."""
        if self._puuid:
            return self._puuid
        lf = self._read_lockfile()
        if lf is None:
            return None
        port, password = lf
        data = await self._local_get(port, password, "/chat/v1/session")
        # El campo se llama `puuid`; algunas versiones viejas usaban `subject`.
        puuid = (data or {}).get("puuid") or (data or {}).get("subject")
        if puuid:
            self._puuid = puuid
        return puuid

    async def match_phase(self) -> str | None:
        """Fase de la partida segun la presencia local, o None si no se pudo leer."""
        lf = self._read_lockfile()
        if lf is None:
            return None
        port, password = lf
        puuid = await self.local_puuid()
        if not puuid:
            return None
        data = await self._local_get(port, password, "/chat/v4/presences")
        presences = (data or {}).get("presences") or []
        own = next(
            (
                p for p in presences
                if isinstance(p, dict)
                and p.get("puuid") == puuid
                and p.get("product") == "valorant"
            ),
            None,
        )
        private = decode_presence_private((own or {}).get("private"))
        return parse_presence_phase(private) if private else None

    async def active_match_id(self) -> str | None:
        """Clave de la partida activa segun la presencia local, o None.

        La presencia no trae un ID de partida, asi que se crea una clave al
        salir de los menus y se suelta al volver, igual que hace el agente.
        Antes se pedia /core-game a localhost, donde ese endpoint no existe:
        vive en los servidores remotos de Riot y siempre devolvia 404.
        """
        if self._read_lockfile() is None:
            self._puuid = None  # el cliente se cerro; limpiamos el cache
            self._match_id = None
            return None
        phase = await self.match_phase()
        if phase in ("pregame", "in_game"):
            if self._match_id is None:
                self._match_id = f"val-{time.time_ns() // 1_000_000}"
            return self._match_id
        if phase == "menus":
            self._match_id = None
            return None
        # Sin lectura o con un estado desconocido no se toca la partida.
        return self._match_id


class ValorantClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.local = ValorantLocalClient(session)
        self._rank = TTLCache(120)
        self._match = TTLCache(120)

    @property
    def configured(self) -> bool:
        return bool(cfg.henrik_api_key and cfg.valorant_id)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any | None:
        headers = {"Authorization": cfg.henrik_api_key}
        async with self.session.get(f"{BASE}{path}", headers=headers, params=params) as resp:
            body = await resp.json(content_type=None)
            if resp.status == 404:
                return None
            if resp.status in (401, 403):
                raise ValorantError("La key de HenrikDev es invalida o no tiene permisos.")
            if resp.status == 429:
                raise ValorantError("Rate limit de HenrikDev, proba en un rato.")
            if resp.status >= 400:
                msg = _dig(body, "errors", default=None) or body
                raise ValorantError(f"HenrikDev {path} -> HTTP {resp.status}: {msg}")
            return body

    def _target(self, riot_id: str | None) -> tuple[str, str]:
        target = riot_id or cfg.valorant_id
        parts = split_riot_id(target)
        if not parts:
            raise ValorantError(f"Riot ID invalido: {target!r}. Formato: Nombre#TAG")
        return parts

    # ------------------------------------------------------------------
    async def rank(self, riot_id: str | None = None) -> str:
        target = riot_id or cfg.valorant_id
        cached = self._rank.get(target.lower())
        if cached:
            return cached
        name, tag = self._target(riot_id)
        region = cfg.valorant_region

        body = await self._get(f"/v3/mmr/{region}/{PLATFORM}/{name}/{tag}")
        parsed = self._parse_mmr_v3(body) if body else None
        if parsed is None:
            body = await self._get(f"/v2/mmr/{region}/{name}/{tag}")
            parsed = self._parse_mmr_v2(body) if body else None
        if parsed is None:
            return f"No encontre datos de {target} en Valorant ({region})."

        tier, rr, peak, change = parsed
        text = f"{target} | {tier}"
        if rr is not None:
            text += f" - {rr} RR"
        if change:
            text += f" ({change:+d} en la ultima)"
        if peak:
            text += f" || Peak: {peak}"
        return self._rank.set(target.lower(), text)

    @staticmethod
    def _parse_mmr_v3(body: Any) -> tuple[str, int | None, str | None, int | None] | None:
        data = _dig(body, "data")
        if not isinstance(data, dict):
            return None
        tier = _dig(data, "current", "tier", "name")
        if not tier:
            return None
        return (
            tier,
            _dig(data, "current", "rr"),
            _dig(data, "peak", "tier", "name"),
            _dig(data, "current", "last_change"),
        )

    @staticmethod
    def _parse_mmr_v2(body: Any) -> tuple[str, int | None, str | None, int | None] | None:
        data = _dig(body, "data")
        if not isinstance(data, dict):
            return None
        tier = _dig(data, "current_data", "currenttierpatched")
        if not tier:
            return None
        return (
            tier,
            _dig(data, "current_data", "ranking_in_tier"),
            _dig(data, "highest_rank", "patched_tier"),
            _dig(data, "current_data", "mmr_change_to_last_game"),
        )

    # ------------------------------------------------------------------
    async def last_match(self, riot_id: str | None = None) -> str:
        target = riot_id or cfg.valorant_id
        cached = self._match.get(target.lower())
        if cached:
            return cached
        name, tag = self._target(riot_id)
        region = cfg.valorant_region

        body = await self._get(
            f"/v4/matches/{region}/{PLATFORM}/{name}/{tag}", params={"size": 1}
        )
        text = self._parse_match_v4(body, name, tag) if body else None
        if text is None:
            body = await self._get(f"/v3/matches/{region}/{name}/{tag}", params={"size": 1})
            text = self._parse_match_v3(body, name, tag) if body else None
        if text is None:
            return f"No encontre partidas recientes de {target}."
        return self._match.set(target.lower(), f"{target} | {text}")

    async def last_match_won(self, riot_id: str | None = None) -> bool | None:
        """Devuelve el resultado explicito de la partida mas reciente."""
        name, tag = self._target(riot_id)
        region = cfg.valorant_region
        body = await self._get(
            f"/v4/matches/{region}/{PLATFORM}/{name}/{tag}", params={"size": 1}
        )
        result = self._match_result_v4(body, name, tag) if body else None
        if result is not None:
            return result
        body = await self._get(
            f"/v3/matches/{region}/{name}/{tag}", params={"size": 1}
        )
        return self._match_result_v3(body, name, tag) if body else None

    @staticmethod
    def _match_result_v4(body: Any, name: str, tag: str) -> bool | None:
        matches = _dig(body, "data", default=[])
        if not isinstance(matches, list) or not matches:
            return None
        match = matches[0]
        me = next(
            (p for p in (match.get("players") or [])
             if str(p.get("name", "")).lower() == name.lower()
             and str(p.get("tag", "")).lower() == tag.lower()),
            None,
        )
        if me is None:
            return None
        team_id = me.get("team_id")
        won = next(
            (t.get("won") for t in (match.get("teams") or []) if t.get("team_id") == team_id),
            None,
        )
        return won if isinstance(won, bool) else None

    @staticmethod
    def _match_result_v3(body: Any, name: str, tag: str) -> bool | None:
        matches = _dig(body, "data", default=[])
        if not isinstance(matches, list) or not matches:
            return None
        match = matches[0]
        me = next(
            (p for p in _dig(match, "players", "all_players", default=[])
             if str(p.get("name", "")).lower() == name.lower()
             and str(p.get("tag", "")).lower() == tag.lower()),
            None,
        )
        if me is None:
            return None
        team = str(me.get("team", "")).lower()
        won = _dig(match, "teams", team, "has_won")
        return won if isinstance(won, bool) else None

    @staticmethod
    def _fmt(agent: str, kills: int, deaths: int, assists: int,
             map_name: str, mode: str, result: str) -> str:
        kd = f"{kills / deaths:.2f}" if deaths else f"{kills}.00"
        return (
            f"Ultima partida ({mode} en {map_name}): {result} | "
            f"{agent} {kills}/{deaths}/{assists} (KD {kd})"
        )

    def _parse_match_v4(self, body: Any, name: str, tag: str) -> str | None:
        matches = _dig(body, "data", default=[])
        if not isinstance(matches, list) or not matches:
            return None
        match = matches[0]
        players = match.get("players") or []
        me = next(
            (p for p in players
             if str(p.get("name", "")).lower() == name.lower()
             and str(p.get("tag", "")).lower() == tag.lower()),
            None,
        )
        if me is None:
            return None
        stats = me.get("stats") or {}
        team_id = me.get("team_id")
        won = next(
            (t.get("won") for t in (match.get("teams") or []) if t.get("team_id") == team_id),
            None,
        )
        return self._fmt(
            _dig(me, "agent", "name", default="?"),
            int(stats.get("kills", 0)),
            int(stats.get("deaths", 0)),
            int(stats.get("assists", 0)),
            _dig(match, "metadata", "map", "name", default="?"),
            _dig(match, "metadata", "queue", "name", default="?"),
            "Victoria" if won else ("Derrota" if won is False else "Sin resultado"),
        )

    def _parse_match_v3(self, body: Any, name: str, tag: str) -> str | None:
        matches = _dig(body, "data", default=[])
        if not isinstance(matches, list) or not matches:
            return None
        match = matches[0]
        players = _dig(match, "players", "all_players", default=[])
        me = next(
            (p for p in players
             if str(p.get("name", "")).lower() == name.lower()
             and str(p.get("tag", "")).lower() == tag.lower()),
            None,
        )
        if me is None:
            return None
        stats = me.get("stats") or {}
        team = str(me.get("team", "")).lower()
        teams = match.get("teams") or {}
        won = _dig(teams, team, "has_won")
        return self._fmt(
            me.get("character", "?"),
            int(stats.get("kills", 0)),
            int(stats.get("deaths", 0)),
            int(stats.get("assists", 0)),
            _dig(match, "metadata", "map", default="?"),
            _dig(match, "metadata", "mode", default="?"),
            "Victoria" if won else ("Derrota" if won is False else "Sin resultado"),
        )
