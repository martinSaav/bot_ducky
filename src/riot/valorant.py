"""Valorant through HenrikDev (community wrapper) and the client's local API.

Riot gives no public access to the Valorant API, so the community's de facto
standard is used: https://docs.henrikdev.xyz
The key is requested on their Discord and goes in the Authorization header.

Responses are parsed defensively and with version fallbacks, because it is a
third-party API that changes without notice.

To know whether a match is active, the client's local presence is read
(/chat/v4/presences) when the bot runs on the same PC as the game. The client
writes a lockfile with the port and password to connect to
https://127.0.0.1:{port}. In normal use the agent does this on the streamer's
PC; this copy is the fallback.

Error messages and rank/match texts are in Spanish on purpose: they are sent
to Twitch chat.
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

# Path of the lockfile Valorant writes while it is open.
_LOCKFILE = Path.home() / "AppData" / "Local" / "Riot Games" / "Riot Client" / "Config" / "lockfile"

# SSL context that accepts the local client's self-signed certificate.
_SSL_CTX = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

log = logging.getLogger("riot.valorant")

BASE = "https://api.henrikdev.xyz/valorant"
PLATFORM = "pc"


class ValorantError(RuntimeError):
    pass


def _dig(data: Any, *path: str, default: Any = None) -> Any:
    """Walks nested dicts without blowing up when a key is missing."""
    cur = data
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


_PHASES = {"MENUS": "menus", "PREGAME": "pregame", "INGAME": "in_game"}


def _first(data: Any, *paths: tuple[str, ...]) -> Any:
    """First non-null value among several possible paths in a dict."""
    for path in paths:
        cur = data
        for key in path:
            cur = cur.get(key) if isinstance(cur, dict) else None
        if cur is not None:
            return cur
    return None


def decode_presence_private(encoded: Any) -> dict[str, Any] | None:
    """Decodes a presence's `private` field (base64-encoded JSON)."""
    if not isinstance(encoded, str) or not encoded:
        return None
    try:
        padded = encoded.replace("-", "+").replace("_", "/") + "=" * (-len(encoded) % 4)
        data = json.loads(base64.b64decode(padded))
    except ValueError:  # binascii.Error, JSONDecodeError and UnicodeDecodeError all are
        return None
    return data if isinstance(data, dict) else None


def parse_presence_phase(private: Any) -> str | None:
    """Match phase in the private presence: menus, pregame or in_game.

    Riot restructured the presence: the old format is flat
    (`sessionLoopState`) and the new one groups fields into objects
    (`matchPresenceData`). Both are tried. An unknown state is returned as is.
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
    """Accesses the Valorant client's local API through its lockfile.

    Needs no extra configuration; it works while the game is open on the same
    machine the bot runs on.
    """

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self._puuid: str | None = None
        self._match_id: str | None = None

    @property
    def configured(self) -> bool:
        """True if the Valorant lockfile exists (client open)."""
        return _LOCKFILE.exists()

    def _read_lockfile(self) -> tuple[int, str] | None:
        """Returns (port, password) from the lockfile, or None if it does not exist."""
        try:
            parts = _LOCKFILE.read_text(encoding="utf-8").strip().split(":")
            # format: name:pid:port:password:protocol
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
        """PUUID of the account signed in to the local client."""
        if self._puuid:
            return self._puuid
        lf = self._read_lockfile()
        if lf is None:
            return None
        port, password = lf
        data = await self._local_get(port, password, "/chat/v1/session")
        # The field is called `puuid`; some older versions used `subject`.
        puuid = (data or {}).get("puuid") or (data or {}).get("subject")
        if puuid:
            self._puuid = puuid
        return puuid

    async def match_phase(self) -> str | None:
        """Match phase according to the local presence, or None if it could not be read."""
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
        """Key of the active match according to the local presence, or None.

        The presence carries no match ID, so a key is created when leaving the
        menus and released when going back, just like the agent does. It used
        to ask localhost for /core-game, where that endpoint does not exist: it
        lives on Riot's remote servers and always returned 404.
        """
        if self._read_lockfile() is None:
            self._puuid = None  # the client closed; clear the cache
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
        # With no reading, or an unknown state, the match is left as is.
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
        """Returns the explicit result of the most recent match."""
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
