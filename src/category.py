"""Resolucion de nombre de juego -> categoria de Twitch, y aplicacion al canal."""
from __future__ import annotations

import difflib
import json
import logging
import re
import unicodedata
from typing import Any

from .config import cfg
from .storage import JsonStore
from .twitch.helix import Helix

log = logging.getLogger("category")

# Debajo de esto no arriesgamos cambiar la categoria del canal.
MATCH_THRESHOLD = 0.82


def _norm(name: str) -> str:
    """Normaliza para comparar: sin acentos, sin puntuacion, minusculas."""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", stripped.lower())


class CategoryResolver:
    def __init__(self, helix: Helix, state: JsonStore):
        self.helix = helix
        self.state = state
        self._map: dict[str, str] = {}
        self._ignore: set[str] = set()
        self.reload_map()

    def reload_map(self) -> None:
        try:
            raw = json.loads(cfg.game_map_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read %s (%s); continuing without manual map", cfg.game_map_file, exc)
            raw = {}
        self._map = {_norm(k): v for k, v in (raw.get("map") or {}).items()}
        self._ignore = {_norm(x) for x in (raw.get("ignore") or [])}
        log.info("game_map: %d aliases, %d ignored", len(self._map), len(self._ignore))

    # ------------------------------------------------------------------
    def is_ignored(self, name: str) -> bool:
        return _norm(name) in self._ignore

    def _cache(self) -> dict[str, Any]:
        return dict(self.state.get("game_cache") or {})

    async def resolve(self, name: str) -> dict[str, str] | None:
        """Devuelve {'id','name'} de la categoria de Twitch, o None si no hay match."""
        name = (name or "").strip()
        if not name:
            return None
        key = _norm(name)
        if key in self._ignore:
            return None

        cache = self._cache()
        cached = cache.get(key)
        if cached is not None:
            # Guardamos False para los nombres que ya sabemos que no resuelven.
            return cached or None

        target = self._map.get(key, name)

        game = await self._lookup(target)
        # Guardamos tambien los fallos: evita repetir la busqueda en cada
        # actualizacion de presencia de un programa que no es un juego.
        await self.state.update("game_cache", {key: game or False})
        if game:
            log.info("Category resolved: %r -> %s (id %s)", name, game["name"], game["id"])
        else:
            log.info("No Twitch category found for %r", name)
        return game

    async def _lookup(self, target: str) -> dict[str, str] | None:
        exact = await self.helix.get_game_by_name(target)
        if exact:
            return {"id": str(exact["id"]), "name": exact["name"]}

        candidates = await self.helix.search_categories(target, limit=10)
        if not candidates:
            return None

        wanted = _norm(target)
        best: tuple[float, dict[str, Any]] | None = None
        for cand in candidates:
            score = difflib.SequenceMatcher(None, wanted, _norm(cand["name"])).ratio()
            if best is None or score > best[0]:
                best = (score, cand)
        if best and best[0] >= MATCH_THRESHOLD:
            return {"id": str(best[1]["id"]), "name": best[1]["name"]}
        if best:
            log.debug("Best candidate for %r was %r (%.2f), below threshold",
                      target, best[1]["name"], best[0])
        return None

    # ------------------------------------------------------------------
    async def apply(self, broadcaster_id: str, game: dict[str, str]) -> bool:
        """Cambia la categoria del canal. Devuelve False si ya estaba puesta."""
        current = await self.helix.get_channel(broadcaster_id)
        if current and str(current.get("game_id")) == str(game["id"]):
            log.debug("Channel is already set to %s", game["name"])
            return False
        if cfg.autocat_dry_run:
            log.info("[DRY-RUN] Would set category to %s", game["name"])
            return True
        await self.helix.modify_channel(broadcaster_id, game_id=game["id"])
        log.info("Channel category updated to %s", game["name"])
        return True
