"""Tests para src/category.py

Naming: [MethodName]_[Scenario/State]_[ExpectedResult]

Estrategia de aislamiento
--------------------------
* ``cfg`` se parchea vía ``unittest.mock.patch`` en el fixture ``mock_resolver``
  (definido en conftest.py) para que los tests no lean el .env real.
* ``Helix`` se reemplaza con AsyncMock en ``mock_helix`` (conftest.py).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.category import CategoryResolver, _norm


# ===========================================================================
# _norm (función interna)
# ===========================================================================

class TestNorm:

    def test_norm_WithLowercase_ReturnsUnchanged(self):
        assert _norm("league") == "league"

    def test_norm_WithUppercase_ConvertsToLowercase(self):
        assert _norm("VALORANT") == "valorant"

    def test_norm_WithAccents_StripsDiacritics(self):
        assert _norm("Léague") == "league"
        assert _norm("ñoño") == "nono"

    def test_norm_WithPunctuation_RemovesSpecialChars(self):
        assert _norm("Counter-Strike 2") == "counterstrike2"

    def test_norm_WithMixedInput_NormalizesCompletely(self):
        assert _norm("  Team-Fight Táctícs  ") == "teamfighttactics"

    def test_norm_WithNumbers_KeepsNumbers(self):
        assert _norm("FIFA 24") == "fifa24"

    def test_norm_WithEmptyString_ReturnsEmpty(self):
        assert _norm("") == ""


# ===========================================================================
# CategoryResolver
# ===========================================================================

@pytest.fixture()
def resolver(mock_helix, mock_state, tmp_path):
    """CategoryResolver con game_map mínimo sin tocar el .env real."""
    game_map = tmp_path / "game_map.json"
    game_map.write_text(
        json.dumps({
            "map": {
                "counter-strike 2": "Counter-Strike",
                "tft": "Teamfight Tactics",
            },
            "ignore": ["spotify", "obs studio", "visual studio code"],
        }),
        encoding="utf-8",
    )
    with patch("src.category.cfg") as mock_cfg:
        mock_cfg.game_map_file = game_map
        mock_cfg.autocat_dry_run = False
        yield CategoryResolver(helix=mock_helix, state=mock_state)


class TestCategoryResolverIsIgnored:

    def test_isIgnored_WhenInIgnoreList_ReturnsTrue(self, resolver):
        assert resolver.is_ignored("Spotify") is True

    def test_isIgnored_WhenInIgnoreListWithDifferentCase_ReturnsTrue(self, resolver):
        assert resolver.is_ignored("OBS STUDIO") is True

    def test_isIgnored_WhenNotInIgnoreList_ReturnsFalse(self, resolver):
        assert resolver.is_ignored("League of Legends") is False

    def test_isIgnored_WithEmptyString_ReturnsFalse(self, resolver):
        assert resolver.is_ignored("") is False


class TestCategoryResolverResolve:

    async def test_resolve_WhenNameIsEmpty_ReturnsNone(self, resolver):
        result = await resolver.resolve("")
        assert result is None

    async def test_resolve_WhenInIgnoreList_ReturnsNoneWithoutCallingHelix(
        self, resolver, mock_helix
    ):
        result = await resolver.resolve("Spotify")
        assert result is None
        mock_helix.get_game_by_name.assert_not_called()
        mock_helix.search_categories.assert_not_called()

    async def test_resolve_WhenExactMatchFromHelix_ReturnsGame(
        self, resolver, mock_helix
    ):
        mock_helix.get_game_by_name.return_value = {"id": 21779, "name": "League of Legends"}
        result = await resolver.resolve("League of Legends")
        assert result == {"id": "21779", "name": "League of Legends"}

    async def test_resolve_WhenCachedResult_DoesNotCallHelix(
        self, resolver, mock_helix, mock_state
    ):
        # Pre-cargar el cache con un resultado conocido
        await mock_state.update(
            "game_cache",
            {"leagueoflegends": {"id": "21779", "name": "League of Legends"}},
        )
        result = await resolver.resolve("League of Legends")
        assert result is not None
        mock_helix.get_game_by_name.assert_not_called()

    async def test_resolve_WhenCachedAsFalse_ReturnsFalsyWithoutCallingHelix(
        self, resolver, mock_helix, mock_state
    ):
        """False en caché significa que ya sabemos que no resuelve."""
        await mock_state.update("game_cache", {"somegame": False})
        result = await resolver.resolve("SomeGame")
        assert result is None
        mock_helix.get_game_by_name.assert_not_called()

    async def test_resolve_WhenSearchReturnsBelowThreshold_ReturnsNone(
        self, resolver, mock_helix
    ):
        mock_helix.get_game_by_name.return_value = None
        # Devuelve candidato muy diferente al input
        mock_helix.search_categories.return_value = [
            {"id": 99, "name": "Completely Different Game"}
        ]
        result = await resolver.resolve("LoL")
        assert result is None

    async def test_resolve_WhenSearchReturnsAboveThreshold_ReturnsGame(
        self, resolver, mock_helix
    ):
        mock_helix.get_game_by_name.return_value = None
        # Nombre muy similar al buscado → score ≥ 0.82
        mock_helix.search_categories.return_value = [
            {"id": 21779, "name": "League of Legends"}
        ]
        result = await resolver.resolve("league of legends")
        assert result is not None
        assert result["name"] == "League of Legends"

    async def test_resolve_WhenManualMapHit_UsesAliasAsTarget(
        self, resolver, mock_helix
    ):
        """'counter-strike 2' mapea a 'Counter-Strike' vía game_map."""
        mock_helix.get_game_by_name.return_value = {"id": 32399, "name": "Counter-Strike"}
        result = await resolver.resolve("Counter-Strike 2")
        # Helix debe haberse llamado con el alias del mapa, no el nombre original
        mock_helix.get_game_by_name.assert_called_once_with("Counter-Strike")
        assert result == {"id": "32399", "name": "Counter-Strike"}

    async def test_resolve_WhenHelixReturnsEmptyList_ReturnsNone(
        self, resolver, mock_helix
    ):
        mock_helix.get_game_by_name.return_value = None
        mock_helix.search_categories.return_value = []
        result = await resolver.resolve("NonExistentGame")
        assert result is None


class TestCategoryResolverApply:

    async def test_apply_WhenSameGameId_ReturnsFalseWithoutPatching(
        self, resolver, mock_helix
    ):
        mock_helix.get_channel.return_value = {"game_id": "21779"}
        game = {"id": "21779", "name": "League of Legends"}
        changed = await resolver.apply("broadcaster_id", game)
        assert changed is False
        mock_helix.modify_channel.assert_not_called()

    async def test_apply_WhenDifferentGameId_CallsModifyAndReturnsTrue(
        self, resolver, mock_helix
    ):
        mock_helix.get_channel.return_value = {"game_id": "99999"}
        game = {"id": "21779", "name": "League of Legends"}
        changed = await resolver.apply("broadcaster_id", game)
        assert changed is True
        mock_helix.modify_channel.assert_called_once_with("broadcaster_id", game_id="21779")

    async def test_apply_WhenDryRun_LogsButDoesNotPatch(
        self, mock_state, tmp_path
    ):
        from unittest.mock import AsyncMock, MagicMock, patch as mpatch
        fresh_helix = MagicMock()
        fresh_helix.get_channel = AsyncMock(return_value={"game_id": "99999"})
        fresh_helix.modify_channel = AsyncMock()

        game_map = tmp_path / "game_map.json"
        game_map.write_text(json.dumps({"map": {}, "ignore": []}), encoding="utf-8")

        # El cfg que lee apply() es el singleton de nivel de módulo
        with mpatch("src.category.cfg") as mock_cfg:
            mock_cfg.game_map_file = game_map
            mock_cfg.autocat_dry_run = True
            resolver_dry = CategoryResolver(helix=fresh_helix, state=mock_state)
            game = {"id": "21779", "name": "League of Legends"}
            changed = await resolver_dry.apply("broadcaster_id", game)

        assert changed is True
        fresh_helix.modify_channel.assert_not_called()

    async def test_apply_WhenChannelIsNone_StillCallsModify(
        self, resolver, mock_helix
    ):
        mock_helix.get_channel.return_value = None
        game = {"id": "21779", "name": "League of Legends"}
        changed = await resolver.apply("broadcaster_id", game)
        assert changed is True
        mock_helix.modify_channel.assert_called_once()


class TestCategoryResolverReloadMap:

    def test_reloadMap_WhenFileMissing_KeepsEmptyMap(self, resolver, tmp_path):
        with patch("src.category.cfg") as mock_cfg:
            mock_cfg.game_map_file = tmp_path / "nonexistent.json"
            resolver.reload_map()
        # No lanza excepción y el mapa queda vacío
        assert resolver._map == {}
        assert resolver._ignore == set()
