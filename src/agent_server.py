"""Endpoint HTTP que recibe los reportes del agente de Rust.

El agente manda el estado absoluto ("juega X" / "no juega nada") cada vez que
cambia y como latido cada minuto. Acá solo se valida y se pasa al árbitro.

Además expone:
  GET  /api/status       — snapshot JSON del estado del bot
  GET  /api/logs         — últimas N líneas de logs/bot.log
  GET  /ws               — WebSocket para eventos en tiempo real
  GET  /dashboard/{tail} — sirve el build de Angular (producción)
  GET  /health           — para diagnóstico del agente
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import WSMsgType, web

from .config import cfg

if TYPE_CHECKING:
    from .dashboard.status import StatusProvider
    from .gamesource import GameArbiter

log = logging.getLogger("agent")

#: Un reporte legítimo son ~200 bytes. Cortamos mucho antes de que un cuerpo
#: gigante nos haga trabajar de gusto.
MAX_BODY = 4096

#: Si el agente no aparece en este tiempo lo damos por caído y el árbitro
#: vuelve a la presencia de Discord. Son 3 latidos de margen.
STALE_AFTER = 210.0

# Directorio donde Angular deposita su build de producción.
_DIST_DIR = Path(__file__).resolve().parent.parent / "dashboard" / "dist" / "dashboard" / "browser"

# CORS origins permitidos (Angular dev server + producción local).
_CORS_ORIGINS = {"http://localhost:4200", "http://127.0.0.1:4200"}


def _cors_headers(request: web.Request) -> dict[str, str]:
    origin = request.headers.get("Origin", "")
    if origin in _CORS_ORIGINS:
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Vary": "Origin",
        }
    return {}


class AgentServer:
    def __init__(
        self,
        arbiter: "GameArbiter",
        status_provider: "StatusProvider | None" = None,
    ):
        self.arbiter = arbiter
        self.status_provider = status_provider
        self._runner: web.AppRunner | None = None
        self._last_host: str | None = None

    # ------------------------------------------------------------------
    def _authorized(self, request: web.Request) -> bool:
        header = request.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        # Comparación en tiempo constante: no filtramos el token por timing.
        return hmac.compare_digest(header[len(prefix):], cfg.agent_token)

    async def handle_game(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            log.warning("Rejected report from %s: invalid token", request.remote)
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

        match_id = data.get("match_id")
        if match_id is not None:
            if not isinstance(match_id, str):
                return web.json_response({"error": "'match_id' debe ser texto o null"}, status=400)
            match_id = match_id.strip()[:200] or None

        metadata = data.get("metadata")
        if metadata is not None:
            if not isinstance(metadata, dict):
                return web.json_response({"error": "'metadata' debe ser un objeto o null"}, status=400)

        host = data.get("host")
        if isinstance(host, str) and host != self._last_host:
            log.info(
                "Agent v%s connected from %s",
                data.get("agent_version", "?"), host[:60],
            )
            self._last_host = host

        await self.arbiter.report(
            "agent", game, match_id=match_id, metadata=metadata, stale_after=STALE_AFTER
        )
        return web.Response(status=204)

    async def handle_health(self, request: web.Request) -> web.Response:
        """Para probar con el navegador que el puerto se ve desde la otra PC."""
        return web.json_response({"ok": True, "channel": cfg.twitch_channel})

    # ------------------------------------------------------------------
    # Dashboard API endpoints
    # ------------------------------------------------------------------

    async def handle_status(self, request: web.Request) -> web.Response:
        """Snapshot JSON del estado completo del bot."""
        headers = _cors_headers(request)
        if self.status_provider is None:
            return web.json_response({"error": "status not available"}, status=503, headers=headers)
        try:
            snapshot = self.status_provider.snapshot()
        except Exception as exc:  # noqa: BLE001
            log.exception("Error building status snapshot")
            return web.json_response({"error": str(exc)}, status=500, headers=headers)
        return web.json_response(snapshot, headers=headers)

    async def handle_logs(self, request: web.Request) -> web.Response:
        """Últimas N líneas del log del bot (tail)."""
        headers = _cors_headers(request)
        try:
            n = min(int(request.rel_url.query.get("n", "100")), 500)
        except (ValueError, TypeError):
            n = 100
        log_file = cfg.log_dir / "bot.log"
        lines: list[str] = []
        if log_file.exists():
            try:
                # Tail eficiente: leemos solo el final del archivo.
                with open(log_file, "rb") as f:
                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    chunk = min(size, n * 200)  # ~200 bytes por línea estimado
                    f.seek(max(0, size - chunk))
                    raw = f.read().decode("utf-8", errors="replace")
                lines = raw.splitlines()[-n:]
            except OSError:
                pass
        return web.json_response({"lines": lines}, headers=headers)

    async def handle_cors_preflight(self, request: web.Request) -> web.Response:
        return web.Response(status=204, headers=_cors_headers(request))

    async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket para eventos en tiempo real (chat, cambios de juego, etc.)."""
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)

        if self.status_provider is not None:
            self.status_provider.broadcaster.add(ws)
            # Enviar snapshot inicial al conectar.
            try:
                snapshot = self.status_provider.snapshot()
                await ws.send_str(json.dumps({"event": "status", "data": snapshot}))
            except Exception:  # noqa: BLE001
                pass

        try:
            async for msg in ws:
                if msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            if self.status_provider is not None:
                self.status_provider.broadcaster.remove(ws)
        return ws

    async def handle_dashboard(self, request: web.Request) -> web.Response:
        """Sirve el build de Angular en producción."""
        tail = request.match_info.get("tail", "")
        # Intentar servir el archivo pedido; si no existe, devolver index.html
        # (client-side routing de Angular).
        if tail and (candidate := _DIST_DIR / tail).exists() and candidate.is_file():
            return web.FileResponse(candidate)
        index = _DIST_DIR / "index.html"
        if index.exists():
            return web.FileResponse(index)
        return web.Response(
            text=(
                "Dashboard not built yet. Run: cd dashboard && npm run build\n"
                "For development, run: cd dashboard && npm start"
            ),
            status=404,
        )

    # ------------------------------------------------------------------
    async def start(self) -> None:
        app = web.Application(client_max_size=MAX_BODY)
        app.router.add_post("/game", self.handle_game)
        app.router.add_get("/health", self.handle_health)
        # Dashboard API
        app.router.add_get("/api/status", self.handle_status)
        app.router.add_get("/api/logs", self.handle_logs)
        app.router.add_get("/ws", self.handle_websocket)
        app.router.add_options("/api/{path:.*}", self.handle_cors_preflight)
        # Serve Angular build (producción)
        app.router.add_get("/dashboard", self.handle_dashboard)
        app.router.add_get("/dashboard/{tail:.*}", self.handle_dashboard)

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, cfg.agent_host, cfg.agent_port)
        await site.start()
        log.info(
            "Listening to agent on http://%s:%s/game", cfg.agent_host, cfg.agent_port
        )
        log.info(
            "Dashboard API on http://%s:%s/api/status", cfg.agent_host, cfg.agent_port
        )

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
