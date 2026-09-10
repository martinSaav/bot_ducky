"""Punto de entrada: levanta los tres subsistemas en un solo proceso asyncio.

  1. Auto-categorizador  (agente de escritorio y/o presencia de Discord)
  2. Bot de chat         (IRC de Twitch + comandos con stats de Riot)
  3. Pipeline de clips   (Helix -> yt-dlp -> Google Drive, diario)

Cada subsistema se activa solo si su configuracion esta completa, asi que
podes empezar con uno y sumar los demas despues.
"""
from __future__ import annotations

import asyncio
import logging
import signal

import aiohttp

from src import logging_setup
from src.agent_server import AgentServer
from src.category import CategoryResolver
from src.chat_control import ChatControlServer
from src.clips.pipeline import ClipPipeline
from src.commands.registry import Registry
from src.config import cfg
from src.discord_presence.bot import PresenceBot
from src.drive import DriveUploader
from src.gamesource import GameArbiter
from src.riot.lol import LolClient
from src.riot.valorant import ValorantClient
from src.services import Services
from src.storage import JsonStore
from src.twitch.auth import TwitchAuth
from src.twitch.chat import ChatClient
from src.twitch.helix import Helix

log = logging.getLogger("run")


async def resolve_broadcaster_id(auth: TwitchAuth, helix: Helix) -> str:
    """user_id del canal a administrar, desde el token o desde /helix/users."""
    entry = auth.info("broadcaster")
    if entry.get("user_id"):
        login = (entry.get("login") or "").lower()
        if login and login != cfg.twitch_channel:
            # Cortamos en vez de avisar: seguir significaria cambiarle la
            # categoria al canal equivocado, y un warning en el log no es
            # proteccion suficiente para eso.
            raise RuntimeError(
                f"El token de broadcaster es de '{login}' pero TWITCH_CHANNEL es "
                f"'{cfg.twitch_channel}'. Con esto le cambiaria la categoria al canal "
                f"equivocado.\n"
                f"    Reautoriza con la cuenta correcta:\n"
                f"      python tools/auth_twitch.py broadcaster"
            )
        return str(entry["user_id"])
    user = await helix.get_user(cfg.twitch_channel)
    if not user:
        raise RuntimeError(f"No existe el canal de Twitch '{cfg.twitch_channel}'")
    return str(user["id"])


async def main() -> int:
    logging_setup.setup()

    missing = cfg.missing("twitch_client_id", "twitch_client_secret", "twitch_channel")
    if missing:
        log.error("Falta configurar en .env: %s. Copia .env.example a .env.", ", ".join(missing))
        return 1

    timeout = aiohttp.ClientTimeout(total=120, connect=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        tokens = JsonStore(cfg.token_file)
        state = JsonStore(cfg.state_file)
        auth = TwitchAuth(session, tokens)
        helix = Helix(session, auth)

        if not auth.has("broadcaster"):
            log.error(
                "Falta el token del broadcaster. Que %s corra:  "
                "python tools/auth_twitch.py broadcaster",
                cfg.twitch_channel,
            )
            return 1

        try:
            broadcaster_id = await resolve_broadcaster_id(auth, helix)
        except RuntimeError as exc:
            log.error("%s", exc)
            return 1
        log.info(
            "%s | canal: %s (id %s)",
            cfg.bot_name or "bot", cfg.twitch_channel, broadcaster_id,
        )

        resolver = CategoryResolver(helix, state)
        arbiter = GameArbiter(helix, resolver, broadcaster_id)

        svc = Services(
            session=session,
            tokens=tokens,
            state=state,
            auth=auth,
            helix=helix,
            resolver=resolver,
            arbiter=arbiter,
            lol=LolClient(session),
            valorant=ValorantClient(session),
            broadcaster_id=broadcaster_id,
        )

        tasks: dict[str, asyncio.Task] = {}
        stop = asyncio.Event()
        chat_control: ChatControlServer | None = None

        # --- 2. chat ------------------------------------------------------
        if auth.has("bot"):
            registry = Registry(svc)
            chat = ChatClient(
                session, auth, cfg.bot_login, cfg.twitch_channel, registry.dispatch
            )
            svc.chat = chat
            arbiter.announce = chat.say
            tasks["chat"] = asyncio.create_task(chat.run(), name="chat")
            chat_control = ChatControlServer(chat)
            await chat_control.start()
            log.info("Bot de chat: activo como %s", cfg.bot_login)
        else:
            log.warning(
                "Bot de chat: apagado (falta 'python tools/auth_twitch.py bot')"
            )

        # --- 1a. agente de escritorio (fuente principal) ------------------
        agent_server: AgentServer | None = None
        if cfg.agent_token:
            agent_server = AgentServer(arbiter)
            await agent_server.start()
        else:
            log.warning("Agente de escritorio: apagado (falta AGENT_TOKEN)")

        # --- 1b. presencia de Discord (respaldo) --------------------------
        if cfg.discord_token and cfg.discord_streamer_id:
            presence = PresenceBot(arbiter=arbiter)
            svc.presence = presence
            tasks["discord"] = asyncio.create_task(
                presence.start(cfg.discord_token), name="discord"
            )
            log.info("Presencia de Discord: activa (respaldo del agente)")
        else:
            log.warning(
                "Presencia de Discord: apagada (falta DISCORD_BOT_TOKEN o DISCORD_STREAMER_ID)"
            )

        if not cfg.agent_token and not (cfg.discord_token and cfg.discord_streamer_id):
            log.warning("Sin fuentes: la categoria no se va a cambiar sola")
        elif cfg.autocat_dry_run:
            log.info("Auto-categorizador en DRY-RUN: no toca Twitch")

        # --- 3. clips -----------------------------------------------------
        drive = DriveUploader(session)
        clips = ClipPipeline(svc, drive)
        svc.clips = clips
        if cfg.clips_enabled and drive.configured:
            tasks["clips"] = asyncio.create_task(clips.scheduler(), name="clips")
            log.info("Pipeline de clips: activo (diario a las %s)", cfg.clips_run_at)
        elif cfg.clips_enabled:
            log.warning(
                "Pipeline de clips: apagado (falta GDRIVE_FOLDER_ID o %s)", cfg.sa_path
            )

        if not tasks and agent_server is None:
            log.error("No hay ningun subsistema configurado. Revisa el .env y el README.")
            return 1

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                # Windows no soporta add_signal_handler; el KeyboardInterrupt
                # de asyncio.run se encarga.
                pass

        log.info("Todo arriba. Ctrl+C para cortar.")
        stop_task = asyncio.create_task(stop.wait(), name="stop")
        done, _ = await asyncio.wait(
            [*tasks.values(), stop_task], return_when=asyncio.FIRST_COMPLETED
        )
        stop_task.cancel()
        for task in done:
            if task.get_name() != "stop" and not task.cancelled():
                exc = task.exception()
                if exc:
                    log.error("El subsistema '%s' murio: %r", task.get_name(), exc)

        log.info("Cerrando...")
        if agent_server is not None:
            await agent_server.stop()
        if svc.chat:
            svc.chat.stop()
        if chat_control is not None:
            await chat_control.stop()
        if svc.presence:
            await svc.presence.close()
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nChau.")
