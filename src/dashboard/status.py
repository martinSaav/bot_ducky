"""Estado del bot serializable para el dashboard web.

StatusProvider recolecta información de todos los subsistemas en un dict
listo para ser enviado como JSON. WSBroadcaster gestiona las conexiones
WebSocket activas y hace push de eventos en tiempo real.
"""
from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from aiohttp import web

if TYPE_CHECKING:
    from ..gamesource import GameArbiter
    from ..chat_history import ChatHistory
    from ..storage import JsonStore
    from ..riot.lol import LolClient
    from ..riot.valorant import ValorantClient

log = logging.getLogger("dashboard")


class WSBroadcaster:
    """Gestiona las conexiones WebSocket activas y hace broadcast de eventos."""

    def __init__(self) -> None:
        self._sockets: set[web.WebSocketResponse] = set()

    def add(self, ws: web.WebSocketResponse) -> None:
        self._sockets.add(ws)

    def remove(self, ws: web.WebSocketResponse) -> None:
        self._sockets.discard(ws)

    async def broadcast(self, event: str, data: Any) -> None:
        """Envía un evento a todos los clientes WebSocket conectados."""
        if not self._sockets:
            return
        payload = json.dumps({"event": event, "data": data, "ts": time.time()})
        dead: list[web.WebSocketResponse] = []
        for ws in self._sockets:
            if ws.closed:
                dead.append(ws)
                continue
            try:
                await ws.send_str(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self._sockets.discard(ws)

    @property
    def count(self) -> int:
        return len(self._sockets)


class StatusProvider:
    """Recolecta el estado de todos los subsistemas del bot.

    Se pasan referencias débiles a los objetos vivos; StatusProvider solo
    los lee, nunca los modifica.
    """

    def __init__(
        self,
        arbiter: "GameArbiter",
        state: "JsonStore",
        history: "ChatHistory",
        lol: "LolClient | None" = None,
        valorant: "ValorantClient | None" = None,
        broadcaster: WSBroadcaster | None = None,
    ) -> None:
        self.arbiter = arbiter
        self.state = state
        self.history = history
        self.lol = lol
        self.valorant = valorant
        self.broadcaster = broadcaster or WSBroadcaster()

        # Flags de subsistemas activos, seteados por run.py después de
        # arrancar cada subsistema.
        self.chat_active = False
        self.discord_active = False
        self.agent_active = False
        self.clips_active = False
        self.llm_active = False
        self.db_active = False
        self.embeddings_active = False
        self.bot_login: str = ""
        self.channel: str = ""
        self.start_time: float = time.time()

    def snapshot(self) -> dict[str, Any]:
        """Devuelve el estado completo del bot como dict JSON-serializable."""
        sources_raw = self.arbiter.sources()
        sources = {}
        for name, report in sources_raw.items():
            sources[name] = {
                "game": report.game,
                "match_id": report.match_id,
                "stale": report.stale,
                "age_seconds": round(time.monotonic() - report.at, 1),
                "stale_after": report.stale_after,
            }

        # Juego ganador (el que se aplica en Twitch)
        live = [(s, r) for s, r in sources_raw.items() if not r.stale]
        winner_source, winner_game = None, None
        if live:
            from ..gamesource import PRIORITY
            winner_source, winner_report = max(
                live, key=lambda item: PRIORITY.get(item[0], 0)
            )
            winner_game = winner_report.game

        # Prediction activa
        active_pred = self.state.get("prediction_active")
        prediction = None
        if active_pred:
            prediction = {
                "match_key": active_pred.get("match_key", ""),
                "prediction_id": active_pred.get("prediction_id", ""),
                "game": active_pred.get("game", ""),
                "outcomes": active_pred.get("outcomes", {}),
            }

        # Historial de chat reciente (últimos 20)
        recent = self.history.since(3600)
        chat_recent = [
            {
                "author": e.display_name,
                "text": e.text,
                "ts": e.created_at,
            }
            for e in recent[-20:]
        ]

        # Riot LoL
        lol_status = {
            "configured": bool(self.lol and self.lol.configured),
            "key_dead": bool(self.lol and self.lol._key_dead),  # noqa: SLF001
        }

        # Valorant (HenrikDev configurado)
        from ..config import cfg
        val_status = {
            "configured": bool(cfg.henrik_api_key and cfg.valorant_id),
        }

        uptime_seconds = int(time.time() - self.start_time)

        return {
            "channel": self.channel,
            "bot_login": self.bot_login,
            "uptime_seconds": uptime_seconds,
            "current_game": winner_game,
            "current_source": winner_source,
            "autocat_enabled": self.arbiter.enabled,
            "sources": sources,
            "prediction": prediction,
            "chat_recent": chat_recent,
            "chat_total_in_memory": len(list(self.history.since(86400))),
            "subsystems": {
                "chat_bot": self.chat_active,
                "discord_presence": self.discord_active,
                "agent": self.agent_active,
                "clips_pipeline": self.clips_active,
                "llm_summary": self.llm_active,
                "database": self.db_active,
                "embeddings": self.embeddings_active,
            },
            "riot": {
                "lol": lol_status,
                "valorant": val_status,
            },
            "ws_clients": self.broadcaster.count,
            "snapshot_at": time.time(),
        }
