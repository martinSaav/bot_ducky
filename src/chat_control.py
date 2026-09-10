"""Control local para enviar mensajes mediante la conexión IRC existente."""
from __future__ import annotations

from typing import Any

from aiohttp import web

from .config import cfg
from .twitch.chat import ChatClient

MAX_MESSAGE = 500


class ChatControlServer:
    def __init__(self, chat: ChatClient):
        self.chat = chat
        self._runner: web.AppRunner | None = None

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "channel": cfg.twitch_channel})

    async def handle_send(self, request: web.Request) -> web.Response:
        try:
            data: Any = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"error": "json invalido"}, status=400)

        text = data.get("text") if isinstance(data, dict) else None
        if not isinstance(text, str) or not text.strip():
            return web.json_response({"error": "'text' debe ser texto"}, status=400)
        text = " ".join(text.split())
        if len(text) > MAX_MESSAGE:
            return web.json_response(
                {"error": f"el mensaje supera los {MAX_MESSAGE} caracteres"},
                status=400,
            )

        await self.chat.say(text)
        return web.json_response({"ok": True})

    async def start(self) -> None:
        app = web.Application(client_max_size=4096)
        app.router.add_get("/health", self.handle_health)
        app.router.add_post("/send", self.handle_send)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, cfg.chat_control_host, cfg.chat_control_port)
        await site.start()

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None