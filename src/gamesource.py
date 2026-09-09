"""Arbitro entre las fuentes que dicen que juego esta corriendo.

Hoy hay dos: el agente de Rust en la PC del streamer y la presencia de
Discord. El agente gana porque mira los procesos reales; Discord queda como
respaldo para cuando el agente esta apagado o la PC no lo tiene instalado.

Toda la logica de "que categoria poner" vive aca, en un solo lugar, para que
las dos fuentes no se peleen por el canal.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from .category import CategoryResolver
from .config import cfg
from .twitch.helix import Helix

log = logging.getLogger("gamesource")

Announcer = Callable[[str], Awaitable[None]]

# Numero mas alto gana. El agente ve procesos; Discord ve un estado que la
# streamer puede tener mal configurado o desactualizado.
PRIORITY = {"agent": 20, "discord": 10}


@dataclass
class Report:
    game: str | None
    at: float
    #: Segundos tras los cuales la fuente se considera muerta. None = nunca.
    stale_after: float | None

    @property
    def stale(self) -> bool:
        return self.stale_after is not None and (time.monotonic() - self.at) > self.stale_after


class GameArbiter:
    def __init__(
        self,
        helix: Helix,
        resolver: CategoryResolver,
        broadcaster_id: str,
        announce: Announcer | None = None,
    ):
        self.helix = helix
        self.resolver = resolver
        self.broadcaster_id = broadcaster_id
        self.announce = announce
        self.enabled = cfg.autocat_enabled

        self._reports: dict[str, Report] = {}
        self._pending: asyncio.Task[None] | None = None
        self._last_applied: str | None = None

    # ------------------------------------------------------------------
    def sources(self) -> dict[str, Report]:
        """Estado actual de cada fuente. Solo para diagnostico y comandos."""
        return dict(self._reports)

    def note_manual_change(self, game_name: str) -> None:
        """Un !categoria a mano pisa lo ultimo que aplicamos automaticamente."""
        self._last_applied = game_name

    async def report(
        self, source: str, game: str | None, *, stale_after: float | None = None
    ) -> None:
        """Una fuente informa que juego ve. Siempre es el estado absoluto."""
        previous = self._reports.get(source)
        self._reports[source] = Report(game=game, at=time.monotonic(), stale_after=stale_after)

        if previous is not None and previous.game == game and not previous.stale:
            return  # latido sin novedad: no reprogramamos nada

        log.debug("Fuente '%s' reporta %r", source, game)
        self._schedule(self._debounce_for(source))

    # ------------------------------------------------------------------
    def _debounce_for(self, source: str) -> int:
        # El agente ve el proceso, no hace falta esperar tanto como con
        # Discord, donde un alt-tab al launcher cambia el estado.
        return cfg.agent_debounce if source == "agent" else cfg.presence_debounce

    def _winner(self) -> tuple[str | None, str | None]:
        """(fuente, juego) de la fuente viva de mayor prioridad."""
        live = [(s, r) for s, r in self._reports.items() if not r.stale]
        if not live:
            return None, None
        source, report = max(live, key=lambda item: PRIORITY.get(item[0], 0))
        return source, report.game

    def _schedule(self, delay: int) -> None:
        if self._pending and not self._pending.done():
            self._pending.cancel()
        self._pending = asyncio.create_task(self._apply_later(delay))

    async def _apply_later(self, delay: int) -> None:
        try:
            await asyncio.sleep(delay)
            await self.apply()
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 - nunca debe matar la task
            log.exception("Fallo aplicando la categoria")

    # ------------------------------------------------------------------
    async def apply(self) -> None:
        source, game_name = self._winner()

        if not self.enabled:
            log.info("Auto-categorizador apagado; ignoro %r", game_name)
            return

        if cfg.autocat_only_when_live:
            stream = await self.helix.get_stream(cfg.twitch_channel)
            if not stream:
                log.info("Canal offline y AUTOCAT_ONLY_WHEN_LIVE=true; no toco la categoria")
                return

        target = game_name or cfg.autocat_idle_category
        if not target:
            return
        if game_name and self.resolver.is_ignored(game_name):
            log.debug("%r esta en la lista de ignorados", game_name)
            return

        game = await self.resolver.resolve(target)
        if not game:
            log.info("No encontre categoria de Twitch para %r; dejo el canal como esta", target)
            return
        if game["name"] == self._last_applied:
            return

        changed = await self.resolver.apply(self.broadcaster_id, game)
        self._last_applied = game["name"]
        log.info("Categoria %s (fuente: %s)", game["name"], source or "?")

        if changed and self.announce and cfg.autocat_announce:
            await self.announce(f"Categoria actualizada automaticamente a: {game['name']}")
