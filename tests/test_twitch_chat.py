"""Tests for src/twitch/chat.py — parse_line, ChatMessage properties, RateLimiter.

The full WebSocket client is not tested (it needs the network). Covered:
* Parsing IRCv3 lines with and without tags
* Derived ChatMessage properties (badges, is_mod, is_vip, is_privileged)
* _unescape_tag
* Basic RateLimiter

Naming: [MethodName]_[Scenario/State]_[ExpectedResult]
"""
from __future__ import annotations

import asyncio
import time

import pytest

from src.twitch.chat import ChatMessage, RateLimiter, _unescape_tag, parse_line
from tests.conftest import make_message


# ===========================================================================
# _unescape_tag
# ===========================================================================

class TestUnescapeTag:

    def test_unescape_tag_WithEscapedSpace_ReturnsSpace(self):
        assert _unescape_tag(r"\s") == " "

    def test_unescape_tag_WithEscapedSemicolon_ReturnsSemicolon(self):
        assert _unescape_tag(r"\:") == ";"

    def test_unescape_tag_WithEscapedBackslash_ReturnsBackslash(self):
        assert _unescape_tag("\\\\") == "\\"

    def test_unescape_tag_WithEscapedNewline_ReturnsNewline(self):
        assert _unescape_tag(r"\n") == "\n"

    def test_unescape_tag_WithEscapedCarriageReturn_ReturnsCR(self):
        assert _unescape_tag(r"\r") == "\r"

    def test_unescape_tag_WithNoEscapes_ReturnsUnchanged(self):
        assert _unescape_tag("hello") == "hello"

    def test_unescape_tag_WithMixedContent_UnescapesCorrectly(self):
        result = _unescape_tag(r"hello\sworld")
        assert result == "hello world"

    def test_unescape_tag_WithEmptyString_ReturnsEmpty(self):
        assert _unescape_tag("") == ""


# ===========================================================================
# parse_line
# ===========================================================================

class TestParseLine:

    def test_parseLine_WithPing_ParsesCommand(self):
        tags, prefix, cmd, args, trailing = parse_line("PING :tmi.twitch.tv")
        assert cmd == "PING"
        assert trailing == "tmi.twitch.tv"
        assert tags == {}
        assert prefix == ""

    def test_parseLine_WithPrivmsgAndTags_ParsesAllFields(self):
        line = (
            "@badges=broadcaster/1;mod=0;display-name=ChaarQueen;user-id=467734820 "
            ":chaarqueen!chaarqueen@chaarqueen.tmi.twitch.tv PRIVMSG #chaarqueen :hola chat"
        )
        tags, prefix, cmd, args, trailing = parse_line(line)
        assert cmd == "PRIVMSG"
        assert args == ["#chaarqueen"]
        assert trailing == "hola chat"
        assert tags["display-name"] == "ChaarQueen"
        assert tags["user-id"] == "467734820"

    def test_parseLine_WithTrailingColonInMessage_ParsesTrailing(self):
        """The trailing part may contain ':' in the message."""
        line = ":user!user@user.tmi.twitch.tv PRIVMSG #chan :hello: world"
        _, _, _, _, trailing = parse_line(line)
        assert trailing == "hello: world"

    def test_parseLine_WithTagsEscapedSpaces_UnescapesInTags(self):
        line = r"@emotes=\s :user!user@user.tmi.twitch.tv PRIVMSG #chan :msg"
        tags, _, _, _, _ = parse_line(line)
        assert tags["emotes"] == " "

    def test_parseLine_WithMultipleArgs_ParsesAllArgs(self):
        line = ":server.example.com 353 botnick = #chaarqueen :user1 user2"
        _, _, cmd, args, trailing = parse_line(line)
        assert cmd == "353"
        assert "=" in args or "#chaarqueen" in args
        assert "user1 user2" in (trailing or "")

    def test_parseLine_WithNoPrefix_ParsesCorrectly(self):
        line = "RECONNECT"
        tags, prefix, cmd, args, trailing = parse_line(line)
        assert cmd == "RECONNECT"
        assert prefix == ""
        assert args == []
        assert trailing is None


# ===========================================================================
# ChatMessage properties
# ===========================================================================

