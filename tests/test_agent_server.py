"""Tests for src/agent_server.py — authentication and the dashboard route.

Naming: [MethodName]_[Scenario/State]_[ExpectedResult]
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from src import agent_server
from src.agent_server import AgentServer


@pytest.fixture()
def dist(tmp_path: Path, monkeypatch) -> Path:
    """Fake Angular build, with a secret file next to it."""
    folder = tmp_path / "dist"
    folder.mkdir()
    (folder / "index.html").write_text("<html>dashboard</html>", encoding="utf-8")
    (folder / "main.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "tokens.json").write_text('{"secret": true}', encoding="utf-8")
    monkeypatch.setattr(agent_server, "_DIST_DIR", folder)
    return folder


def _served(response: web.StreamResponse) -> Path | None:
    return Path(response._path) if isinstance(response, web.FileResponse) else None


class TestDashboardRoute:

    async def test_handleDashboard_WhenFileExists_ServesIt(self, dist):
        request = make_mocked_request("GET", "/dashboard/main.js", match_info={"tail": "main.js"})
        response = await AgentServer(None).handle_dashboard(request)
        assert _served(response) == dist / "main.js"

    async def test_handleDashboard_WhenPathEscapesTheBuild_DoesNotServeIt(self, dist):
        tail = "../tokens.json"
        request = make_mocked_request("GET", "/dashboard/x", match_info={"tail": tail})

        response = await AgentServer(None).handle_dashboard(request)

        served = _served(response)
        assert served is None or served.resolve().is_relative_to(dist.resolve())

    async def test_handleDashboard_WhenAbsolutePath_DoesNotServeIt(self, dist):
        secret = dist.parent / "tokens.json"
        request = make_mocked_request("GET", "/dashboard/x", match_info={"tail": str(secret)})

        response = await AgentServer(None).handle_dashboard(request)

        served = _served(response)
        assert served is None or served.resolve().is_relative_to(dist.resolve())


class TestAuthorization:

    def test_authorized_WhenAgentTokenIsEmpty_RejectsEmptyBearer(self):
        request = make_mocked_request("POST", "/agent/logs", headers={"Authorization": "Bearer "})
        with patch("src.agent_server.cfg") as cfg:
            cfg.agent_token = ""
            assert AgentServer(None)._authorized(request) is False

    def test_authorized_WhenTokenMatches_Accepts(self):
        request = make_mocked_request("POST", "/game", headers={"Authorization": "Bearer s3cret"})
        with patch("src.agent_server.cfg") as cfg:
            cfg.agent_token = "s3cret"
            assert AgentServer(None)._authorized(request) is True

    async def test_handleGame_WhenNoArbiter_Returns503(self):
        request = make_mocked_request("POST", "/game", headers={"Authorization": "Bearer s3cret"})
        with patch("src.agent_server.cfg") as cfg:
            cfg.agent_token = "s3cret"
            response = await AgentServer(None).handle_game(request)
        assert response.status == 503


# ===========================================================================
# Dashboard API: DASHBOARD_TOKEN
# ===========================================================================

def _dashboard_server() -> AgentServer:
    provider = MagicMock()
    provider.snapshot.return_value = {"channel": "chaarqueen"}
    return AgentServer(None, status_provider=provider)


class TestDashboardToken:
    """/api/* and /ws expose the chat and the logs: with DASHBOARD_TOKEN set
    they need it, as a Bearer header or as ?token= (WebSockets cannot send
    headers from the browser)."""

    async def test_handleStatus_WhenTokenSetAndMissing_Returns401(self):
        request = make_mocked_request("GET", "/api/status")
        with patch("src.agent_server.cfg") as cfg:
            cfg.dashboard_token = "dash"
            response = await _dashboard_server().handle_status(request)
        assert response.status == 401

    async def test_handleStatus_WhenBearerMatches_Returns200(self):
        request = make_mocked_request("GET", "/api/status", headers={"Authorization": "Bearer dash"})
        with patch("src.agent_server.cfg") as cfg:
            cfg.dashboard_token = "dash"
            response = await _dashboard_server().handle_status(request)
        assert response.status == 200

    async def test_handleStatus_WhenQueryTokenMatches_Returns200(self):
        request = make_mocked_request("GET", "/api/status?token=dash")
        with patch("src.agent_server.cfg") as cfg:
            cfg.dashboard_token = "dash"
            response = await _dashboard_server().handle_status(request)
        assert response.status == 200

    async def test_handleStatus_WhenTokenWrong_Returns401(self):
        request = make_mocked_request("GET", "/api/status?token=nope")
        with patch("src.agent_server.cfg") as cfg:
            cfg.dashboard_token = "dash"
            response = await _dashboard_server().handle_status(request)
        assert response.status == 401

    async def test_handleLogs_WhenTokenSetAndMissing_Returns401(self):
        request = make_mocked_request("GET", "/api/logs?source=bot")
        with patch("src.agent_server.cfg") as cfg:
            cfg.dashboard_token = "dash"
            response = await _dashboard_server().handle_logs(request)
        assert response.status == 401

    async def test_handleWebsocket_WhenTokenSetAndMissing_Returns401(self):
        request = make_mocked_request("GET", "/ws")
        with patch("src.agent_server.cfg") as cfg:
            cfg.dashboard_token = "dash"
            response = await _dashboard_server().handle_websocket(request)
        assert response.status == 401

    async def test_handleStatus_WhenNoTokenConfigured_StaysOpen(self):
        request = make_mocked_request("GET", "/api/status")
        with patch("src.agent_server.cfg") as cfg:
            cfg.dashboard_token = ""
            response = await _dashboard_server().handle_status(request)
        assert response.status == 200
