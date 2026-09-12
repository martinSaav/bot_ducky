"""Comandos del chat: registro, permisos, cooldowns y despacho."""
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
            return  # silencio: no spameamos el chat con "no tenes permiso"

        # Los mods se saltean el cooldown para poder corregir cosas al toque.
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
                 help="lista de comandos", cooldown=20)
        self.add("resumen", self.cmd_summary, aliases=("summary",),
             help="resume el chat reciente", cooldown=60)
        self.add("lrank", self.cmd_rank, aliases=("rank", "elo", "lol"),
                 help="elo de LoL del streamer", cooldown=10)
        self.add("lmatch", self.cmd_live, aliases=("partida", "live", "game", "enpartida"),
                 help="partida de LoL en curso", cooldown=10)
        self.add("vrank", self.cmd_valorant, aliases=("valorant", "val"),
                 help="rango de Valorant", cooldown=10)
        self.add("vmatch", self.cmd_vmatch, aliases=("ultima", "lastmatch"),
                 help="ultima partida de Valorant", cooldown=15)
        self.add("vgame", self.cmd_vgame,
             help="confirma una partida de Valorant y crea prediction",
             cooldown=5, mod_only=True)
        self.add("vwin", self.cmd_vwin,
             help="resuelve la prediction como victoria", cooldown=5, mod_only=True)
        self.add("vloss", self.cmd_vloss,
             help="resuelve la prediction como derrota", cooldown=5, mod_only=True)
        self.add("vcancel", self.cmd_vcancel,
             help="cancela la prediction actual", cooldown=5, mod_only=True)
        self.add("uptime", self.cmd_uptime, help="tiempo en vivo", cooldown=20)
        self.add("clip", self.cmd_clip, help="crea un clip", cooldown=30)
        self.add("categoria", self.cmd_category, aliases=("cat", "juego"),
                 help="cambia la categoria (mods)", cooldown=5, mod_only=True)
        self.add("auto", self.cmd_auto,
                 help="on/off del auto-categorizador (mods)", cooldown=5, mod_only=True)
        self.add("recargar", self.cmd_reload, aliases=("reload",),
                 help="recarga game_map.json (mods)", cooldown=5, mod_only=True)

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

        # 1. Búsqueda semántica por embeddings (mejor opción si está disponible).
        if ctx.svc.embeddings is not None:
            limit = ctx.svc.llm_summary.max_messages if ctx.svc.llm_summary else 300
            semantic = await ctx.svc.embeddings.search(game, seconds, limit=limit)
            if semantic:
                log.info(
                    "cmd_summary: %d messages via embeddings (window %ds, query=%r)",
                    len(semantic), seconds, game,
                )
                messages = [
                    HistoryEntry(
                        author_id="",
                        display_name=m["display_name"],
                        text=m["text"],
                        created_at=m["created_at"],
                    )
                    for m in semantic
                ]

        # 2. Fallback: mensajes de PostgreSQL por ventana temporal.
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

        # 3. Fallback final: historial en memoria.
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
            actual = (channel or {}).get("game_name") or "sin categoria"
            return f"Categoria actual: {actual}. Uso: {PREFIX}categoria <juego>"
        game = await ctx.svc.resolver.resolve(ctx.argstr)
        if not game:
            return f"No encontre la categoria {ctx.argstr!r} en Twitch."
        await ctx.svc.resolver.apply(ctx.svc.broadcaster_id, game)
        # Un cambio manual manda: el arbitro no lo pisa hasta el proximo juego.
        ctx.svc.arbiter.note_manual_change(game["name"])
        return f"Categoria puesta en {game['name']}."

    async def cmd_auto(self, ctx: Ctx) -> str:
        arbiter = ctx.svc.arbiter
        arg = ctx.argstr.lower()
        if arg in ("on", "si", "1", "true"):
            arbiter.enabled = True
        elif arg in ("off", "no", "0", "false"):
            arbiter.enabled = False
        elif arg:
            return f"Uso: {PREFIX}auto on | {PREFIX}auto off"

        estado = "encendido" if arbiter.enabled else "apagado"
        vivas = [s for s, r in arbiter.sources().items() if not r.stale]
        fuentes = ", ".join(vivas) if vivas else "ninguna"
        return f"Auto-categorizador {estado}. Fuentes activas: {fuentes}."

    async def cmd_reload(self, ctx: Ctx) -> str:
        ctx.svc.resolver.reload_map()
        return "game_map.json recargado."
