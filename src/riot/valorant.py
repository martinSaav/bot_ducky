"""Valorant via HenrikDev (wrapper comunitario).

Riot no da acceso publico a la API de Valorant, asi que se usa el estandar
de facto de la comunidad: https://docs.henrikdev.xyz
La key se pide en su Discord y va en el header Authorization.

Las respuestas se parsean de forma defensiva y con fallback de version,
porque es una API de terceros que cambia sin previo aviso.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from ..config import cfg
from ..util import TTLCache, split_riot_id

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


class ValorantClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
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
