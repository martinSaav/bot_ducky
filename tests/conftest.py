"""Fixtures shared by the whole test suite.

Isolation rules
---------------
* The real .env is never read: environment variables are injected with
  ``monkeypatch.setenv`` or through the helpers in this module.
* External dependencies (Helix, JsonStore, ChatClient, etc.) are always
  passed as MagicMock / AsyncMock so no test touches the network.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.chat_history import ChatHistory
from src.gamesource import GameArbiter
from src.twitch.chat import ChatMessage


# ---------------------------------------------------------------------------
# Message factory helpers
# ---------------------------------------------------------------------------

def make_message(
    text: str = "hola",
    author: str = "viewer1",
    author_id: str = "111",
    display_name: str = "Viewer1",
    *,
    is_mod: bool = False,
    is_vip: bool = False,
    is_broadcaster: bool = False,
    message_id: str = "msg-001",
    channel: str = "chaarqueen",
) -> ChatMessage:
    """Builds a ChatMessage ready to use in tests."""
    badges_parts: list[str] = []
    if is_broadcaster:
        badges_parts.append("broadcaster/1")
    if is_vip:
        badges_parts.append("vip/1")
    tags: dict[str, str] = {
        "badges": ",".join(badges_parts),
        "mod": "1" if is_mod else "0",
    }
    return ChatMessage(
        channel=channel,
        author=author,
        author_id=author_id,
        display_name=display_name,
        text=text,
        message_id=message_id,
        tags=tags,
    )


# ---------------------------------------------------------------------------
# Infrastructure fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_store(tmp_path: Path):
    """JsonStore backed by a temporary file (deleted afterwards)."""
    from src.storage import JsonStore
    return JsonStore(tmp_path / "test_store.json")


@pytest.fixture()
def history() -> ChatHistory:
    return ChatHistory(ignored_authors={"nightbot"})


@pytest.fixture()
def mock_helix() -> MagicMock:
    helix = MagicMock()
    helix.get_game_by_name = AsyncMock(return_value=None)
    helix.search_categories = AsyncMock(return_value=[])
    helix.get_channel = AsyncMock(return_value=None)
    helix.modify_channel = AsyncMock(return_value=None)
    helix.get_stream = AsyncMock(return_value=None)
    helix.create_clip = AsyncMock(return_value=None)
    return helix


@pytest.fixture()
def mock_state(tmp_path: Path):
    """Empty JsonStore for the bot state."""
    from src.storage import JsonStore
    return JsonStore(tmp_path / "state.json")


@pytest.fixture()
def mock_resolver(mock_helix, mock_state, tmp_path):
    """CategoryResolver with a minimal game_map.json and a mocked Helix."""
    import json
    game_map = tmp_path / "game_map.json"
    game_map.write_text(
        json.dumps({"map": {"counter-strike 2": "Counter-Strike"}, "ignore": ["spotify", "obs studio"]}),
        encoding="utf-8",
    )
    with patch("src.category.cfg") as mock_cfg:
        mock_cfg.game_map_file = game_map
        mock_cfg.autocat_dry_run = False
        from src.category import CategoryResolver
        resolver = CategoryResolver(helix=mock_helix, state=mock_state)
    return resolver


@pytest.fixture()
def mock_arbiter(mock_helix, mock_resolver, mock_state) -> GameArbiter:
    """GameArbiter with every dependency mocked."""
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
            broadcaster_id="467734820",
            state=mock_state,
            lol=None,
            valorant=None,
            announce=None,
        )
    arbiter.enabled = True
    return arbiter


@pytest.fixture()
def mock_services(history, mock_helix, mock_state, mock_arbiter, mock_resolver):
    """Minimal Services mock for Registry tests."""
    import aiohttp
    from src.services import Services
    from src.twitch.auth import TwitchAuth

    auth = MagicMock(spec=TwitchAuth)
    svc = MagicMock(spec=Services)
    svc.history = history
    svc.helix = mock_helix
    svc.state = mock_state
    svc.arbiter = mock_arbiter
    svc.resolver = mock_resolver
    svc.chat = AsyncMock()
    svc.chat.say = AsyncMock()
    svc.lol = MagicMock()
    svc.lol.configured = False
    svc.valorant = MagicMock()
    svc.valorant.configured = False
    svc.database_history = None
    svc.embeddings = None
    svc.llm_summary = None
    svc.broadcaster_id = "467734820"
    return svc
