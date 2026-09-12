"""Tests para la lógica de predicciones de Twitch en src/gamesource.py.

Cubre los métodos con lógica de negocio real que afectan dinero real:
  - resolve_manual_prediction  (!vwin / !vloss)
  - confirm_valorant_prediction (!vgame)
  - cancel_prediction (!vcancel)
  - _prediction_result (resolución automática de LoL)
  - _forget_finished_prediction (limpieza de predictions cerradas)
  - _prediction_match_key (LoL requiere partida activa; Valorant nunca auto)

Naming: [MethodName]_[Scenario/State]_[ExpectedResult]
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.gamesource import GameArbiter
from src.storage import JsonStore


# ---------------------------------------------------------------------------
# Fixture local: helix con todos los métodos async de predicciones
# ---------------------------------------------------------------------------

@pytest.fixture()
def hx() -> MagicMock:
    """Helix mock con AsyncMock correctos para todos los endpoints de predicciones."""
    helix = MagicMock()
    helix.get_predictions = AsyncMock(return_value=[])
    helix.create_prediction = AsyncMock(return_value={
        "id": "pred-001",
        "outcomes": [
            {"title": "Gana", "id": "outcome-win-id"},
            {"title": "Pierde", "id": "outcome-lose-id"},
        ],
    })
    helix.resolve_prediction = AsyncMock(return_value=None)
    helix.cancel_prediction = AsyncMock(return_value=None)
    helix.get_channel = AsyncMock(return_value=None)
    helix.modify_channel = AsyncMock(return_value=None)
    return helix


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def _make(
    tmp_path: Path,
    helix: MagicMock,
    *,
    lol: MagicMock | None = None,
    state: JsonStore | None = None,
) -> GameArbiter:
    if state is None:
        state = JsonStore(tmp_path / "state.json")
    resolver = MagicMock()

    with patch("src.gamesource.cfg") as cfg:
        cfg.autocat_enabled = True
        cfg.agent_debounce = 15
        cfg.presence_debounce = 45
        cfg.autocat_only_when_live = False
        cfg.autocat_idle_category = "Just Chatting"
        cfg.autocat_announce = False
        cfg.twitch_channel = "chaarqueen"
        cfg.autocat_dry_run = False
        cfg.prediction_enabled = True
        cfg.prediction_window = 300
        arbiter = GameArbiter(
            helix=helix,
            resolver=resolver,
            broadcaster_id="467734820",
            state=state,
            lol=lol,
            valorant=None,
            announce=None,
        )
    return arbiter


def _pred_state() -> dict:
    return {
        "prediction_id": "pred-001",
        "match_key": "lol:12345",
        "game": "league of legends",
        "outcomes": {"gana": "outcome-win-id", "pierde": "outcome-lose-id"},
    }


# ===========================================================================
# resolve_manual_prediction
# ===========================================================================

class TestResolveManualPrediction:

    async def test_resolveManualPrediction_WhenNoPredictionActive_ReturnsNoActivePrediction(
        self, hx, tmp_path
    ):
        arbiter = _make(tmp_path, hx)
        result = await arbiter.resolve_manual_prediction(won=True)
        assert "no hay" in result.lower()
        hx.resolve_prediction.assert_not_called()

    async def test_resolveManualPrediction_WhenWon_CallsHelixWithWinOutcomeId(
        self, hx, tmp_path
    ):
        arbiter = _make(tmp_path, hx)
        await arbiter.state.set("prediction_active", _pred_state())

        result = await arbiter.resolve_manual_prediction(won=True)

        hx.resolve_prediction.assert_called_once_with(
            "467734820",
            prediction_id="pred-001",
            winning_outcome_id="outcome-win-id",
        )
        assert "ganada" in result.lower()

    async def test_resolveManualPrediction_WhenLost_CallsHelixWithLoseOutcomeId(
        self, hx, tmp_path
    ):
        arbiter = _make(tmp_path, hx)
        await arbiter.state.set("prediction_active", _pred_state())

        result = await arbiter.resolve_manual_prediction(won=False)

        hx.resolve_prediction.assert_called_once_with(
            "467734820",
            prediction_id="pred-001",
            winning_outcome_id="outcome-lose-id",
        )
        assert "perdida" in result.lower()

    async def test_resolveManualPrediction_WhenResolved_ClearsStatePredictionActive(
        self, hx, tmp_path
    ):
        arbiter = _make(tmp_path, hx)
        await arbiter.state.set("prediction_active", _pred_state())
        await arbiter.resolve_manual_prediction(won=True)
        assert arbiter.state.get("prediction_active") is None

    async def test_resolveManualPrediction_WhenOutcomeMissing_DoesNotCallHelixAndReturnsError(
        self, hx, tmp_path
    ):
        arbiter = _make(tmp_path, hx)
        bad = {**_pred_state(), "outcomes": {}}
        await arbiter.state.set("prediction_active", bad)

        result = await arbiter.resolve_manual_prediction(won=True)

        hx.resolve_prediction.assert_not_called()
        assert "resultado" in result.lower() or "outcome" in result.lower()


# ===========================================================================
# confirm_valorant_prediction (!vgame)
# ===========================================================================

class TestConfirmValorantPrediction:

    async def test_confirmValorantPrediction_WhenNoActivePrediction_CreatesPredictionAndReturnsOk(
        self, hx, tmp_path
    ):
        arbiter = _make(tmp_path, hx)

        result = await arbiter.confirm_valorant_prediction()

        hx.create_prediction.assert_called_once()
        assert "iniciada" in result.lower()

    async def test_confirmValorantPrediction_WhenCreated_PersistsPredictionActiveInState(
        self, hx, tmp_path
    ):
        arbiter = _make(tmp_path, hx)
        await arbiter.confirm_valorant_prediction()

        pred = arbiter.state.get("prediction_active")
        assert pred is not None
        assert pred["game"] == "valorant"
        assert pred["prediction_id"] == "pred-001"

    async def test_confirmValorantPrediction_WhenCreated_PersistsPredictionSession(
        self, hx, tmp_path
    ):
        """prediction_session es necesario para que !vwin/!vloss funcione."""
        arbiter = _make(tmp_path, hx)
        await arbiter.confirm_valorant_prediction()

        session = arbiter.state.get("prediction_session")
        assert session is not None
        assert session["game"] == "valorant"

    async def test_confirmValorantPrediction_WhenLocalStateHasActivePrediction_ReturnsAlreadyActive(
        self, hx, tmp_path
    ):
        hx.get_predictions.return_value = [{"id": "pred-001", "status": "ACTIVE"}]
        arbiter = _make(tmp_path, hx)
        await arbiter.state.set("prediction_active", {**_pred_state(), "game": "valorant"})

        result = await arbiter.confirm_valorant_prediction()

        hx.create_prediction.assert_not_called()
        assert "activa" in result.lower()

    async def test_confirmValorantPrediction_WhenTwitchHasActivePrediction_ReturnsAlreadyActive(
        self, hx, tmp_path
    ):
        hx.get_predictions.return_value = [{"status": "ACTIVE", "id": "twitch-pred"}]
        arbiter = _make(tmp_path, hx)

        result = await arbiter.confirm_valorant_prediction()

        hx.create_prediction.assert_not_called()
        assert "activa" in result.lower()

    async def test_confirmValorantPrediction_WhenNoState_ReturnsGuardarError(
        self, hx, tmp_path
    ):
        with patch("src.gamesource.cfg") as cfg:
            cfg.autocat_enabled = True
            cfg.agent_debounce = 15
            cfg.presence_debounce = 45
            cfg.autocat_only_when_live = False
            cfg.autocat_idle_category = "Just Chatting"
            cfg.autocat_announce = False
            cfg.twitch_channel = "chaarqueen"
            cfg.autocat_dry_run = False
            cfg.prediction_enabled = True
            cfg.prediction_window = 300
            arbiter = GameArbiter(
                helix=hx, resolver=MagicMock(), broadcaster_id="467734820", state=None
            )

        result = await arbiter.confirm_valorant_prediction()
        assert "estado" in result.lower() or "guardar" in result.lower()
        hx.create_prediction.assert_not_called()


# ===========================================================================
# cancel_prediction (!vcancel)
# ===========================================================================

class TestCancelPrediction:

    async def test_cancelPrediction_WhenNoPredictionActive_ReturnsNoActivePrediction(
        self, hx, tmp_path
    ):
        arbiter = _make(tmp_path, hx)
        result = await arbiter.cancel_prediction()
        assert "no hay" in result.lower()
        hx.cancel_prediction.assert_not_called()

    async def test_cancelPrediction_WhenPredictionActive_CallsHelixCancel(
        self, hx, tmp_path
    ):
        arbiter = _make(tmp_path, hx)
        await arbiter.state.set("prediction_active", _pred_state())

        result = await arbiter.cancel_prediction()

        hx.cancel_prediction.assert_called_once_with("467734820", "pred-001")
        assert "cancelada" in result.lower()

    async def test_cancelPrediction_WhenCancelled_ClearsStatePredictionActive(
        self, hx, tmp_path
    ):
        arbiter = _make(tmp_path, hx)
        await arbiter.state.set("prediction_active", _pred_state())
        await arbiter.cancel_prediction()
        assert arbiter.state.get("prediction_active") is None

    async def test_cancelPrediction_WhenTwitchSaysAlreadyEnded_ClearsStateAnyway(
        self, hx, tmp_path
    ):
        """Twitch rechaza cancelar eventos ya terminados; el estado local igual debe limpiarse."""
        hx.cancel_prediction.side_effect = Exception("already ended")
        arbiter = _make(tmp_path, hx)
        await arbiter.state.set("prediction_active", _pred_state())

        await arbiter.cancel_prediction()

        assert arbiter.state.get("prediction_active") is None

    async def test_cancelPrediction_WhenTwitchThrowsUnknownError_Propagates(
        self, hx, tmp_path
    ):
        hx.cancel_prediction.side_effect = Exception("network timeout")
        arbiter = _make(tmp_path, hx)
        await arbiter.state.set("prediction_active", _pred_state())

        with pytest.raises(Exception, match="network timeout"):
            await arbiter.cancel_prediction()


# ===========================================================================
# _prediction_result
# ===========================================================================

class TestPredictionResult:

    async def test_predictionResult_WhenLolGameWon_ReturnsTrue(self, hx, tmp_path):
        lol = MagicMock()
        lol.configured = True
        lol.match_won = AsyncMock(return_value=True)
        arbiter = _make(tmp_path, hx, lol=lol)

        result = await arbiter._prediction_result({
            "game": "league of legends",
            "match_key": "lol:12345",
            "prediction_id": "pred-001",
            "outcomes": {},
        })

        assert result is True
        lol.match_won.assert_called_once_with("12345")

    async def test_predictionResult_WhenLolGameLost_ReturnsFalse(self, hx, tmp_path):
        lol = MagicMock()
        lol.configured = True
        lol.match_won = AsyncMock(return_value=False)
        arbiter = _make(tmp_path, hx, lol=lol)

        result = await arbiter._prediction_result({
            "game": "league of legends",
            "match_key": "lol:99999",
            "prediction_id": "pred-001",
            "outcomes": {},
        })

        assert result is False

    async def test_predictionResult_WhenLolButNoClient_ReturnsNone(self, hx, tmp_path):
        arbiter = _make(tmp_path, hx, lol=None)
        result = await arbiter._prediction_result({
            "game": "league of legends",
            "match_key": "lol:12345",
            "prediction_id": "pred-001",
            "outcomes": {},
        })
        assert result is None

    async def test_predictionResult_WhenValorant_AlwaysReturnsNone(self, hx, tmp_path):
        """Valorant nunca resuelve solo — requiere !vwin o !vloss."""
        arbiter = _make(tmp_path, hx)
        result = await arbiter._prediction_result({
            "game": "valorant",
            "match_key": "valorant:manual:999",
            "prediction_id": "pred-val",
            "outcomes": {},
        })
        assert result is None

    async def test_predictionResult_WhenUnknownGame_ReturnsNone(self, hx, tmp_path):
        arbiter = _make(tmp_path, hx)
        result = await arbiter._prediction_result({
            "game": "minecraft",
            "match_key": "mc:xyz",
            "prediction_id": "pred-mc",
            "outcomes": {},
        })
        assert result is None

    async def test_predictionResult_WhenLolMatchKeyMissingPrefix_ReturnsNoneWithoutCallingLol(
        self, hx, tmp_path
    ):
        lol = MagicMock()
        lol.configured = True
        lol.match_won = AsyncMock(return_value=True)
        arbiter = _make(tmp_path, hx, lol=lol)

        result = await arbiter._prediction_result({
            "game": "league of legends",
            "match_key": "12345",   # sin prefijo lol:
            "prediction_id": "pred-001",
            "outcomes": {},
        })

        assert result is None
        lol.match_won.assert_not_called()


# ===========================================================================
# _prediction_match_key
# ===========================================================================

class TestPredictionMatchKey:

    async def test_predictionMatchKey_WhenLolWithActiveGame_ReturnsLolKey(
        self, hx, tmp_path
    ):
        lol = MagicMock()
        lol.configured = True
        lol.active_game_id = AsyncMock(return_value="99999")
        arbiter = _make(tmp_path, hx, lol=lol)

        key = await arbiter._prediction_match_key("league of legends")
        assert key == "lol:99999"

    async def test_predictionMatchKey_WhenLolNoActiveGame_ReturnsNone(
        self, hx, tmp_path
    ):
        lol = MagicMock()
        lol.configured = True
        lol.active_game_id = AsyncMock(return_value=None)
        arbiter = _make(tmp_path, hx, lol=lol)

        key = await arbiter._prediction_match_key("league of legends")
        assert key is None

    async def test_predictionMatchKey_WhenLolClientMissing_ReturnsNone(
        self, hx, tmp_path
    ):
        arbiter = _make(tmp_path, hx, lol=None)
        key = await arbiter._prediction_match_key("league of legends")
        assert key is None

    async def test_predictionMatchKey_WhenValorant_AlwaysReturnsNone(
        self, hx, tmp_path
    ):
        """Valorant nunca tiene key automática — diseño intencional."""
        arbiter = _make(tmp_path, hx)
        key = await arbiter._prediction_match_key("valorant")
        assert key is None


# ===========================================================================
# _forget_finished_prediction
# ===========================================================================

class TestForgetFinishedPrediction:

    async def test_forgetFinishedPrediction_WhenNoPredictionInState_SkipsHelixCall(
        self, hx, tmp_path
    ):
        arbiter = _make(tmp_path, hx)
        await arbiter._forget_finished_prediction()
        hx.get_predictions.assert_not_called()

    async def test_forgetFinishedPrediction_WhenPredictionStillActive_KeepsState(
        self, hx, tmp_path
    ):
        hx.get_predictions.return_value = [{"id": "pred-001", "status": "ACTIVE"}]
        arbiter = _make(tmp_path, hx)
        await arbiter.state.set("prediction_active", _pred_state())

        await arbiter._forget_finished_prediction()

        assert arbiter.state.get("prediction_active") is not None

    async def test_forgetFinishedPrediction_WhenPredictionResolved_ClearsState(
        self, hx, tmp_path
    ):
        hx.get_predictions.return_value = [{"id": "pred-001", "status": "RESOLVED"}]
        arbiter = _make(tmp_path, hx)
        await arbiter.state.set("prediction_active", _pred_state())

        await arbiter._forget_finished_prediction()

        assert arbiter.state.get("prediction_active") is None

    async def test_forgetFinishedPrediction_WhenPredictionLockedNotEnded_KeepsState(
        self, hx, tmp_path
    ):
        hx.get_predictions.return_value = [{"id": "pred-001", "status": "LOCKED"}]
        arbiter = _make(tmp_path, hx)
        await arbiter.state.set("prediction_active", _pred_state())

        await arbiter._forget_finished_prediction()

        assert arbiter.state.get("prediction_active") is not None

    async def test_forgetFinishedPrediction_WhenPredictionNotFoundInTwitch_ClearsState(
        self, hx, tmp_path
    ):
        hx.get_predictions.return_value = []
        arbiter = _make(tmp_path, hx)
        await arbiter.state.set("prediction_active", _pred_state())

        await arbiter._forget_finished_prediction()

        assert arbiter.state.get("prediction_active") is None
