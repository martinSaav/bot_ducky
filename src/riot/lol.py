"""Cliente de la API oficial de Riot para League of Legends.

Endpoints usados:
  Account-V1    -> Riot ID (Nombre#TAG) a PUUID
  League-V4     -> elo, LP, victorias/derrotas
  Spectator-V5  -> partida en curso (campeon, cola, duracion, rivales)
  Data Dragon   -> nombres de campeones (no cuenta contra la cuota)
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from ..config import cfg
from ..util import TTLCache, fmt_duration, split_riot_id, winrate

log = logging.getLogger("riot.lol")

DDRAGON = "https://ddragon.leagueoflegends.com"

QUEUES = {
    400: "Normal Draft",
    420: "Ranked Solo/Duo",
    430: "Normal Blind",
    440: "Ranked Flex",
    450: "ARAM",
    490: "Quickplay",
    700: "Clash",
    720: "ARAM Clash",
    900: "URF",
    1020: "One for All",
    1700: "Arena",
    1900: "URF",
}

TIER_ES = {
    "IRON": "Hierro", "BRONZE": "Bronce", "SILVER": "Plata", "GOLD": "Oro",
    "PLATINUM": "Platino", "EMERALD": "Esmeralda", "DIAMOND": "Diamante",
    "MASTER": "Maestro", "GRANDMASTER": "Gran Maestro", "CHALLENGER": "Retador",
}


class RiotError(RuntimeError):
    pass


class LolClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self._puuid = TTLCache(24 * 3600)
        self._rank = TTLCache(90)
        self._live = TTLCache(30)
        self._champs: dict[int, str] = {}
        self._champs_cache = TTLCache(12 * 3600)

    @property
    def configured(self) -> bool:
        return bool(cfg.riot_api_key and cfg.riot_id)

    # ------------------------------------------------------------------
    async def _get(self, host: str, path: str) -> Any | None:
        """None cuando Riot responde 404 (no existe / no esta en partida)."""
        url = f"https://{host}.api.riotgames.com{path}"
        headers = {"X-Riot-Token": cfg.riot_api_key}
        async with self.session.get(url, headers=headers) as resp:
            if resp.status == 404:
                return None
            body = await resp.json(content_type=None)
            if resp.status == 401 or resp.status == 403:
                raise RiotError(
                    "La API key de Riot es invalida o expiro "
                    "(las keys de desarrollo duran 24 h)."
                )
            if resp.status == 429:
                retry = resp.headers.get("Retry-After", "?")
                raise RiotError(f"Rate limit de Riot, reintenta en {retry}s.")
            if resp.status >= 400:
                raise RiotError(f"Riot {path} -> HTTP {resp.status}: {body}")
            return body

    # ------------------------------------------------------------------
    async def puuid(self, riot_id: str | None = None) -> str:
        riot_id = riot_id or cfg.riot_id
        parts = split_riot_id(riot_id)
        if not parts:
            raise RiotError(f"Riot ID invalido: {riot_id!r}. Formato: Nombre#TAG")
        cached = self._puuid.get(riot_id.lower())
        if cached:
            return cached
        name, tag = parts
        data = await self._get(
            cfg.riot_region, f"/riot/account/v1/accounts/by-riot-id/{name}/{tag}"
        )
        if not data:
            raise RiotError(f"No existe la cuenta {riot_id} en la region {cfg.riot_region}.")
        return self._puuid.set(riot_id.lower(), data["puuid"])

    async def champions(self) -> dict[int, str]:
        if self._champs_cache.get("v") and self._champs:
            return self._champs
        async with self.session.get(f"{DDRAGON}/api/versions.json") as resp:
            versions = await resp.json(content_type=None)
        version = versions[0]
        url = f"{DDRAGON}/cdn/{version}/data/es_MX/champion.json"
        async with self.session.get(url) as resp:
            data = await resp.json(content_type=None)
        self._champs = {int(c["key"]): c["name"] for c in data["data"].values()}
        self._champs_cache.set("v", version)
        log.info("Data Dragon %s: %d campeones", version, len(self._champs))
        return self._champs

    # ------------------------------------------------------------------
    async def rank(self, riot_id: str | None = None) -> str:
        """Texto listo para el chat con el elo del jugador."""
        target = riot_id or cfg.riot_id
        cached = self._rank.get(target.lower())
        if cached:
            return cached

        puuid = await self.puuid(target)
        entries = await self._get(
            cfg.riot_platform, f"/lol/league/v4/entries/by-puuid/{puuid}"
        )
        if entries is None:
            entries = []

        by_queue = {e.get("queueType"): e for e in entries}
        pieces: list[str] = []
        for queue_type, label in (
            ("RANKED_SOLO_5x5", "SoloQ"),
            ("RANKED_FLEX_SR", "Flex"),
        ):
            entry = by_queue.get(queue_type)
            if not entry:
                continue
            tier = TIER_ES.get(entry["tier"], entry["tier"].title())
            division = entry.get("rank", "")
            lp = entry.get("leaguePoints", 0)
            wins, losses = entry.get("wins", 0), entry.get("losses", 0)
            pieces.append(
                f"{label}: {tier} {division} - {lp} LP "
                f"({wins}V/{losses}D, {winrate(wins, losses)} WR)"
            )

        if not pieces:
            text = f"{target} no tiene rango en esta temporada (unranked)."
        else:
            text = f"{target} | " + " || ".join(pieces)
        return self._rank.set(target.lower(), text)

    async def live_game(self, riot_id: str | None = None) -> str:
        """Texto con la partida en curso, o aviso de que no esta jugando."""
        target = riot_id or cfg.riot_id
        cached = self._live.get(target.lower())
        if cached:
            return cached

        game = await self.active_game(target)
        if not game:
            return self._live.set(target.lower(), f"{target} no esta en partida ahora mismo.")

        puuid = await self.puuid(target)

        champs = await self.champions()
        participants = game.get("participants", [])
        me = next((p for p in participants if p.get("puuid") == puuid), None)
        if me is None:
            return self._live.set(target.lower(), f"{target} esta en partida.")

        champ = champs.get(int(me.get("championId", 0)), "?")
        queue = QUEUES.get(int(game.get("gameQueueConfigId", 0)), "Partida personalizada")
        elapsed = fmt_duration(game.get("gameLength", 0))

        rivals = [
            champs.get(int(p.get("championId", 0)), "?")
            for p in participants
            if p.get("teamId") != me.get("teamId")
        ]
        rivals_txt = ", ".join(rivals[:5]) if rivals else "?"

        text = (
            f"{target} esta jugando {champ} en {queue} "
            f"(va {elapsed}). Enemigos: {rivals_txt}"
        )
        return self._live.set(target.lower(), text)

    async def active_game(self, riot_id: str | None = None) -> dict[str, Any] | None:
        """Devuelve los datos de la partida activa, o None si no esta jugando."""
        puuid = await self.puuid(riot_id)
        return await self._get(
            cfg.riot_platform, f"/lol/spectator/v5/active-games/by-summoner/{puuid}"
        )

    async def active_game_id(self, riot_id: str | None = None) -> str | None:
        game = await self.active_game(riot_id)
        game_id = (game or {}).get("gameId")
        return str(game_id) if game_id is not None else None

    async def match_won(self, game_id: str, riot_id: str | None = None) -> bool | None:
        """Devuelve si la cuenta gano el gameId, o None si aun no aparece."""
        puuid = await self.puuid(riot_id)
        ids = await self._get(
            cfg.riot_region,
            f"/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count=10",
        )
        for match_id in ids or []:
            match = await self._get(cfg.riot_region, f"/lol/match/v5/matches/{match_id}")
            info = (match or {}).get("info") or {}
            if str(info.get("gameId")) != str(game_id):
                continue
            participant = next(
                (p for p in info.get("participants", []) if p.get("puuid") == puuid),
                None,
            )
            if participant is None:
                return None
            team_id = participant.get("teamId")
            team = next(
                (team for team in info.get("teams", []) if team.get("teamId") == team_id),
                None,
            )
            won = (team or {}).get("win")
            return won if isinstance(won, bool) else None
        return None
