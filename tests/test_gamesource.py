"""Tests for src/gamesource.py — Report, GameArbiter.

Naming: [MethodName]_[Scenario/State]_[ExpectedResult]

Isolation strategy
------------------
* ``cfg`` is patched in the ``mock_arbiter`` fixture (conftest.py).
* Helix and CategoryResolver are replaced with mocks.
* Twitch prediction logic is not tested here (see test_gamesource_predictions.py
  and test_predictions_local.py).
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.gamesource import GameArbiter, Report


# ===========================================================================
# Report.stale
# ===========================================================================

class TestReport:

    def test_stale_WhenStaleAfterIsNone_AlwaysReturnsFalse(self):
        r = Report(game="LoL", at=time.monotonic() - 9999, stale_after=None)
        assert r.stale is False

    def test_stale_WhenFreshReport_ReturnsFalse(self):
        r = Report(game="LoL", at=time.monotonic(), stale_after=300)
        assert r.stale is False

    def test_stale_WhenExpiredReport_ReturnsTrue(self):
        # Simulate a report made 500s ago that expires after 300s
        r = Report(game="LoL", at=time.monotonic() - 500, stale_after=300)
        assert r.stale is True

    def test_stale_WhenJustOnBoundary_ReturnsTrue(self):
        """stale_after=0 → always expired."""
        r = Report(game="LoL", at=time.monotonic() - 1, stale_after=0)
        assert r.stale is True

    def test_stale_WhenGameIsNone_BehaviorBasedOnTime(self):
        """game=None does not affect the stale calculation."""
        fresh = Report(game=None, at=time.monotonic(), stale_after=300)
        assert fresh.stale is False
        old = Report(game=None, at=time.monotonic() - 500, stale_after=300)
        assert old.stale is True


# ===========================================================================
# GameArbiter._winner
# ===========================================================================

class TestGameArbiterWinner:

    def _make_arbiter(self, mock_helix, mock_resolver, mock_state) -> GameArbiter:
        with patch("src.gamesource.cfg") as mock_cfg:
            mock_cfg.autocat_enabled = True
            mock_cfg.agent_debounce = 15
            mock_cfg.presence_debounce = 45
            mock_cfg.autocat_only_when_live = False
            mock_cfg.autocat_idle_category = "Just Chatting"
            mock_cfg.autocat_announce = True
            mock_cfg.twitch_channel = "chaarqueen"
            mock_cfg.autocat_dry_run = False
            arbiter = GameArbiter(
                helix=mock_helix,
                resolver=mock_resolver,
                broadcaster_id="123",
                state=mock_state,
            )
        return arbiter

    def test_winner_WhenNoSources_ReturnsNoneNone(self, mock_helix, mock_resolver, mock_state):
        arbiter = self._make_arbiter(mock_helix, mock_resolver, mock_state)
        source, game = arbiter._winner()
        assert source is None
        assert game is None

    def test_winner_WhenOnlyDiscord_ReturnsDiscord(self, mock_helix, mock_resolver, mock_state):
        arbiter = self._make_arbiter(mock_helix, mock_resolver, mock_state)
        arbiter._reports["discord"] = Report(
            game="League of Legends", at=time.monotonic(), stale_after=300
        )
        source, game = arbiter._winner()
        assert source == "discord"
        assert game == "League of Legends"

    def test_winner_WhenAgentAndDiscordBothActive_PrefersAgent(
        self, mock_helix, mock_resolver, mock_state
    ):
        arbiter = self._make_arbiter(mock_helix, mock_resolver, mock_state)
        arbiter._reports["discord"] = Report(
            game="League of Legends", at=time.monotonic(), stale_after=300
        )
        arbiter._reports["agent"] = Report(
            game="VALORANT", at=time.monotonic(), stale_after=300
        )
        source, game = arbiter._winner()
        assert source == "agent"
        assert game == "VALORANT"

    def test_winner_WhenAgentStale_FallsBackToDiscord(
        self, mock_helix, mock_resolver, mock_state
    ):
        arbiter = self._make_arbiter(mock_helix, mock_resolver, mock_state)
        arbiter._reports["agent"] = Report(
            game="VALORANT", at=time.monotonic() - 9999, stale_after=300  # expirado
        )
        arbiter._reports["discord"] = Report(
            game="League of Legends", at=time.monotonic(), stale_after=300
        )
        source, game = arbiter._winner()
        assert source == "discord"
        assert game == "League of Legends"

    def test_winner_WhenAllSourcesStale_ReturnsNoneNone(
        self, mock_helix, mock_resolver, mock_state
    ):
        arbiter = self._make_arbiter(mock_helix, mock_resolver, mock_state)
        arbiter._reports["discord"] = Report(
            game="LoL", at=time.monotonic() - 9999, stale_after=300
        )
        arbiter._reports["agent"] = Report(
            game="VAL", at=time.monotonic() - 9999, stale_after=300
        )
        source, game = arbiter._winner()
        assert source is None
        assert game is None

    def test_winner_WhenSourceReportsNone_ReturnsNoneGame(
        self, mock_helix, mock_resolver, mock_state
    ):
        arbiter = self._make_arbiter(mock_helix, mock_resolver, mock_state)
        arbiter._reports["discord"] = Report(
            game=None, at=time.monotonic(), stale_after=300
        )
        source, game = arbiter._winner()
        assert source == "discord"
        assert game is None


# ===========================================================================
# GameArbiter.note_manual_change
# ===========================================================================

class TestGameArbiterNoteManualChange:

    def test_noteManualChange_Always_UpdatesLastApplied(
        self, mock_helix, mock_resolver, mock_state
    ):
        with patch("src.gamesource.cfg") as mock_cfg:
            mock_cfg.autocat_enabled = True
            mock_cfg.agent_debounce = 15
            mock_cfg.presence_debounce = 45
            mock_cfg.autocat_only_when_live = False
            mock_cfg.autocat_idle_category = "Just Chatting"
            mock_cfg.autocat_announce = True
            mock_cfg.twitch_channel = "chaarqueen"
            mock_cfg.autocat_dry_run = False
            arbiter = GameArbiter(
                helix=mock_helix,
                resolver=mock_resolver,
                broadcaster_id="123",
                state=mock_state,
            )
        assert arbiter._last_applied is None
        arbiter.note_manual_change("Just Chatting")
        assert arbiter._last_applied == "Just Chatting"

    def test_noteManualChange_CalledTwice_UpdatesToLatest(
        self, mock_helix, mock_resolver, mock_state
    ):
        with patch("src.gamesource.cfg") as mock_cfg:
            mock_cfg.autocat_enabled = True
            mock_cfg.agent_debounce = 15
            mock_cfg.presence_debounce = 45
            mock_cfg.autocat_only_when_live = False
            mock_cfg.autocat_idle_category = "Just Chatting"
            mock_cfg.autocat_announce = True
            mock_cfg.twitch_channel = "chaarqueen"
            mock_cfg.autocat_dry_run = False
            arbiter = GameArbiter(
                helix=mock_helix,
                resolver=mock_resolver,
                broadcaster_id="123",
                state=mock_state,
            )
        arbiter.note_manual_change("League of Legends")
        arbiter.note_manual_change("VALORANT")
        assert arbiter._last_applied == "VALORANT"


# ===========================================================================
# GameArbiter.sources
# ===========================================================================

class TestGameArbiterSources:

    def test_sources_WhenEmpty_ReturnsEmptyDict(
        self, mock_helix, mock_resolver, mock_state
    ):
        with patch("src.gamesource.cfg") as mock_cfg:
            mock_cfg.autocat_enabled = True
            mock_cfg.agent_debounce = 15
            mock_cfg.presence_debounce = 45
            mock_cfg.autocat_only_when_live = False
            mock_cfg.autocat_idle_category = "Just Chatting"
            mock_cfg.autocat_announce = True
            mock_cfg.twitch_channel = "chaarqueen"
            mock_cfg.autocat_dry_run = False
            arbiter = GameArbiter(
                helix=mock_helix,
                resolver=mock_resolver,
                broadcaster_id="123",
                state=mock_state,
            )
        assert arbiter.sources() == {}

    def test_sources_AfterReport_ReturnsACopy(
        self, mock_helix, mock_resolver, mock_state
    ):
        with patch("src.gamesource.cfg") as mock_cfg:
            mock_cfg.autocat_enabled = True
            mock_cfg.agent_debounce = 15
            mock_cfg.presence_debounce = 45
            mock_cfg.autocat_only_when_live = False
            mock_cfg.autocat_idle_category = "Just Chatting"
            mock_cfg.autocat_announce = True
            mock_cfg.twitch_channel = "chaarqueen"
            mock_cfg.autocat_dry_run = False
            arbiter = GameArbiter(
                helix=mock_helix,
                resolver=mock_resolver,
                broadcaster_id="123",
                state=mock_state,
            )
        arbiter._reports["discord"] = Report(game="LoL", at=time.monotonic(), stale_after=300)
        snapshot = arbiter.sources()
        # Mutating the snapshot does not affect the internal state
        del snapshot["discord"]
        assert "discord" in arbiter._reports
