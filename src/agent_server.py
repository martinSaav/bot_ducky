"""Endpoint HTTP que recibe los reportes del agente de Rust.

El agente manda el estado absoluto ("juega X" / "no juega nada") cada vez que
cambia y como latido cada minuto. Aca solo se valida y se pasa al arbitro.
"""
from __future__ import annotations

import hmac
import logging
from typing import Any

from aiohttp import web

from .config import cfg
from .gamesource import GameArbiter

log = logging.getLogger("agent")

#: Un reporte legitimo son ~200 bytes. Cortamos mucho antes de que un cuerpo
#: gigante nos haga trabajar de gusto.
MAX_BODY = 4096

#: Si el agente no aparece en este tiempo lo damos por caido y el arbitro
#: vuelve a la presencia de Discord. Son 3 latidos de margen.
STALE_AFTER = 210.0


class AgentServer:
    def __init__(self, arbiter: GameArbiter):
        self.arbiter = arbiter
        self._runner: web.AppRunner | None = None
        self._last_host: str | None = None

    # ------------------------------------------------------------------
    def _authorized(self, request: web.Request) -> bool:
        header = request.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        # Comparacion en tiempo constante: no filtramos el token por timing.
        return hmac.compare_digest(header[len(prefix):], cfg.agent_token)

    async def handle_game(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            log.warning("Reporte rechazado desde %s: token invalido", request.remote)
            return web.json_response({"error": "token invalido"}, status=401)

        if request.content_length and request.content_length > MAX_BODY:
            return web.json_response({"error": "cuerpo demasiado grande"}, status=413)

        try:
            data: Any = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"error": "json invalido"}, status=400)

        if not isinstance(data, dict):
            return web.json_response({"error": "se esperaba un objeto"}, status=400)

        game = data.get("game")
        if game is not None:
            if not isinstance(game, str):
                return web.json_response({"error": "'game' debe ser texto o null"}, status=400)
            game = game.strip()[:100] or None

        host = data.get("host")
        if isinstance(host, str) and host != self._last_host:
            log.info(
                "Agente v%s conectado desde %s",
                data.get("agent_version", "?"), host[:60],
            )
            self._last_host = host

        await self.arbiter.report("agent", game, stale_after=STALE_AFTER)
        return web.Response(status=204)

    async def handle_health(self, request: web.Request) -> web.Response:
        """Para probar con el navegador que el puerto se ve desde la otra PC."""
        return web.json_response({"ok": True, "channel": cfg.twitch_channel})

    # ------------------------------------------------------------------
    async def start(self) -> None:
        app = web.Application(client_max_size=MAX_BODY)
        app.router.add_post("/game", self.handle_game)
        app.router.add_get("/health", self.handle_health)

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, cfg.agent_host, cfg.agent_port)
        await site.start()
        log.info(
            "Escuchando al agente en http://%s:%s/game", cfg.agent_host, cfg.agent_port
        )

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
