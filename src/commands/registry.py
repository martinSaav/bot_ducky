"""Chat commands: registration, permissions, cooldowns and dispatch.

Command names and every reply sent to chat are in Spanish on purpose: the
stream's viewers type and read them.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from ..config import cfg
from ..chat_history import HistoryEntry
from ..riot.lol import RiotError
from ..riot.valorant import ValorantError
from ..services import Services
from ..twitch.chat import ChatMessage
from ..twitch.helix import HelixError
from ..util import fmt_duration

log = logging.getLogger("commands")

PREFIX = "!"


@dataclass
class Ctx:
    msg: ChatMessage
    args: list[str]
    argstr: str
    svc: Services


Handler = Callable[[Ctx], Awaitable[str | None]]


@dataclass
class Command:
    name: str
    aliases: tuple[str, ...]
    handler: Handler
    help: str
    cooldown: float
    mod_only: bool


class Registry:
    def __init__(self, svc: Services):
        self.svc = svc
        self.commands: dict[str, Command] = {}
        self._last_used: dict[str, float] = {}
        self._register_all()

    # ------------------------------------------------------------------
    def add(
        self,
        name: str,
        handler: Handler,
        *,
        aliases: tuple[str, ...] = (),
        help: str = "",
        cooldown: float = 8.0,
        mod_only: bool = False,
    ) -> None:
        cmd = Command(name, aliases, handler, help, cooldown, mod_only)
        for key in (name, *aliases):
            self.commands[key.lower()] = cmd

    async def dispatch(self, msg: ChatMessage) -> None:
        text = msg.text.strip()
        if not text.startswith(PREFIX):
            return
        raw = text[len(PREFIX):].strip()
        if not raw:
            return
        parts = raw.split()
        cmd = self.commands.get(parts[0].lower())
        if cmd is None:
            return

        log.info(
            "Command !%s invoked by %s: %s",
            parts[0].lower(),
            msg.display_name,
            text,
        )

        if cmd.mod_only and not msg.is_mod:
            return  # silent: do not spam chat with "you do not have permission"

        # Mods skip the cooldown so they can fix things right away.
        if not msg.is_privileged:
            last = self._last_used.get(cmd.name, 0.0)
            if time.monotonic() - last < cmd.cooldown:
                return
        self._last_used[cmd.name] = time.monotonic()

        ctx = Ctx(msg=msg, args=parts[1:], argstr=" ".join(parts[1:]).strip(), svc=self.svc)
        try:
            reply = await cmd.handler(ctx)
        except (RiotError, ValorantError) as exc:
            reply = str(exc)
        except HelixError as exc:
            log.warning("Helix failed on !%s: %s", cmd.name, exc)
            reply = "Twitch no respondio bien a esa peticion, proba de nuevo en un minuto."
        except Exception:  # noqa: BLE001
            log.exception("Error in command !%s", cmd.name)
            reply = "Se rompio algo ejecutando ese comando, ya quedo en el log."

        if reply and self.svc.chat:
            await self.svc.chat.say(reply, reply_to=msg.message_id or None)

    # ------------------------------------------------------------------
    def _register_all(self) -> None:
        self.add("comandos", self.cmd_help, aliases=("ayuda", "help"),
                 help="list of commands", cooldown=20)
        self.add("resumen", self.cmd_summary, aliases=("summary",),
             help="summarizes recent chat", cooldown=60)
        self.add("lrank", self.cmd_rank, aliases=("rank", "elo", "lol"),
                 help="streamer's LoL rank", cooldown=10)
        self.add("lmatch", self.cmd_live, aliases=("partida", "live", "enpartida"),
                 help="LoL match in progress", cooldown=10)
        self.add("vrank", self.cmd_valorant, aliases=("valorant", "val"),
                 help="Valorant rank", cooldown=10)
        self.add("vmatch", self.cmd_vmatch, aliases=("ultima", "lastmatch"),
                 help="last Valorant match", cooldown=15)
        self.add("vgame", self.cmd_vgame,
             help="confirms a Valorant match and creates a prediction",
             cooldown=5, mod_only=True)
        self.add("vwin", self.cmd_vwin,
             help="resolves the prediction as a win", cooldown=5, mod_only=True)
        self.add("vloss", self.cmd_vloss,
             help="resolves the prediction as a loss", cooldown=5, mod_only=True)
        self.add("vcancel", self.cmd_vcancel,
             help="cancels the current prediction", cooldown=5, mod_only=True)
        self.add("uptime", self.cmd_uptime, help="time live", cooldown=20)
        self.add("clip", self.cmd_clip, help="creates a clip", cooldown=30)
        self.add("categoria", self.cmd_category, aliases=("cat", "juego", "game"),
                 help="changes the category (mods)", cooldown=5, mod_only=True)
        self.add("gamef", self.cmd_category_force, aliases=("catf", "juegof"),
                 help="pins the category for 4h; the bot will not override it (mods)", cooldown=5, mod_only=True)
        self.add("auto", self.cmd_auto,
                 help="turns the auto-categorizer on/off (mods)", cooldown=5, mod_only=True)
        self.add("recargar", self.cmd_reload, aliases=("reload",),
                 help="reloads game_map.json (mods)", cooldown=5, mod_only=True)

    # ------------------------------------------------------------------
    async def cmd_help(self, ctx: Ctx) -> str:
        seen: list[str] = []
        for cmd in self.commands.values():
            if cmd.mod_only and not ctx.msg.is_mod:
                continue
            if cmd.name not in seen:
                seen.append(cmd.name)
        return "Comandos: " + " ".join(f"{PREFIX}{n}" for n in seen)

    async def cmd_rank(self, ctx: Ctx) -> str:
        if not ctx.svc.lol.configured:
            return "Los comandos de LoL no estan configurados todavia."
        return await ctx.svc.lol.rank(ctx.argstr or None)

    async def cmd_summary(self, ctx: Ctx) -> str:
        seconds = self._summary_seconds(ctx.argstr)
        if seconds is None:
            return "Uso: !resumen [10m|1h|2h]"

        game = self._current_game(ctx)
        messages = None
        limit = ctx.svc.llm_summary.max_messages if ctx.svc.llm_summary else 300

        # 1. Prefer the database (persistent and complete).
        # If the messages fit within the LLM limit, all of them are sent in
        # chronological order: the LLM reads the full timeline and can follow
        # cause and reaction, jokes, activity spikes, etc.
        # If there are more messages than the limit, stride sampling is used:
        # 1 in every N messages, spread evenly over time, to keep coverage of
        # the whole stream without topical or temporal bias.
        if ctx.svc.database_history is not None:
            all_msgs = await ctx.svc.database_history.recent(seconds, limit=10_000)
            if all_msgs:
                if len(all_msgs) <= limit:
                    sampled = all_msgs
                else:
                    step = len(all_msgs) / limit
                    sampled = [all_msgs[int(i * step)] for i in range(limit)]
                log.info(
                    "cmd_summary: %d/%d messages via %s (window %ds)",
                    len(sampled), len(all_msgs),
                    "full" if len(all_msgs) <= limit else f"stride-{len(all_msgs) / limit:.1f}x",
                    seconds,
                )
                messages = [
                    HistoryEntry(
                        author_id="",
                        display_name=m["display_name"],
                        text=m["text"],
                        created_at=m["created_at"],
                    )
                    for m in sampled
                ]

        # 2. Fallback: PostgreSQL messages by time window.
        if messages is None and ctx.svc.database_history is not None:
            limit = ctx.svc.llm_summary.max_messages if ctx.svc.llm_summary else 2_000
            persisted = await ctx.svc.database_history.recent(seconds, limit=limit)
            if persisted:
                log.info(
                    "cmd_summary: %d messages via time window (%ds)",
                    len(persisted), seconds,
                )
                messages = [
                    HistoryEntry(
                        author_id="",
                        display_name=m["display_name"],
                        text=m["text"],
                        created_at=m["created_at"],
                    )
                    for m in persisted
                ]

        # 3. Last fallback: in-memory history.
        if messages is None:
            messages = ctx.svc.history.since(seconds)

        if not messages:
            return "No hay mensajes guardados en ese período."

        if ctx.svc.llm_summary is not None and ctx.svc.llm_summary.configured:
            summary = await ctx.svc.llm_summary.summarize(
                messages, game, self._format_window(seconds)
            )
            if summary:
                return summary

        topics = ctx.svc.history.topic_words(messages)
        participants = ctx.svc.history.participants(messages)
        topic_text = ", ".join(topics) if topics else "no se detectaron temas claros"
        people_text = ", ".join(f"{name} ({count})" for name, count in participants)
        return (
            f"Resumen ({self._format_window(seconds)}): "
            f"{len(messages)} mensajes. "
            f"Juego: {game}. "
            f"Temas aproximados: {topic_text}. "
            f"Participantes: {people_text}."
        )


    @staticmethod
    def _summary_seconds(value: str) -> int | None:
        if not value:
            return 3600
        match = re.fullmatch(r"([1-9]\d*)([mh])", value.lower())
        if not match:
            return None
        amount, unit = int(match.group(1)), match.group(2)
        seconds = amount * (60 if unit == "m" else 3600)
        return seconds if seconds <= 24 * 3600 else None

    @staticmethod
    def _format_window(seconds: int) -> str:
        return f"{seconds // 3600}h" if seconds % 3600 == 0 else f"{seconds // 60}m"

    @staticmethod
    def _current_game(ctx: Ctx) -> str:
        sources = ctx.svc.arbiter.sources()
        live = [report.game for report in sources.values() if report.game and not report.stale]
        return live[0] if live else "sin juego detectado"

    async def cmd_live(self, ctx: Ctx) -> str:
        if not ctx.svc.lol.configured:
            return "Los comandos de LoL no estan configurados todavia."
        return await ctx.svc.lol.live_game(ctx.argstr or None)

    async def cmd_valorant(self, ctx: Ctx) -> str:
        if not ctx.svc.valorant.configured:
            return "Los comandos de Valorant no estan configurados todavia."
        return await ctx.svc.valorant.rank(ctx.argstr or None)

    async def cmd_vmatch(self, ctx: Ctx) -> str:
        if not ctx.svc.valorant.configured:
            return "Los comandos de Valorant no estan configurados todavia."
        return await ctx.svc.valorant.last_match(ctx.argstr or None)

    async def cmd_vgame(self, ctx: Ctx) -> str:
        if not ctx.svc.valorant.configured:
            return "Los comandos de Valorant no estan configurados todavia."
        return await ctx.svc.arbiter.confirm_valorant_prediction()

    async def cmd_vwin(self, ctx: Ctx) -> str:
        return await ctx.svc.arbiter.resolve_manual_prediction(True)

    async def cmd_vloss(self, ctx: Ctx) -> str:
        return await ctx.svc.arbiter.resolve_manual_prediction(False)

    async def cmd_vcancel(self, ctx: Ctx) -> str:
        return await ctx.svc.arbiter.cancel_prediction()

    async def cmd_uptime(self, ctx: Ctx) -> str:
        stream = await ctx.svc.helix.get_stream(cfg.twitch_channel)
        if not stream:
            return f"{cfg.twitch_channel} esta offline."
        started = datetime.strptime(stream["started_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        return f"{cfg.twitch_channel} lleva {fmt_duration(elapsed)} en vivo."

    async def cmd_clip(self, ctx: Ctx) -> str:
        stream = await ctx.svc.helix.get_stream(cfg.twitch_channel)
        if not stream:
            return "No puedo clipear con el canal offline."
        clip = await ctx.svc.helix.create_clip(ctx.svc.broadcaster_id)
        if not clip:
            return "Twitch no devolvio el clip, proba de nuevo."
        return f"Clip creado: {clip.get('edit_url', '').replace('/edit', '')}"

    async def cmd_category(self, ctx: Ctx) -> str:
        if not ctx.argstr:
            channel = await ctx.svc.helix.get_channel(ctx.svc.broadcaster_id)
            current = (channel or {}).get("game_name") or "sin categoria"
            return f"Categoria actual: {current}. Uso: {PREFIX}categoria <juego>"
        game = await ctx.svc.resolver.resolve(ctx.argstr)
        if not game:
            return f"No encontre la categoria {ctx.argstr!r} en Twitch."
        await ctx.svc.resolver.apply(ctx.svc.broadcaster_id, game)
        # A manual change wins: the arbiter will not override it until the next game.
        ctx.svc.arbiter.note_manual_change(game["name"])
        return f"Categoria puesta en {game['name']}."

    async def cmd_category_force(self, ctx: Ctx) -> str:
        if not ctx.argstr:
            return f"Uso: {PREFIX}gamef <juego>  — fija la categoria por 4h, el bot no la pisa."
        game = await ctx.svc.resolver.resolve(ctx.argstr)
        if not game:
            return f"No encontre la categoria {ctx.argstr!r} en Twitch."
        await ctx.svc.resolver.apply(ctx.svc.broadcaster_id, game)
        # Injects the game as the highest-priority "manual" source (lasts 4h).
        await ctx.svc.arbiter.force_game(game["name"])
        return f"Categoria fijada en {game['name']} por 4h. El auto-categorizador no la va a pisar."

    async def cmd_auto(self, ctx: Ctx) -> str:
        arbiter = ctx.svc.arbiter
        arg = ctx.argstr.lower()
        if arg in ("on", "si", "1", "true"):
            arbiter.enabled = True
        elif arg in ("off", "no", "0", "false"):
            arbiter.enabled = False
        elif arg:
            return f"Uso: {PREFIX}auto on | {PREFIX}auto off"

        status = "encendido" if arbiter.enabled else "apagado"
        live_sources = [s for s, r in arbiter.sources().items() if not r.stale]
        sources_text = ", ".join(live_sources) if live_sources else "ninguna"
        return f"Auto-categorizador {status}. Fuentes activas: {sources_text}."

    async def cmd_reload(self, ctx: Ctx) -> str:
        ctx.svc.resolver.reload_map()
        return "game_map.json recargado."
