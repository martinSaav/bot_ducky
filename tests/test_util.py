"""Tests para src/util.py

Naming: [MethodName]_[Scenario/State]_[ExpectedResult]
"""
from __future__ import annotations

import time

import pytest

from src.util import TTLCache, fmt_duration, split_riot_id, winrate


# ===========================================================================
# TTLCache
# ===========================================================================

class TestTTLCache:

    def test_TTLCache_get_BeforeExpiry_ReturnsValue(self):
        cache = TTLCache(ttl=60)
        cache.set("key", "value")
        assert cache.get("key") == "value"

    def test_TTLCache_get_AfterExpiry_ReturnsNone(self):
        cache = TTLCache(ttl=0.01)
        cache.set("key", "value")
        time.sleep(0.05)
        assert cache.get("key") is None

    def test_TTLCache_get_WhenKeyMissing_ReturnsNone(self):
        cache = TTLCache(ttl=60)
        assert cache.get("nonexistent") is None

    def test_TTLCache_set_OverExistingKey_UpdatesValue(self):
        cache = TTLCache(ttl=60)
        cache.set("key", "old")
        cache.set("key", "new")
        assert cache.get("key") == "new"

    def test_TTLCache_set_OverExistingKey_RenewsExpiry(self):
        cache = TTLCache(ttl=0.05)
        cache.set("key", "v1")
        time.sleep(0.03)
        cache.set("key", "v2")  # renueva el TTL
        time.sleep(0.03)
        # En total pasaron 0.06s pero el TTL se renovó a los 0.03s
        assert cache.get("key") == "v2"

    def test_TTLCache_clear_Always_EmptiesCache(self):
        cache = TTLCache(ttl=60)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_TTLCache_set_WithFalsyValue_StillStored(self):
        """0, False y [] son valores válidos, no None."""
        cache = TTLCache(ttl=60)
        cache.set("zero", 0)
        cache.set("false", False)
        cache.set("empty_list", [])
        # get() devuelve None solo si la clave no existe o expiró
        # Para valores falsy el método simplemente los retorna tal cual
        assert cache.get("zero") == 0
        assert cache.get("false") is False
        assert cache.get("empty_list") == []


# ===========================================================================
# fmt_duration
# ===========================================================================

class TestFmtDuration:

    def test_fmt_duration_WithZero_ReturnsZeroSeconds(self):
        assert fmt_duration(0) == "0s"

    def test_fmt_duration_WithNegative_TreatsAsZero(self):
        assert fmt_duration(-10) == "0s"

    def test_fmt_duration_WithSeconds_ReturnsSecondsString(self):
        assert fmt_duration(45) == "45s"

    def test_fmt_duration_WithExactMinute_ReturnsMinutesOnly(self):
        assert fmt_duration(60) == "1m 0s"

    def test_fmt_duration_WithMinutes_ReturnsMinutesAndSeconds(self):
        assert fmt_duration(125) == "2m 5s"

    def test_fmt_duration_WithExactHour_ReturnsHoursAndMinutes(self):
        assert fmt_duration(3600) == "1h 0m"

    def test_fmt_duration_WithHours_ReturnsHoursString(self):
        assert fmt_duration(7322) == "2h 2m"

    def test_fmt_duration_WithFloat_TruncatesToInt(self):
        assert fmt_duration(61.9) == "1m 1s"


# ===========================================================================
# split_riot_id
# ===========================================================================

class TestSplitRiotId:

    def test_split_riot_id_WithValidId_ReturnsTuple(self):
        assert split_riot_id("Chaar#LAS") == ("Chaar", "LAS")

    def test_split_riot_id_WithSpaces_StripsWhitespace(self):
        assert split_riot_id("Chaar Queen # LAS") == ("Chaar Queen", "LAS")

    def test_split_riot_id_WithNoHash_ReturnsNone(self):
        assert split_riot_id("ChaarLAS") is None

    def test_split_riot_id_WithEmptyTag_ReturnsNone(self):
        assert split_riot_id("Chaar#") is None

    def test_split_riot_id_WithEmptyName_ReturnsNone(self):
        assert split_riot_id("#LAS") is None

    def test_split_riot_id_WithEmptyString_ReturnsNone(self):
        assert split_riot_id("") is None

    def test_split_riot_id_WithNone_ReturnsNone(self):
        assert split_riot_id(None) is None  # type: ignore[arg-type]

    def test_split_riot_id_WithMultipleHashes_SplitsOnLast(self):
        """Nombre con # interno: parte antes del último # es el nombre."""
        result = split_riot_id("Chaar#Queen#LAS")
        # rpartition en el último #
        assert result == ("Chaar#Queen", "LAS")


# ===========================================================================
# winrate
# ===========================================================================

class TestWinrate:

    def test_winrate_WithWinsAndLosses_ReturnsPercentage(self):
        assert winrate(7, 3) == "70%"

    def test_winrate_WithAllWins_Returns100Percent(self):
        assert winrate(5, 0) == "100%"

    def test_winrate_WithZeroGames_ReturnsDash(self):
        assert winrate(0, 0) == "-"

    def test_winrate_WithOnlyLosses_Returns0Percent(self):
        assert winrate(0, 10) == "0%"

    def test_winrate_WithRoundedValue_RoundsCorrectly(self):
        # 1/3 = 33.33% → "33%"
        assert winrate(1, 2) == "33%"
