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
from typing import Any, Awaitable, Callable

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
        state: Any | None = None,
        lol: Any | None = None,
        valorant: Any | None = None,
        announce: Announcer | None = None,
    ):
        self.helix = helix
        self.resolver = resolver
        self.broadcaster_id = broadcaster_id
        self.state = state
        self.lol = lol
        self.valorant = valorant
        self.announce = announce
        self.enabled = cfg.autocat_enabled

        self._reports: dict[str, Report] = {}
        self._pending: asyncio.Task[None] | None = None
        self._prediction_result_task: asyncio.Task[None] | None = None
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

        active_prediction = self.state.get("prediction_active") if self.state else None
        previous_game = str((active_prediction or {}).get("game", ""))
        if active_prediction and (
            not game_name or previous_game not in (game_name or "").lower()
        ):
            self._schedule_prediction_result()

        if self.state is not None:
            session = self.state.get("prediction_session")
            normalized_game = (game_name or "").lower()
            if session and (
                "valorant" not in normalized_game
                or session.get("game") != normalized_game
            ):
                await self.state.set("prediction_session", None)

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
            await self._maybe_start_prediction(game_name)
            return

        changed = await self.resolver.apply(self.broadcaster_id, game)
        self._last_applied = game["name"]
        log.info("Categoria %s (fuente: %s)", game["name"], source or "?")

        await self._maybe_start_prediction(game_name)

        if changed and self.announce and cfg.autocat_announce:
            await self.announce(
                cfg.say_as(f"Categoria actualizada a: {game['name']}")
            )

    async def _maybe_start_prediction(self, game_name: str | None) -> None:
        if not cfg.prediction_enabled or not game_name:
            return
        normalized = game_name.lower()
        if "league of legends" not in normalized and "valorant" not in normalized:
            return

        try:
            match_key = await self._prediction_match_key(normalized)
            if match_key is None:
                return
            if self.state is not None and self.state.get("prediction_created_key") == match_key:
                return

            active = await self.helix.get_predictions(self.broadcaster_id)
            if any(prediction.get("status") == "ACTIVE" for prediction in active):
                if self.state is not None:
                    await self.state.set("prediction_created_key", match_key)
                return
            prediction = await self.helix.create_prediction(
                self.broadcaster_id,
                title="¿Gana esta partida?",
                outcomes=["Gana", "Pierde"],
                prediction_window=cfg.prediction_window,
            )
            if self.state is not None:
                outcomes = {
                    str(outcome.get("title", "")).lower(): str(outcome.get("id", ""))
                    for outcome in prediction.get("outcomes", [])
                }
                await self.state.set(
                    "prediction_active",
                    {
                        "match_key": match_key,
                        "prediction_id": str(prediction.get("id", "")),
                        "outcomes": outcomes,
                        "game": normalized,
                    },
                )
                await self.state.set("prediction_created_key", match_key)
            log.info("Prediction creada para %s (id %s)", game_name, prediction.get("id", "?"))
            if self.announce and cfg.autocat_announce:
                await self.announce(
                    cfg.say_as(
                        "Prediction iniciada: ¿Gana esta partida? "
                        "Voten Gana o Pierde."
                    )
                )
        except Exception:  # noqa: BLE001 - una prediction no debe apagar el arbitro
            log.exception("No se pudo crear la prediction para %s", game_name)

    async def _prediction_match_key(self, normalized_game: str) -> str | None:
        if "league of legends" in normalized_game:
            if self.lol is None or not self.lol.configured:
                log.info("No creo prediction de LoL: falta validar la partida activa")
                return None
            game_id = await self.lol.active_game_id()
            if not game_id:
                log.info("No creo prediction de LoL: la cuenta no esta en partida")
                return None
            return f"lol:{game_id}"

        if self.state is None:
            return f"valorant:{normalized_game}"
        session = self.state.get("prediction_session") or {}
        if session.get("game") == normalized_game and session.get("key"):
            return str(session["key"])
        key = f"valorant:{normalized_game}:{time.time_ns()}"
        await self.state.set("prediction_session", {"game": normalized_game, "key": key})
        return key

    def _schedule_prediction_result(self) -> None:
        if self.state is None or not self.state.get("prediction_active"):
            return
        if self._prediction_result_task and not self._prediction_result_task.done():
            return
        self._prediction_result_task = asyncio.create_task(
            self._resolve_prediction_later(), name="prediction-result"
        )

    async def _resolve_prediction_later(self) -> None:
        for attempt in range(6):
            prediction = self.state.get("prediction_active") if self.state else None
            if not prediction:
                return
            try:
                won = await self._prediction_result(prediction)
                if won is not None:
                    outcome = "gana" if won else "pierde"
                    outcome_id = (prediction.get("outcomes") or {}).get(outcome)
                    if not outcome_id:
                        log.error("Prediction sin outcome para %s", outcome)
                        return
                    await self.helix.resolve_prediction(
                        self.broadcaster_id,
                        prediction_id=prediction["prediction_id"],
                        winning_outcome_id=outcome_id,
                    )
                    await self.state.set("prediction_active", None)
                    if self.announce and cfg.autocat_announce:
                        await self.announce(
                            cfg.say_as("Partida ganada." if won else "Partida perdida.")
                        )
                    log.info("Prediction resuelta: %s", "ganada" if won else "perdida")
                    return
            except Exception:  # noqa: BLE001 - el resultado se puede reintentar
                log.exception("No se pudo consultar el resultado de la partida")
            if attempt < 5:
                await asyncio.sleep(10)
        log.warning("El resultado de la partida aun no esta disponible")

    async def _prediction_result(self, prediction: dict[str, Any]) -> bool | None:
        game = str(prediction.get("game", ""))
        match_key = str(prediction.get("match_key", ""))
        if "league of legends" in game:
            if self.lol is None or not match_key.startswith("lol:"):
                return None
            return await self.lol.match_won(match_key.removeprefix("lol:"))
        if "valorant" in game and self.valorant is not None:
            return await self.valorant.last_match_won()
        return None
