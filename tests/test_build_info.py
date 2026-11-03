"""Tests for the build identification: which code the running bot is.

The code is copied into the Docker image, so changes only run after a rebuild.
Showing the commit in /health and on the dashboard makes an old image obvious.

Naming: [MethodName]_[Scenario/State]_[ExpectedResult]
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from aiohttp.test_utils import make_mocked_request

from src.agent_server import AgentServer
from src.chat_history import ChatHistory
from src.dashboard.status import StatusProvider
from src.storage import JsonStore


class TestBuildInfo:

    def test_buildInfo_WhenImageHasCommit_ReturnsIt(self, monkeypatch):
        from src.build_info import build_info

        monkeypatch.setenv("BOT_COMMIT", "46eeb2b")
        monkeypatch.setenv("BOT_BUILT_AT", "2026-11-02T04:31:00Z")

        assert build_info() == {"commit": "46eeb2b", "built_at": "2026-11-02T04:31:00Z"}


class TestBuildExposed:

    async def test_handleHealth_IncludesBuildCommit(self, monkeypatch):
        monkeypatch.setenv("BOT_COMMIT", "46eeb2b")
        request = make_mocked_request("GET", "/health")

        response = await AgentServer(None).handle_health(request)

        assert json.loads(response.body)["build"]["commit"] == "46eeb2b"

    def test_snapshot_IncludesBuildCommit(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BOT_COMMIT", "46eeb2b")
        arbiter = MagicMock()
        arbiter.sources.return_value = {}
        arbiter.enabled = True
        provider = StatusProvider(
            arbiter=arbiter,
            state=JsonStore(tmp_path / "state.json"),
            history=ChatHistory(),
        )

        assert provider.snapshot()["build"]["commit"] == "46eeb2b"
