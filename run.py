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
import time
from datetime import datetime, timezone

import aiohttp

from src import logging_setup
from src.agent_server import AgentServer
from src.category import CategoryResolver
from src.chat_embeddings import EmbeddingWorker
from src.chat_history import ChatHistory
from src.chat_history import HistoryEntry
from src.chat_history_db import PostgresChatHistory
from src.llm_summary import LlmSummary
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
from src.twitch.chat import ChatMessage
from src.twitch.helix import Helix

log = logging.getLogger("run")


async def monitor_stream(
    helix: Helix,
    history: ChatHistory,
    database_history: PostgresChatHistory | None,
    llm_summary: LlmSummary,
    arbiter: GameArbiter,
    stop: asyncio.Event,
    chat: ChatClient | None = None,
) -> None:
    """Detect the live-to-offline transition and log a session summary."""
    session_started = time.time()
    stream_id: str | None = None
    session_game: str | None = None
    was_live = False
    while not stop.is_set():
        try:
            live = await helix.get_stream(cfg.twitch_channel)
            if live:
                if not was_live:
                    session_started = time.time()
                    stream_id = str(live.get("id", "")) or None
                    session_game = live.get("game_name")
                    if database_history is not None and stream_id:
                        await database_history.start_session(
                            stream_id,
                            _parse_stream_started(live.get("started_at")),
                            session_game,
                        )
                was_live = True
            elif was_live:
                seconds = max(60, int(time.time() - session_started))
                messages = history.since(seconds)
                if database_history is not None:
                    await database_history.flush()
                    persisted = await database_history.recent(
                        seconds, limit=llm_summary.max_messages
                    )
                    if persisted:
                        messages = [
                            HistoryEntry(
                                author_id="",
                                display_name=item["display_name"],
                                text=item["text"],
                                created_at=item["created_at"],
                            )
                            for item in persisted
                        ]
                summary = None
                min_seconds = cfg.llm_min_stream_minutes * 60
                if seconds >= min_seconds:
                    summary = await llm_summary.summarize(
                        messages, live_game_name(arbiter), _format_seconds(seconds)
                    )
                else:
                    log.info("Stream too short (%dm < %dm), skipping summary", seconds // 60, cfg.llm_min_stream_minutes)
                if summary:
                    log.info("Final stream summary:\n%s", summary)
                    if chat:
                        await chat.say(f"Resumen del stream: {summary}")
                else:
                    log.info("Stream ended; no LLM summary available")
                if database_history is not None and stream_id:
                    final_game = live_game_name(arbiter)
                    if final_game == "sin juego detectado":
                        final_game = session_game
                    await database_history.finish_session(
                        stream_id,
                        datetime.now(timezone.utc),
                        final_game,
                        len(messages),
                        summary,
                    )
                was_live = False
                session_started = time.time()
                stream_id = None
                session_game = None
        except Exception:  # noqa: BLE001 - monitor must not stop the bot
            log.exception("Could not check stream status")
        try:
            await asyncio.wait_for(stop.wait(), timeout=cfg.presence_check_seconds)
        except asyncio.TimeoutError:
            pass


def live_game_name(arbiter: GameArbiter) -> str:
    sources = arbiter.sources()
    games = [report.game for report in sources.values() if report.game and not report.stale]
    return games[0] if games else "sin juego detectado"


def _format_seconds(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours}h" if minutes == 0 else f"{hours}h {minutes}m"


def _parse_stream_started(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return datetime.now(timezone.utc)


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
        log.error("Missing .env configuration: %s. Copy .env.example to .env.", ", ".join(missing))
        return 1

    timeout = aiohttp.ClientTimeout(total=120, connect=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        tokens = JsonStore(cfg.token_file)
        state = JsonStore(cfg.state_file)
        auth = TwitchAuth(session, tokens)
        helix = Helix(session, auth)

        if not auth.has("broadcaster"):
            log.error(
                "Missing broadcaster token. Have %s run:  "
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
            "%s | channel: %s (id %s)",
            cfg.bot_name or "bot", cfg.twitch_channel, broadcaster_id,
        )

        resolver = CategoryResolver(helix, state)
        lol = LolClient(session)
        valorant = ValorantClient(session)
        history = ChatHistory(
            ignored_authors=cfg.chat_ignored_authors | {cfg.bot_login.lower()}
        )
        database_history: PostgresChatHistory | None = None
        if cfg.database_url:
            try:
                database_history = PostgresChatHistory(
                    cfg.database_url,
                    ignored_authors=cfg.chat_ignored_authors | {cfg.bot_login.lower()},
                )
                await database_history.start()
                log.info("PostgreSQL history: active")
            except Exception:  # noqa: BLE001 - database is optional
                database_history = None
                log.exception("PostgreSQL history: disabled; bot will keep it in memory")
        llm_summary = LlmSummary(
            session,
            cfg.llm_api_key,
            cfg.llm_model,
            cfg.llm_base_url,
            cfg.llm_max_messages,
        )
        if llm_summary.configured:
            log.info("LLM summary: active (%s)", cfg.llm_model)

        # --- embeddings (opcional: requiere DB + LLM) ----------------------
        embedding_worker: EmbeddingWorker | None = None
        if database_history is not None and cfg.llm_api_key:
            try:
                embedding_worker = EmbeddingWorker(
                    pool=database_history._pool,  # noqa: SLF001
                    session=session,
                    api_key=cfg.llm_api_key,
                    model=cfg.embedding_model,
                    base_url=cfg.llm_base_url,
                    batch_size=cfg.embedding_batch_size,
                )
                await embedding_worker.start()
                log.info(
                    "Embeddings: active (%s, batch=%d)",
                    cfg.embedding_model, cfg.embedding_batch_size,
                )
                database_history._embedding_worker = embedding_worker  # noqa: SLF001
            except Exception:  # noqa: BLE001
                embedding_worker = None
                log.exception("Embeddings: disabled due to startup error")
        elif not cfg.llm_api_key:
            log.info("Embeddings: disabled (missing LLM_API_KEY)")

        arbiter = GameArbiter(
            helix, resolver, broadcaster_id, state=state, lol=lol, valorant=valorant,
        )
        svc = Services(
            session=session,
            tokens=tokens,
            state=state,
            auth=auth,
            helix=helix,
            resolver=resolver,
            arbiter=arbiter,
            lol=lol,
            valorant=valorant,
            broadcaster_id=broadcaster_id,
            history=history,
            database_history=database_history,
            embeddings=embedding_worker,
            llm_summary=llm_summary,
        )
        tasks: dict[str, asyncio.Task] = {}
        stop = asyncio.Event()
        chat_control: ChatControlServer | None = None

        # --- 2. chat ------------------------------------------------------
        if auth.has("bot"):
            registry = Registry(svc)
            async def on_chat_message(message: ChatMessage) -> None:
                accepted = history.add(message)
                if accepted:
                    log.info("Message saved from %s", message.display_name)
                if accepted and database_history is not None:
                    database_history.enqueue(message)
                await registry.dispatch(message)

            chat = ChatClient(
                session, auth, cfg.bot_login, cfg.twitch_channel, on_chat_message
            )
            svc.chat = chat
            arbiter.announce = chat.say
            tasks["chat"] = asyncio.create_task(chat.run(), name="chat")
            chat_control = ChatControlServer(chat)
            await chat_control.start()
            log.info("Chat bot: active as %s", cfg.bot_login)
        else:
            log.warning(
                "Chat bot: disabled (missing 'python tools/auth_twitch.py bot')"
            )

        # --- 1a. agente de escritorio (fuente principal) ------------------
        agent_server: AgentServer | None = None
        if cfg.agent_token:
            agent_server = AgentServer(arbiter)
            await agent_server.start()
        else:
            log.warning("Desktop agent: disabled (missing AGENT_TOKEN)")

        # --- 1b. presencia de Discord (respaldo) --------------------------
        if cfg.discord_token and cfg.discord_streamer_id:
            presence = PresenceBot(arbiter=arbiter)
            svc.presence = presence
            tasks["discord"] = asyncio.create_task(
                presence.start(cfg.discord_token), name="discord"
            )
            log.info("Discord presence: active (agent fallback)")
        else:
            log.warning(
                "Discord presence: disabled (missing DISCORD_BOT_TOKEN or DISCORD_STREAMER_ID)"
            )

        if not cfg.agent_token and not (cfg.discord_token and cfg.discord_streamer_id):
            log.warning("No sources: category will not change automatically")
        elif cfg.autocat_dry_run:
            log.info("Auto-categorizer in DRY-RUN: not touching Twitch")

        tasks["stream-monitor"] = asyncio.create_task(
            monitor_stream(
                helix, history, database_history, llm_summary, arbiter, stop, svc.chat
            ),
            name="stream-monitor",
        )

        # --- 3. clips -----------------------------------------------------
        drive = DriveUploader(session)
        clips = ClipPipeline(svc, drive)
        svc.clips = clips
        if cfg.clips_enabled and drive.configured:
            tasks["clips"] = asyncio.create_task(clips.scheduler(), name="clips")
            log.info("Clips pipeline: active (daily at %s)", cfg.clips_run_at)
        elif cfg.clips_enabled:
            log.warning(
                "Clips pipeline: disabled (missing GDRIVE_FOLDER_ID or %s)", cfg.sa_path
            )

        if not tasks and agent_server is None:
            log.error("No subsystems configured. Check .env and README.")
            return 1

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                # Windows no soporta add_signal_handler; el KeyboardInterrupt
                # de asyncio.run se encarga.
                pass

        log.info("All systems up. Ctrl+C to stop.")
        stop_task = asyncio.create_task(stop.wait(), name="stop")
        done, _ = await asyncio.wait(
            [*tasks.values(), stop_task], return_when=asyncio.FIRST_COMPLETED
        )
        stop_task.cancel()
        for task in done:
            if task.get_name() != "stop" and not task.cancelled():
                exc = task.exception()
                if exc:
                    log.error("Subsystem '%s' died: %r", task.get_name(), exc)

        log.info("Shutting down...")
        if agent_server is not None:
            await agent_server.stop()
        if svc.chat:
            svc.chat.stop()
        if chat_control is not None:
            await chat_control.stop()
        if svc.presence:
            await svc.presence.close()
        if embedding_worker is not None:
            await embedding_worker.stop()
        if database_history is not None:
            await database_history.stop()
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nGoodbye.")
