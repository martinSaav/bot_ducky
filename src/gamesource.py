"""Arbiter between the sources that say which game is running.

Today there are two: the Rust agent on the streamer's PC and Discord
presence. The agent wins because it looks at the real processes; Discord
remains as a fallback for when the agent is off or not installed.

All the "which category to set" logic lives here, in one place, so the two
sources do not fight over the channel.

Text that viewers see in Twitch (prediction title and options, chat
announcements, replies to the manual commands) is kept in Spanish on purpose.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .category import CategoryResolver
from .config import cfg
from .riot.lol import RiotError
from .twitch.helix import Helix

log = logging.getLogger("gamesource")

Announcer = Callable[[str], Awaitable[None]]

# Higher number wins. The agent sees processes; Discord sees a status the
# streamer may have misconfigured or out of date.
# "manual" has the highest priority: a mod set it by hand with !gamef.
PRIORITY = {"manual": 30, "agent": 20, "discord": 10}

# How many seconds the manual source lasts before it expires (4 hours).
MANUAL_STALE_AFTER = 4 * 3600

# Local client phases (the agent's shared vocabulary) in which a match is
# being played and opening a prediction still makes sense. LoL never reports
# "pregame": a dodge in champion select does not open anything.
LIVE_PHASES = {"pregame", "in_game"}

# Local state source the agent reports for each game.
LOCAL_SOURCE = {"league of legends": "lcu", "valorant": "presence"}

# Twitch allows a single open prediction (active or locked) per channel.
OPEN_PREDICTION = {"ACTIVE", "LOCKED"}


@dataclass
class Report:
    game: str | None
    at: float
    #: Seconds after which the source is considered dead. None = never.
    stale_after: float | None
    #: Active match ID reported by the agent (e.g. from the Valorant client).
    match_id: str | None = None
    #: Live data the agent reads from the game client.
    metadata: dict[str, Any] | None = None
    #: Wall-clock time (time.time()) when match_id was first seen. Shown on
    #: the dashboard as the match timer.
    match_since: float | None = None

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
        self._last_skip: str | None = None
        #: Match that could not get a prediction because another one was
        #: still open on Twitch. Retried as soon as that one closes.
        self._waiting_key: str | None = None

    # ------------------------------------------------------------------
    def sources(self) -> dict[str, Report]:
        """Current state of each source. Only for diagnostics and commands."""
        return dict(self._reports)

    def note_manual_change(self, game_name: str) -> None:
        """A manual !categoria overrides whatever was last applied automatically."""
        self._last_applied = game_name

    def clear_forced_game(self) -> None:
        """Ends a !gamef pin early: the automatic sources decide again."""
        self._reports.pop("manual", None)
        self._schedule(0)

    async def force_game(self, game_name: str) -> None:
        """Forces a game as the highest-priority 'manual' source.

        Lasts MANUAL_STALE_AFTER seconds (4h). Used by !gamef.
        """
        await self.report(
            "manual",
            game_name,
            stale_after=MANUAL_STALE_AFTER,
            force=True,
        )

    async def report(
        self,
        source: str,
        game: str | None,
        *,
        match_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        stale_after: float | None = None,
        force: bool = False,
    ) -> None:
        """A source reports which game it sees. Always the absolute state."""
        previous = self._reports.get(source)
        if match_id is None:
            match_since = None
        elif previous is not None and previous.match_id == match_id and previous.match_since:
            match_since = previous.match_since
        else:
            match_since = time.time()
        self._reports[source] = Report(
            game=game,
            at=time.monotonic(),
            stale_after=stale_after,
            match_id=match_id,
            metadata=metadata,
            match_since=match_since,
        )

        if previous is not None and previous.game == game and not previous.stale and not force:
            # Same game, but the match changed: it started, it ended, or the
            # result is known. It used to react only to a new match_id, so the
            # end of a match went unnoticed.
            if previous.match_id != match_id or previous.metadata != metadata:
                log.debug(
                    "Source '%s' reports match change %r -> %r",
                    source, previous.match_id, match_id,
                )
                self._schedule(self._debounce_for(source))
            return

        log.debug("Source '%s' reports %r", source, game)
        self._schedule(self._debounce_for(source))

    # ------------------------------------------------------------------
    def _debounce_for(self, source: str) -> int:
        # The agent sees the process, so there is no need to wait as long as
        # with Discord, where alt-tabbing to the launcher changes the status.
        return cfg.agent_debounce if source == "agent" else cfg.presence_debounce

    def _winner(self) -> tuple[str | None, str | None]:
        """(source, game) of the live source with the highest priority."""
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
        except Exception:  # noqa: BLE001 - must never kill the task
            log.exception("Failed to apply category")

    # ------------------------------------------------------------------
    async def apply(self) -> None:
        source, game_name = self._winner()

        active_prediction = self.state.get("prediction_active") if self.state else None
        previous_game = str((active_prediction or {}).get("game", ""))
        if active_prediction and "league of legends" in previous_game:
            await self._check_lol_prediction_finished(active_prediction)
        if active_prediction and "valorant" in previous_game:
            self._check_valorant_prediction_finished(active_prediction)
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
            log.info("Auto-categorizer disabled; ignoring %r", game_name)
            return

        if cfg.autocat_only_when_live:
            stream = await self.helix.get_stream(cfg.twitch_channel)
            if not stream:
                log.info("Channel offline and AUTOCAT_ONLY_WHEN_LIVE=true; leaving category unchanged")
                return

        target = game_name or cfg.autocat_idle_category
        if not target:
            return
        if game_name and self.resolver.is_ignored(game_name):
            log.debug("%r is in the ignore list", game_name)
            return

        game = await self.resolver.resolve(target)
        if not game:
            log.info("No Twitch category found for %r; leaving channel unchanged", target)
            return
        if game["name"] == self._last_applied:
            await self._maybe_start_prediction(game_name)
            return

        changed = await self.resolver.apply(self.broadcaster_id, game)
        self._last_applied = game["name"]
        log.info("Category %s (source: %s)", game["name"], source or "?")

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
            if self.state is not None:
                await self._forget_finished_prediction()
            match_key = await self._prediction_match_key(normalized)
            if match_key is None:
                return
            if self.state is not None and self.state.get("prediction_created_key") == match_key:
                return

            active = await self.helix.get_predictions(self.broadcaster_id)
            if any(prediction.get("status") in OPEN_PREDICTION for prediction in active):
                # A locked one counts too: Twitch rejects a new prediction while
                # the previous one is unresolved. The key is not stored, so this
                # match still gets its prediction once that one closes.
                self._waiting_key = match_key
                self._skip("a prediction is already open on Twitch; resolve it with !vwin / !vloss")
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
            log.info("Prediction created for %s (id %s)", game_name, prediction.get("id", "?"))
            self._last_skip = None
            self._waiting_key = None
            if self.announce and cfg.autocat_announce:
                await self.announce(
                    cfg.say_as(
                        "Prediction iniciada: ¿Gana esta partida? "
                        "Voten Gana o Pierde."
                    )
                )
        except RiotError as exc:
            # Expired or invalid key: LolClient already logged the WARNING and
            # marked itself as not configured; the full traceback is not needed.
            log.warning("Prediction cancelled due to Riot API error: %s", exc)
        except Exception:  # noqa: BLE001 - a prediction must not take the arbiter down
            log.exception("Failed to create prediction for %s", game_name)

    async def confirm_valorant_prediction(self) -> str:
        """Starts a Valorant prediction after manual confirmation."""
        if self.state is None:
            return "No puedo guardar el estado de la prediction."
        await self._forget_finished_prediction()
        if self.state.get("prediction_active"):
            return "Ya hay una prediction activa registrada."

        active = await self.helix.get_predictions(self.broadcaster_id)
        if any(prediction.get("status") in OPEN_PREDICTION for prediction in active):
            return "Twitch ya tiene una prediction activa."

        normalized = "valorant"
        match_key = f"valorant:manual:{time.time_ns()}"
        prediction = await self.helix.create_prediction(
            self.broadcaster_id,
            title="¿Gana esta partida?",
            outcomes=["Gana", "Pierde"],
            prediction_window=cfg.prediction_window,
        )
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
        await self.state.set("prediction_session", {"game": normalized, "key": match_key})
        return "Prediction de Valorant iniciada."

    async def resolve_manual_prediction(self, won: bool) -> str:
        """Manually resolves the current prediction (Valorant or LoL)."""
        prediction = self.state.get("prediction_active") if self.state else None
        if not prediction:
            return "No hay una prediction activa registrada."
        outcome = "gana" if won else "pierde"
        outcome_id = (prediction.get("outcomes") or {}).get(outcome)
        if not outcome_id:
            return "La prediction no tiene el resultado esperado."
        await self.helix.resolve_prediction(
            self.broadcaster_id,
            prediction_id=prediction["prediction_id"],
            winning_outcome_id=outcome_id,
        )
        await self.state.set("prediction_active", None)
        self._retry_waiting_match()
        return "Partida ganada." if won else "Partida perdida."

    async def cancel_prediction(self) -> str:
        prediction = self.state.get("prediction_active") if self.state else None
        if not prediction:
            return "No hay una prediction activa registrada."
        try:
            await self.helix.cancel_prediction(
                self.broadcaster_id, prediction["prediction_id"]
            )
        except Exception as exc:  # Twitch refuses to cancel events that already ended.
            if "already ended" not in str(exc).lower():
                raise
        await self.state.set("prediction_active", None)
        self._retry_waiting_match()
        return "Prediction cancelada."

    async def _forget_finished_prediction(self) -> None:
        """Removes from the state a prediction that Twitch already closed."""
        prediction = self.state.get("prediction_active") if self.state else None
        if not prediction:
            return
        remote = await self.helix.get_predictions(self.broadcaster_id)
        current = next(
            (item for item in remote if item.get("id") == prediction.get("prediction_id")),
            None,
        )
        if current is None or current.get("status") not in {"ACTIVE", "LOCKED"}:
            await self.state.set("prediction_active", None)

    def _retry_waiting_match(self) -> None:
        """A prediction just closed: open the one for the match that was waiting.

        Without this, nothing would call apply() again until a source reports a
        change, which the agent does not do mid-match.
        """
        if self._waiting_key is not None:
            self._schedule(0)

    def _skip(self, reason: str) -> None:
        """Reason for not creating a prediction, logged once per reason.

        apply() runs every minute: without this the log repeated the same line.
        """
        if reason != self._last_skip:
            log.info("Not creating prediction: %s", reason)
            self._last_skip = reason

    def _local(self, normalized_game: str) -> Report | None:
        """Agent report carrying state read from this game's local client."""
        source = next((s for g, s in LOCAL_SOURCE.items() if g in normalized_game), None)
        report = self._reports.get("agent")
        if source is None or report is None or report.stale:
            return None
        metadata = report.metadata or {}
        if metadata.get("source") != source or not metadata.get("phase"):
            return None
        return report

    def _local_result(self, normalized_game: str, match_id: str) -> bool | None:
        """Result of a match according to the local client, if already known."""
        report = self._local(normalized_game)
        if report is None:
            return None
        metadata = report.metadata or {}
        last = metadata.get("last_match") or {}
        candidates = []
        if report.match_id == match_id:
            candidates.append(metadata.get("result"))
        if str(last.get("id", "")) == match_id:
            candidates.append(last.get("result"))
        for result in candidates:
            if result == "win":
                return True
            if result == "loss":
                return False
        return None

    def _local_valorant_result(self, match_id: str) -> bool | None:
        """Result of a finished Valorant match, from its final score.

        Only last_match counts: the score of a match still being played does
        not decide anything. A tie (or no score) stays manual.
        """
        report = self._local("valorant")
        if report is None:
            return None
        last = (report.metadata or {}).get("last_match") or {}
        if str(last.get("id", "")) != match_id:
            return None
        score = last.get("score") or {}
        ally, enemy = score.get("ally"), score.get("enemy")
        if not isinstance(ally, int) or not isinstance(enemy, int) or ally == enemy:
            return None
        return ally > enemy

    async def _prediction_match_key(self, normalized_game: str) -> str | None:
        if "league of legends" in normalized_game:
            prefix = "lol"
        elif "valorant" in normalized_game:
            prefix = "valorant"
        else:
            return None

        # 1. The local client, through the agent: it knows whether a match
        #    is running without any API key. If it answers, it wins.
        local = self._local(normalized_game)
        if local is not None:
            phase = (local.metadata or {}).get("phase")
            if phase in LIVE_PHASES and local.match_id:
                return f"{prefix}:{local.match_id}"
            self._skip(f"local client says {prefix} is not in a match (phase: {phase})")
            return None

        # 2. Public APIs, when the agent has no data from the client.
        if prefix == "lol":
            if self.lol is None or not self.lol.configured:
                self._skip("no local LoL data and the Riot API is unavailable (key missing or expired)")
                return None
            game_id = await self.lol.active_game_id()
            if not game_id:
                self._skip("Riot API says the account is not in a match")
                return None
            return f"lol:{game_id}"

        # Valorant has no public live-match API: what remains is the bot's
        # own local client, for when it runs on the same PC as the game.
        if self.valorant is not None and self.valorant.local.configured:
            match_id = await self.valorant.local.active_match_id()
            if match_id:
                return f"valorant:{match_id}"
        self._skip("no local Valorant data (agent not reporting presence, or not in a match)")
        return None

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
                        log.error("Prediction missing outcome for %s", outcome)
                        return
                    await self.helix.resolve_prediction(
                        self.broadcaster_id,
                        prediction_id=prediction["prediction_id"],
                        winning_outcome_id=outcome_id,
                    )
                    await self.state.set("prediction_active", None)
                    self._retry_waiting_match()
                    if self.announce and cfg.autocat_announce:
                        await self.announce(
                            cfg.say_as("Partida ganada." if won else "Partida perdida.")
                        )
                    log.info("Prediction resolved: %s", "won" if won else "lost")
                    return
            except Exception:  # noqa: BLE001 - the result can be retried
                log.exception("Failed to check match result")
            if attempt < 5:
                await asyncio.sleep(10)
        log.warning("Match result is not yet available")

    async def _prediction_result(self, prediction: dict[str, Any]) -> bool | None:
        game = str(prediction.get("game", ""))
        match_key = str(prediction.get("match_key", ""))
        if "league of legends" in game:
            if not match_key.startswith("lol:"):
                return None
            game_id = match_key.removeprefix("lol:")
            local = self._local_result("league of legends", game_id)
            if local is not None:
                log.info("LoL result from the local client: %s", "win" if local else "loss")
                return local
            if self.lol is not None and self.lol.configured:
                return await self.lol.match_won(game_id)
            return None
        if "valorant" in game:
            # Keys from the agent are "valorant:<id>"; the ones from !vgame are
            # "valorant:manual:<ns>" and have no local match to read.
            match_id = match_key.removeprefix("valorant:")
            if match_key.startswith("valorant:") and not match_id.startswith("manual:"):
                local = self._local_valorant_result(match_id)
                if local is not None:
                    log.info("Valorant result from the final score: %s", "win" if local else "loss")
                    return local
            log.info("No Valorant result yet: use !vwin or !vloss if it does not arrive")
            return None
        return None

    def _check_valorant_prediction_finished(self, prediction: dict[str, Any]) -> None:
        """Detect a finished Valorant match while the game is still VALORANT."""
        match_key = str(prediction.get("match_key", ""))
        match_id = match_key.removeprefix("valorant:")
        if self._local_valorant_result(match_id) is not None:
            self._schedule_prediction_result()

    async def _check_lol_prediction_finished(self, prediction: dict[str, Any]) -> None:
        """Detect a finished LoL game even when the active game is still LoL."""
        match_key = str(prediction.get("match_key", ""))
        if not match_key.startswith("lol:"):
            return
        game_id = match_key.removeprefix("lol:")

        local = self._local("league of legends")
        if local is not None:
            if self._local_result("league of legends", game_id) is not None:
                self._schedule_prediction_result()
            elif local.match_id != game_id and self.lol is not None and self.lol.configured:
                # It ended but the client gave no result (remake, client
                # closed): let the public API confirm it.
                self._schedule_prediction_result()
            return

        if self.lol is None or not self.lol.configured:
            return
        active_game_id = await self.lol.active_game_id()
        if active_game_id is None:
            log.info("LoL match is no longer active; checking its result")
            self._schedule_prediction_result()
