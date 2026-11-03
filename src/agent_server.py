"""HTTP endpoint that receives the Rust agent's reports.

The agent sends the absolute state ("playing X" / "playing nothing") whenever
it changes, and as a heartbeat every minute. This module only validates it and
hands it to the arbiter.

It also exposes:
  GET  /api/status       — JSON snapshot of the bot state
  GET  /api/logs         — last N lines of logs/bot.log
  GET  /ws               — WebSocket for real-time events
  GET  /dashboard/{tail} — serves the Angular build (production)
  GET  /health           — for diagnosing the agent
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import WSMsgType, web

from .build_info import build_info
from .config import cfg

if TYPE_CHECKING:
    from .dashboard.status import StatusProvider
    from .gamesource import GameArbiter

log = logging.getLogger("agent")

#: A legitimate report is ~200 bytes. We cut off long before a huge body
#: makes us do pointless work.
MAX_BODY = 65536

#: If the agent does not show up within this time it is considered down and
#: the arbiter falls back to Discord presence. That is 3 heartbeats of margin.
STALE_AFTER = 210.0

# Directory where Angular puts its production build.
_DIST_DIR = Path(__file__).resolve().parent.parent / "dashboard" / "dist" / "dashboard" / "browser"

# Allowed CORS origins (Angular dev server + local production).
_CORS_ORIGINS = {"http://localhost:4200", "http://127.0.0.1:4200"}


def _cors_headers(request: web.Request) -> dict[str, str]:
    origin = request.headers.get("Origin", "")
    if origin in _CORS_ORIGINS:
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Vary": "Origin",
        }
    return {}


class AgentServer:
    def __init__(
        self,
        arbiter: "GameArbiter | None",
        status_provider: "StatusProvider | None" = None,
    ):
        self.arbiter = arbiter
        self.status_provider = status_provider
        self._runner: web.AppRunner | None = None
        self._last_host: str | None = None

    # ------------------------------------------------------------------
    def _authorized(self, request: web.Request) -> bool:
        if not cfg.agent_token:
            # Without a token there is nothing to compare against: an empty
            # "Bearer " header would otherwise match.
            return False
        header = request.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        # Constant-time comparison: the token does not leak through timing.
        return hmac.compare_digest(header[len(prefix):], cfg.agent_token)

    def _dashboard_authorized(self, request: web.Request) -> bool:
        """/api/* and /ws: Bearer header or ?token= (a browser WebSocket
        cannot send headers). Open when DASHBOARD_TOKEN is not set."""
        if not cfg.dashboard_token:
            return True
        header = request.headers.get("Authorization", "")
        supplied = header[len("Bearer "):] if header.startswith("Bearer ") else ""
        supplied = supplied or request.rel_url.query.get("token", "")
        return bool(supplied) and hmac.compare_digest(supplied, cfg.dashboard_token)

    async def handle_game(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            log.warning("Rejected report from %s: invalid token", request.remote)
            return web.json_response({"error": "invalid token"}, status=401)

        if self.arbiter is None:
            return web.json_response({"error": "agent endpoint disabled"}, status=503)

        if request.content_length and request.content_length > MAX_BODY:
            return web.json_response({"error": "body too large"}, status=413)

        try:
            data: Any = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"error": "invalid json"}, status=400)

        if not isinstance(data, dict):
            return web.json_response({"error": "expected an object"}, status=400)

        game = data.get("game")
        if game is not None:
            if not isinstance(game, str):
                return web.json_response({"error": "'game' must be a string or null"}, status=400)
            game = game.strip()[:100] or None

        match_id = data.get("match_id")
        if match_id is not None:
            if not isinstance(match_id, str):
                return web.json_response({"error": "'match_id' must be a string or null"}, status=400)
            match_id = match_id.strip()[:200] or None

        metadata = data.get("metadata")
        if metadata is not None:
            if not isinstance(metadata, dict):
                return web.json_response({"error": "'metadata' must be an object or null"}, status=400)

        host = data.get("host")
        if isinstance(host, str) and host != self._last_host:
            log.info(
                "Agent v%s connected from %s",
                data.get("agent_version", "?"), host[:60],
            )
            self._last_host = host

        agent_logs = data.get("logs", [])
        if isinstance(agent_logs, list):
            clean_logs = [line[:1000] for line in agent_logs if isinstance(line, str)]
            if clean_logs:
                agent_log_file = cfg.log_dir / "agent.log"
                try:
                    cfg.log_dir.mkdir(parents=True, exist_ok=True)
                    with agent_log_file.open("a", encoding="utf-8") as file:
                        file.write("\n".join(clean_logs) + "\n")
                except OSError:
                    log.exception("Could not persist agent logs")

        await self.arbiter.report(
            "agent", game, match_id=match_id, metadata=metadata, stale_after=STALE_AFTER
        )
        return web.Response(status=204)

    async def handle_agent_logs(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"error": "invalid token"}, status=401)

        try:
            data: Any = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"error": "invalid json"}, status=400)

        logs = data.get("logs") if isinstance(data, dict) else None
        if not isinstance(logs, list):
            return web.json_response({"error": "'logs' must be a list"}, status=400)

        clean_logs = [line[:1000] for line in logs if isinstance(line, str)]
        if clean_logs:
            try:
                agent_log_file = cfg.log_dir / "agent.log"
                cfg.log_dir.mkdir(parents=True, exist_ok=True)
                with agent_log_file.open("a", encoding="utf-8") as file:
                    file.write("\n".join(clean_logs) + "\n")
            except OSError:
                log.exception("Could not persist agent logs")
                return web.json_response({"error": "could not save the logs"}, status=500)

        return web.Response(status=204)

    async def handle_health(self, request: web.Request) -> web.Response:
        """For checking from a browser that the port is reachable from the other PC."""
        return web.json_response(
            {"ok": True, "channel": cfg.twitch_channel, "build": build_info()}
        )

    # ------------------------------------------------------------------
    # Dashboard API endpoints
    # ------------------------------------------------------------------

    async def handle_status(self, request: web.Request) -> web.Response:
        """JSON snapshot of the full bot state."""
        headers = _cors_headers(request)
        if not self._dashboard_authorized(request):
            return web.json_response({"error": "invalid token"}, status=401, headers=headers)
        if self.status_provider is None:
            return web.json_response({"error": "status not available"}, status=503, headers=headers)
        try:
            snapshot = self.status_provider.snapshot()
        except Exception as exc:  # noqa: BLE001
            log.exception("Error building status snapshot")
            return web.json_response({"error": str(exc)}, status=500, headers=headers)
        return web.json_response(snapshot, headers=headers)

    async def handle_logs(self, request: web.Request) -> web.Response:
        """Last N lines of the selected log (tail)."""
        headers = _cors_headers(request)
        if not self._dashboard_authorized(request):
            return web.json_response({"error": "invalid token"}, status=401, headers=headers)
        try:
            n = min(int(request.rel_url.query.get("n", "100")), 500)
        except (ValueError, TypeError):
            n = 100
        source = request.rel_url.query.get("source", "bot")
        log_file = cfg.log_dir / ("agent.log" if source == "agent" else "bot.log")
        lines: list[str] = []
        if log_file.exists():
            try:
                # Efficient tail: only the end of the file is read.
                with open(log_file, "rb") as f:
                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    chunk = min(size, n * 200)  # ~200 bytes per line, estimated
                    f.seek(max(0, size - chunk))
                    raw = f.read().decode("utf-8", errors="replace")
                lines = raw.splitlines()[-n:]
            except OSError:
                pass
        return web.json_response({"lines": lines}, headers=headers)

    async def handle_cors_preflight(self, request: web.Request) -> web.Response:
        return web.Response(status=204, headers=_cors_headers(request))

    async def handle_websocket(self, request: web.Request) -> web.StreamResponse:
        """WebSocket for real-time events (chat, game changes, etc.)."""
        if not self._dashboard_authorized(request):
            return web.json_response({"error": "invalid token"}, status=401)
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)

        if self.status_provider is not None:
            self.status_provider.broadcaster.add(ws)
            # Send the initial snapshot on connect.
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
        """Serves the Angular build in production."""
        tail = request.match_info.get("tail", "")
        # Try to serve the requested file; if it does not exist, return index.html
        # (Angular client-side routing).
        if tail:
            candidate = (_DIST_DIR / tail).resolve()
            # This route has no auth: never serve anything outside the build
            # (e.g. ../../data/tokens.json, or an absolute path).
            if not candidate.is_relative_to(_DIST_DIR.resolve()):
                return web.Response(text="Not found", status=404)
            if candidate.is_file():
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
        app.router.add_post("/agent/logs", self.handle_agent_logs)
        app.router.add_get("/health", self.handle_health)
        # Dashboard API
        app.router.add_get("/api/status", self.handle_status)
        app.router.add_get("/api/logs", self.handle_logs)
        app.router.add_get("/ws", self.handle_websocket)
        app.router.add_options("/api/{path:.*}", self.handle_cors_preflight)
        # Serve Angular build (production)
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
        if not cfg.dashboard_token:
            log.warning(
                "DASHBOARD_TOKEN is not set: /api and /ws (chat and logs) are open "
                "to anyone who can reach port %s", cfg.agent_port,
            )
        log.info("Build %s", build_info()["commit"])

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
