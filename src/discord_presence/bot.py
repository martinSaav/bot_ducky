"""Presencia de Discord como fuente de "que juego esta corriendo".

Es el respaldo del agente de escritorio: sirve cuando el agente no esta
instalado o esta apagado. Este modulo solo informa lo que ve; quien decide
que categoria poner es el GameArbiter.

Requiere activar PRESENCE INTENT y SERVER MEMBERS INTENT en el portal de
desarrolladores de Discord (Bot > Privileged Gateway Intents).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord

from ..config import cfg
from ..gamesource import GameArbiter

log = logging.getLogger("presence")


def playing_name(member: Any) -> str | None:
    """Nombre del juego que Discord reporta como 'Jugando a ...'."""
    for activity in getattr(member, "activities", ()) or ():
        if getattr(activity, "type", None) is discord.ActivityType.playing:
            name = (getattr(activity, "name", "") or "").strip()
            if name:
                return name
    return None


class PresenceBot(discord.Client):
    def __init__(self, *, arbiter: GameArbiter):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        intents.presences = True
        super().__init__(intents=intents)

        self.arbiter = arbiter
        self._presence_check_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    async def on_ready(self) -> None:
        log.info("Discord conectado como %s", self.user)
        if not cfg.discord_streamer_id:
            log.error("DISCORD_STREAMER_ID no configurado: esta fuente no reporta nada")
            return

        member = self._find_streamer()
        if member is None:
            log.error(
                "El streamer (id %s) no aparece en ningun servidor del bot. "
                "Invitalo al servidor privado o revisa el SERVER MEMBERS INTENT.",
                cfg.discord_streamer_id,
            )
            return

        log.info("Vigilando la presencia de %s", member)
        # Si ya estaba jugando cuando arrancamos, informamos igual: sin esto
        # el arbitro no sabria nada hasta el proximo cambio de actividad.
        await self.arbiter.report("discord", playing_name(member))
        if self._presence_check_task is None or self._presence_check_task.done():
            self._presence_check_task = asyncio.create_task(
                self._periodic_check(), name="presence-check"
            )

    def _find_streamer(self) -> Any | None:
        for guild in self.guilds:
            member = guild.get_member(cfg.discord_streamer_id)
            if member is not None:
                return member
        return None

    async def on_presence_update(self, before: Any, after: Any) -> None:
        if after.id != cfg.discord_streamer_id:
            return
        old, new = playing_name(before), playing_name(after)
        if old == new:
            return
        log.debug("Presencia: %r -> %r", old, new)
        await self.arbiter.report("discord", new)

    async def _periodic_check(self) -> None:
        while not self.is_closed():
            await asyncio.sleep(cfg.presence_check_seconds)
            member = self._find_streamer()
            if member is None:
                continue
            await self.arbiter.report(
                "discord", playing_name(member), force=True
            )

    async def close(self) -> None:
        if self._presence_check_task is not None:
            self._presence_check_task.cancel()
            await asyncio.gather(self._presence_check_task, return_exceptions=True)
            self._presence_check_task = None
        await super().close()