class TestChatMessageProperties:

    def test_isMod_WhenModTagIsOne_ReturnsTrue(self):
        msg = make_message(is_mod=True)
        assert msg.is_mod is True

    def test_isMod_WhenModTagIsZero_ReturnsFalse(self):
        msg = make_message(is_mod=False)
        assert msg.is_mod is False

    def test_isMod_WhenBroadcasterBadge_ReturnsTrue(self):
        msg = make_message(is_broadcaster=True)
        assert msg.is_mod is True

    def test_isMod_WhenModeratorBadgeInBadges_ReturnsTrue(self):
        msg = ChatMessage(
            channel="ch",
            author="a",
            author_id="1",
            display_name="A",
            text="hi",
            message_id="m",
            tags={"badges": "moderator/1", "mod": "0"},
        )
        assert msg.is_mod is True

    def test_isVip_WhenVipBadge_ReturnsTrue(self):
        msg = make_message(is_vip=True)
        assert msg.is_vip is True

    def test_isVip_WhenNoBadge_ReturnsFalse(self):
        msg = make_message(is_vip=False)
        assert msg.is_vip is False

    def test_isPrivileged_WhenMod_ReturnsTrue(self):
        msg = make_message(is_mod=True)
        assert msg.is_privileged is True

    def test_isPrivileged_WhenVipNotMod_ReturnsTrue(self):
        msg = make_message(is_vip=True, is_mod=False)
        assert msg.is_privileged is True

    def test_isPrivileged_WhenViewer_ReturnsFalse(self):
        msg = make_message(is_mod=False, is_vip=False)
        assert msg.is_privileged is False

    def test_isBroadcaster_WhenBroadcasterBadge_ReturnsTrue(self):
        msg = make_message(is_broadcaster=True)
        assert msg.is_broadcaster is True

    def test_badges_WithMultipleBadges_ReturnsCorrectSet(self):
        msg = ChatMessage(
            channel="ch",
            author="a",
            author_id="1",
            display_name="A",
            text="hi",
            message_id="m",
            tags={"badges": "broadcaster/1,subscriber/12,vip/1"},
        )
        assert "broadcaster" in msg.badges
        assert "subscriber" in msg.badges
        assert "vip" in msg.badges

    def test_badges_WhenNoBadgesTag_ReturnsEmptySet(self):
        msg = ChatMessage(
            channel="ch",
            author="a",
            author_id="1",
            display_name="A",
            text="hi",
            message_id="m",
            tags={},
        )
        assert msg.badges == set()


# ===========================================================================
# RateLimiter
# ===========================================================================

class TestRateLimiter:

    async def test_RateLimiter_acquire_BelowLimit_AcquiresImmediately(self):
        rl = RateLimiter(limit=5, window=30.0)
        start = time.monotonic()
        for _ in range(5):
            await rl.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.5, "Should acquire without waiting"

    async def test_RateLimiter_acquire_AtLimit_WaitsForWindow(self):
        """Once the bucket is full, the next acquisition must wait.

        A very short window (0.1s) keeps the tests fast.
        """
        rl = RateLimiter(limit=2, window=0.1)
        await rl.acquire()
        await rl.acquire()
        start = time.monotonic()
        # This third acquisition has to wait until the window expires
        await asyncio.wait_for(rl.acquire(), timeout=1.0)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.05, "Should have waited for part of the window"

    def test_RateLimiter_configure_UpdatesLimitAndWindow(self):
        rl = RateLimiter(limit=5, window=30.0)
        rl.configure(100, 60.0)
        assert rl.limit == 100
        assert rl.window == 60.0


# ===========================================================================
# ChatClient._handle
# ===========================================================================

class TestChatClientHandle:

    async def test_handle_WhenMessageHandlerIsSlow_DoesNotBlockReading(self):
        # A long command (!resumen calls an LLM) must not freeze the IRC loop:
        # PINGs and other messages keep being read meanwhile.
        from unittest.mock import MagicMock
        from src.twitch.chat import ChatClient

        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_handler(message: ChatMessage) -> None:
            started.set()
            await release.wait()

        client = ChatClient(MagicMock(), MagicMock(), "botducky", "chaarqueen", slow_handler)
        line = "@id=m1;user-id=2 :viewer!viewer@viewer.tmi.twitch.tv PRIVMSG #chaarqueen :!resumen"

        await asyncio.wait_for(client._handle(line), timeout=1)
        await asyncio.wait_for(started.wait(), timeout=1)
        release.set()
        await asyncio.sleep(0)
