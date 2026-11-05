"""Tests for the live stream and match data the dashboard shows.

Naming: [MethodName]_[Scenario/State]_[ExpectedResult]
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from src.chat_history import ChatHistory
from src.dashboard.status import StatusProvider
from src.gamesource import Report
from src.storage import JsonStore


def _provider(tmp_path, reports: dict[str, Report] | None = None) -> StatusProvider:
    arbiter = MagicMock()
    arbiter.sources.return_value = reports or {}
    arbiter.enabled = True
    return StatusProvider(
        arbiter=arbiter,
        state=JsonStore(tmp_path / "state.json"),
        history=ChatHistory(),
    )


def _agent(metadata: dict | None, match_id: str | None = None, *, stale: bool = False,
           match_since: float | None = None, game: str = "VALORANT") -> Report:
    report = Report(
        game=game,
        at=time.monotonic() - (1000 if stale else 0),
        stale_after=210,
        match_id=match_id,
        metadata=metadata,
        match_since=match_since,
    )
    return report


# ===========================================================================
# Stream: live/offline, title, viewers
# ===========================================================================

class TestStreamStatus:

    def test_snapshot_BeforeFirstCheck_LiveIsUnknown(self, tmp_path):
        assert _provider(tmp_path).snapshot()["stream"] == {"live": None}

    def test_snapshot_WhenLive_ReportsTitleViewersStartAndGame(self, tmp_path):
        provider = _provider(tmp_path)
        provider.update_stream({
            "id": "319324514678",
            "title": "ranked hasta platino",
            "viewer_count": 42,
            "started_at": "2026-11-07T04:00:00Z",
            "game_name": "VALORANT",
        })

        assert provider.snapshot()["stream"] == {
            "live": True,
            "title": "ranked hasta platino",
            "viewers": 42,
            "started_at": "2026-11-07T04:00:00Z",
            "game": "VALORANT",
        }

    def test_snapshot_WhenOffline_ReportsNotLive(self, tmp_path):
        provider = _provider(tmp_path)
        provider.update_stream(None)
        assert provider.snapshot()["stream"] == {"live": False}


# ===========================================================================
# Match: phase, score, queue, start time (from the agent)
# ===========================================================================

class TestMatchStatus:

    def test_snapshot_WhenValorantInGame_ReportsPhaseScoreQueueAndStart(self, tmp_path):
        agent = _agent(
            {"source": "presence", "phase": "in_game", "queue": "competitive",
             "score": {"ally": 7, "enemy": 4}},
            "val-1", match_since=1_000.0,
        )

        match = _provider(tmp_path, {"agent": agent}).snapshot()["match"]

        assert match == {
            "game": "VALORANT",
            "phase": "in_game",
            "queue": "Competitive",
            "score": {"ally": 7, "enemy": 4},
            "match_id": "val-1",
            "since": 1_000.0,
            "last_match": None,
        }

    def test_snapshot_WhenLoLRankedSolo_LabelsTheQueue(self, tmp_path):
        agent = _agent({"source": "lcu", "phase": "in_game", "queue_id": 420}, "55",
                       game="League of Legends")
        match = _provider(tmp_path, {"agent": agent}).snapshot()["match"]
        assert match["queue"] == "Ranked Solo/Duo"

    def test_snapshot_WhenBackInMenus_KeepsTheLastMatch(self, tmp_path):
        last = {"id": "val-1", "score": {"ally": 13, "enemy": 9}}
        agent = _agent({"source": "presence", "phase": "menus", "last_match": last})

        match = _provider(tmp_path, {"agent": agent}).snapshot()["match"]

        assert match["phase"] == "menus"
        assert match["last_match"] == last

    def test_snapshot_WhenAgentIsStale_HasNoMatch(self, tmp_path):
        agent = _agent({"source": "presence", "phase": "in_game"}, "val-1", stale=True)
        assert _provider(tmp_path, {"agent": agent}).snapshot()["match"] is None

    def test_snapshot_WhenAgentHasNoClientData_HasNoMatch(self, tmp_path):
        agent = _agent(None, game="Counter-Strike 2")
        assert _provider(tmp_path, {"agent": agent}).snapshot()["match"] is None


class TestQueueLabel:

    @pytest.mark.parametrize("raw, label", [
        ("competitive", "Competitive"),
        ("unrated", "Unrated"),
        ("swiftplay", "Swiftplay"),
        (420, "Ranked Solo/Duo"),
        (440, "Ranked Flex"),
        (450, "ARAM"),
    ])
    def test_queueLabel_WhenKnown_ReturnsReadableName(self, raw, label):
        from src.dashboard.status import queue_label
        assert queue_label(raw) == label

    def test_queueLabel_WhenUnknown_ReturnsItAsText(self):
        from src.dashboard.status import queue_label
        assert queue_label("newmode") == "newmode"
        assert queue_label(9999) == "Queue 9999"

    def test_queueLabel_WhenMissing_ReturnsNone(self):
        from src.dashboard.status import queue_label
        assert queue_label(None) is None
