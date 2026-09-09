"""Cliente de chat de Twitch: IRC sobre WebSocket (wss://irc-ws.chat.twitch.tv).

No usa librerias de terceros: el protocolo IRC de Twitch es chico y estable,
y asi evitamos romper el bot cada vez que un wrapper cambia de API mayor.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import aiohttp

from .auth import TwitchAuth

log = logging.getLogger("twitch.chat")

WS_URL = "wss://irc-ws.chat.twitch.tv:443"

# Twitch corta la conexion si superas el limite. 20 msg/30s para cuentas
# normales, 100 para moderadores del canal. Nos quedamos por debajo.
RATE_NORMAL = (18, 30.0)
RATE_MOD = (90, 30.0)

_TAG_UNESCAPE = {"\\s": " ", "\\:": ";", "\\r": "\r", "\\n": "\n", "\\\\": "\\"}


def _unescape_tag(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        pair = value[i : i + 2]
        if pair in _TAG_UNESCAPE:
            out.append(_TAG_UNESCAPE[pair])
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def parse_line(line: str) -> tuple[dict[str, str], str, str, list[str], str | None]:
    """Parsea una linea IRCv3 -> (tags, prefix, comando, args, trailing)."""
    tags: dict[str, str] = {}
    rest = line
    if rest.startswith("@"):
        raw, _, rest = rest[1:].partition(" ")
        for part in raw.split(";"):
            key, _, val = part.partition("=")
            if key:
                tags[key] = _unescape_tag(val)
    prefix = ""
    if rest.startswith(":"):
        prefix, _, rest = rest[1:].partition(" ")
    parts = rest.split(" ")
    command = parts[0].upper() if parts else ""
    args: list[str] = []
    trailing: str | None = None
    for i in range(1, len(parts)):
        if parts[i].startswith(":"):
            trailing = " ".join(parts[i:])[1:]
            break
        args.append(parts[i])
    return tags, prefix, command, args, trailing


@dataclass
class ChatMessage:
    channel: str
    author: str
    author_id: str
    display_name: str
    text: str
    message_id: str
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def badges(self) -> set[str]:
        raw = self.tags.get("badges", "")
        return {b.split("/")[0] for b in raw.split(",") if b}

    @property
    def is_broadcaster(self) -> bool:
        return "broadcaster" in self.badges

    @property
    def is_mod(self) -> bool:
        return self.tags.get("mod") == "1" or self.is_broadcaster or "moderator" in self.badges

    @property
    def is_vip(self) -> bool:
        return "vip" in self.badges

    @property
    def is_privileged(self) -> bool:
        """Mod, VIP o el propio streamer: puede usar comandos de control."""
        return self.is_mod or self.is_vip


class RateLimiter:
    """Token bucket por ventana deslizante."""

    def __init__(self, limit: int, window: float):
        self.limit = limit
        self.window = window
        self._times: deque[float] = deque()

    def configure(self, limit: int, window: float) -> None:
        self.limit, self.window = limit, window

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            while self._times and now - self._times[0] > self.window:
                self._times.popleft()
            if len(self._times) < self.limit:
                self._times.append(now)
                return
            await asyncio.sleep(self.window - (now - self._times[0]) + 0.05)


class ChatClient:
    """Conexion persistente al chat con reconexion automatica."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        auth: TwitchAuth,
        nick: str,
        channel: str,
        on_message: Callable[[ChatMessage], Awaitable[None]],
    ):
        self.session = session
        self.auth = auth
        self.nick = nick.lower()
        self.channel = channel.lower()
        self.on_message = on_message
        self.limiter = RateLimiter(*RATE_NORMAL)
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._connected = asyncio.Event()
        self._stop = False
        self._auth_failed = False

    # ------------------------------------------------------------------
    async def send_raw(self, line: str) -> None:
        ws = self._ws
        if ws is None or ws.closed:
            raise ConnectionError("Chat desconectado")
        await ws.send_str(line + "\r\n")

    async def say(self, text: str, reply_to: str | None = None) -> None:
        """Manda un mensaje al canal, respetando el rate limit."""
        text = " ".join(text.split())
        if not text:
            return
        # Twitch corta en 500 caracteres; partimos en trozos para no perder texto.
        for chunk in [text[i : i + 480] for i in range(0, len(text), 480)] or [""]:
            await self.limiter.acquire()
            prefix = f"@reply-parent-msg-id={reply_to} " if reply_to else ""
            try:
                await self.send_raw(f"{prefix}PRIVMSG #{self.channel} :{chunk}")
            except ConnectionError:
                log.warning("Mensaje descartado, chat desconectado: %r", chunk[:60])
                return
            reply_to = None  # solo el primer trozo responde

    async def wait_ready(self, timeout: float = 30.0) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def stop(self) -> None:
        self._stop = True

    # ------------------------------------------------------------------
    async def run(self) -> None:
        backoff = 1.0
        while not self._stop:
            try:
                await self._connect_and_listen()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - el loop nunca debe morir
                log.warning("Chat caido (%s). Reconectando en %.0fs", exc, backoff)
            finally:
                self._connected.clear()
                self._ws = None
            if self._stop:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _connect_and_listen(self) -> None:
        token = await self.auth.bearer("bot")
        async with self.session.ws_connect(WS_URL, heartbeat=60) as ws:
            self._ws = ws
            await self.send_raw("CAP REQ :twitch.tv/tags twitch.tv/commands")
            await self.send_raw(f"PASS oauth:{token}")
            await self.send_raw(f"NICK {self.nick}")
            log.info("Conectando al chat como %s...", self.nick)

            async for msg in ws:
                if msg.type is aiohttp.WSMsgType.TEXT:
                    for line in msg.data.split("\r\n"):
                        if line:
                            await self._handle(line)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        if self._auth_failed:
            self._auth_failed = False
            log.info("Refrescando token del bot tras fallo de login")
            await self.auth.refresh("bot")

    async def _handle(self, line: str) -> None:
        tags, prefix, command, args, trailing = parse_line(line)

        if command == "PING":
            await self.send_raw(f"PONG :{trailing or 'tmi.twitch.tv'}")
            return

        if command == "001":  # RPL_WELCOME: login aceptado
            await self.send_raw(f"JOIN #{self.channel}")
            return

        if command == "JOIN" and prefix.split("!")[0].lower() == self.nick:
            log.info("Conectado al chat de #%s", self.channel)
            self._connected.set()
            return

        if command == "USERSTATE":
            # Si somos mod del canal podemos subir el limite de envio.
            badges = {b.split("/")[0] for b in tags.get("badges", "").split(",") if b}
            if tags.get("mod") == "1" or badges & {"moderator", "broadcaster"}:
                self.limiter.configure(*RATE_MOD)
            return

        if command == "RECONNECT":
            log.info("Twitch pidio RECONNECT")
            ws = self._ws
            if ws:
                await ws.close()
            return

        if command == "NOTICE":
            text = trailing or ""
            log.info("NOTICE: %s", text)
            if "authentication failed" in text.lower() or "improperly formatted" in text.lower():
                self._auth_failed = True
                ws = self._ws
                if ws:
                    await ws.close()
            return

        if command == "PRIVMSG":
            channel = (args[0] if args else "").lstrip("#")
            author = prefix.split("!")[0]
            message = ChatMessage(
                channel=channel,
                author=author.lower(),
                author_id=tags.get("user-id", ""),
                display_name=tags.get("display-name") or author,
                text=trailing or "",
                message_id=tags.get("id", ""),
                tags=tags,
            )
            if message.author == self.nick:
                return  # no reaccionamos a nuestros propios mensajes
            try:
                await self.on_message(message)
            except Exception:  # noqa: BLE001
                log.exception("Error procesando mensaje de %s", message.author)
