"""Tests de predictions con estado leido del cliente local (via el agente).

Cubren el orden de fuentes: primero el cliente local que reporta el agente,
despues las APIs publicas (Riot para LoL, cliente local del bot para Valorant).

Convenciones
------------
* Nombre: ``test_<metodo>_When<Escenario>_<Resultado>``.
* ``cfg`` se parchea en tiempo de ejecucion con el fixture ``runtime_cfg``:
  el fixture ``mock_arbiter`` solo lo parchea durante la construccion.
* Nada toca la red: Helix, LolClient y el cliente de Valorant son mocks.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.gamesource import GameArbiter, Report
from src.riot.valorant import (
    ValorantLocalClient,
    decode_presence_private,
    parse_presence_phase,
)

OUTCOMES = [{"id": "o-win", "title": "Gana"}, {"id": "o-loss", "title": "Pierde"}]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def agent(game: str, match_id: str | None = None, **metadata) -> Report:
    """Reporte fresco del agente, con el estado del cliente local en metadata."""
    return Report(
        game=game,
        at=time.monotonic(),
        stale_after=210,
        match_id=match_id,
        metadata=metadata or None,
    )


def lol_client(*, configured: bool = True, active: str | None = None, won: bool | None = None):
    client = MagicMock()
    client.configured = configured
    client.active_game_id = AsyncMock(return_value=active)
    client.match_won = AsyncMock(return_value=won)
    return client


@pytest.fixture()
def runtime_cfg():
    with patch("src.gamesource.cfg") as cfg:
        cfg.prediction_enabled = True
        cfg.prediction_window = 300
        cfg.autocat_announce = False
        cfg.agent_debounce = 15
        cfg.presence_debounce = 45
        yield cfg


@pytest.fixture()
def arbiter(mock_arbiter: GameArbiter, mock_helix) -> GameArbiter:
    mock_helix.get_predictions = AsyncMock(return_value=[])
    mock_helix.create_prediction = AsyncMock(return_value={"id": "p1", "outcomes": OUTCOMES})
    mock_helix.resolve_prediction = AsyncMock()
    return mock_arbiter


# ---------------------------------------------------------------------------
# report(): cuando se vuelve a evaluar
# ---------------------------------------------------------------------------

class TestReportReschedules:

    async def test_report_WhenAgentMatchEnds_SchedulesApply(self, arbiter, runtime_cfg):
        arbiter._reports["agent"] = agent("VALORANT", "val-1", source="presence", phase="in_game")
        arbiter._schedule = MagicMock()
        await arbiter.report("agent", "VALORANT", match_id=None,
                             metadata={"source": "presence", "phase": "menus"}, stale_after=210)
        arbiter._schedule.assert_called_once()

    async def test_report_WhenResultArrivesForSameMatch_SchedulesApply(self, arbiter, runtime_cfg):
        arbiter._reports["agent"] = agent("League of Legends", "55", source="lcu", phase="post_game", result=None)
        arbiter._schedule = MagicMock()
        await arbiter.report("agent", "League of Legends", match_id="55",
                             metadata={"source": "lcu", "phase": "post_game", "result": "win"},
                             stale_after=210)
        arbiter._schedule.assert_called_once()

    async def test_report_WhenHeartbeatRepeatsSameState_DoesNotSchedule(self, arbiter, runtime_cfg):
        meta = {"source": "lcu", "phase": "in_game", "result": None}
        arbiter._reports["agent"] = agent("League of Legends", "55", **meta)
        arbiter._schedule = MagicMock()
        await arbiter.report("agent", "League of Legends", match_id="55", metadata=dict(meta), stale_after=210)
        arbiter._schedule.assert_not_called()


# ---------------------------------------------------------------------------
# _prediction_match_key: local primero, publico despues
# ---------------------------------------------------------------------------

class TestMatchKey:

    async def test_matchKey_WhenLocalLoLInGame_UsesGameIdWithoutRiotApi(self, arbiter, runtime_cfg):
        arbiter.lol = lol_client(configured=True, active="999")
        arbiter._reports["agent"] = agent("League of Legends", "55", source="lcu", phase="in_game")
        assert await arbiter._prediction_match_key("league of legends") == "lol:55"
        arbiter.lol.active_game_id.assert_not_called()

    async def test_matchKey_WhenLocalLoLInMenus_ReturnsNoneWithoutAskingRiot(self, arbiter, runtime_cfg):
        arbiter.lol = lol_client(configured=True, active="999")
        arbiter._reports["agent"] = agent("League of Legends", None, source="lcu", phase="menus")
        assert await arbiter._prediction_match_key("league of legends") is None
        arbiter.lol.active_game_id.assert_not_called()

    async def test_matchKey_WhenLocalLoLPostGame_ReturnsNone(self, arbiter, runtime_cfg):
        # La partida ya termino: abrir una prediction ahora no tiene sentido.
        arbiter._reports["agent"] = agent("League of Legends", "55", source="lcu", phase="post_game")
        assert await arbiter._prediction_match_key("league of legends") is None

    async def test_matchKey_WhenNoLocalData_FallsBackToRiotApi(self, arbiter, runtime_cfg):
        arbiter.lol = lol_client(configured=True, active="777")
        arbiter._reports["agent"] = agent("League of Legends", None)  # agente sin datos del cliente
        assert await arbiter._prediction_match_key("league of legends") == "lol:777"

    async def test_matchKey_WhenNoLocalDataAndKeyExpired_ReturnsNone(self, arbiter, runtime_cfg):
        arbiter.lol = lol_client(configured=False)
        assert await arbiter._prediction_match_key("league of legends") is None
        arbiter.lol.active_game_id.assert_not_called()

    async def test_matchKey_WhenAgentStale_IgnoresItsLocalData(self, arbiter, runtime_cfg):
        arbiter.lol = lol_client(configured=True, active="777")
        stale = agent("League of Legends", "55", source="lcu", phase="in_game")
        stale.at = time.monotonic() - 1000
        arbiter._reports["agent"] = stale
        assert await arbiter._prediction_match_key("league of legends") == "lol:777"

    async def test_matchKey_WhenLocalDataIsFromAnotherGame_IgnoresIt(self, arbiter, runtime_cfg):
        arbiter.lol = lol_client(configured=False)
        arbiter._reports["agent"] = agent("VALORANT", "val-1", source="presence", phase="in_game")
        assert await arbiter._prediction_match_key("league of legends") is None

    async def test_matchKey_WhenLocalValorantPregame_UsesAgentKey(self, arbiter, runtime_cfg):
        arbiter._reports["agent"] = agent("VALORANT", "val-1", source="presence", phase="pregame")
        assert await arbiter._prediction_match_key("valorant") == "valorant:val-1"

    async def test_matchKey_WhenLocalValorantMenus_ReturnsNone(self, arbiter, runtime_cfg):
        arbiter._reports["agent"] = agent("VALORANT", None, source="presence", phase="menus")
        assert await arbiter._prediction_match_key("valorant") is None

    async def test_matchKey_WhenNoAgentValorant_FallsBackToBotLocalClient(self, arbiter, runtime_cfg):
        arbiter.valorant = MagicMock()
        arbiter.valorant.local.configured = True
        arbiter.valorant.local.active_match_id = AsyncMock(return_value="val-9")
        assert await arbiter._prediction_match_key("valorant") == "valorant:val-9"


# ---------------------------------------------------------------------------
# _prediction_result: resultado local, Match-V5 de respaldo
# ---------------------------------------------------------------------------

class TestPredictionResult:

    async def test_result_WhenLocalReportsWinForCurrentMatch_ReturnsTrue(self, arbiter, runtime_cfg):
        arbiter._reports["agent"] = agent("League of Legends", "55", source="lcu", phase="post_game", result="win")
        assert await arbiter._prediction_result({"game": "league of legends", "match_key": "lol:55"}) is True

    async def test_result_WhenLocalReportsLossInLastMatch_ReturnsFalse(self, arbiter, runtime_cfg):
        arbiter._reports["agent"] = agent(
            "League of Legends", None, source="lcu", phase="menus",
            last_match={"id": "55", "result": "loss"},
        )
        assert await arbiter._prediction_result({"game": "league of legends", "match_key": "lol:55"}) is False

    async def test_result_WhenLocalResultIsForAnotherGame_FallsBackToMatchV5(self, arbiter, runtime_cfg):
        arbiter.lol = lol_client(configured=True, won=True)
        arbiter._reports["agent"] = agent(
            "League of Legends", None, source="lcu", phase="menus",
            last_match={"id": "44", "result": "loss"},
        )
        assert await arbiter._prediction_result({"game": "league of legends", "match_key": "lol:55"}) is True
        arbiter.lol.match_won.assert_awaited_once_with("55")

    async def test_result_WhenNoLocalResultAndKeyExpired_ReturnsNone(self, arbiter, runtime_cfg):
        arbiter.lol = lol_client(configured=False)
        assert await arbiter._prediction_result({"game": "league of legends", "match_key": "lol:55"}) is None
        arbiter.lol.match_won.assert_not_called()

    async def test_result_WhenValorant_StaysManual(self, arbiter, runtime_cfg):
        arbiter._reports["agent"] = agent(
            "VALORANT", None, source="presence", phase="menus",
            last_match={"id": "val-1", "score": {"ally": 13, "enemy": 9}},
        )
        assert await arbiter._prediction_result({"game": "valorant", "match_key": "valorant:val-1"}) is None


# ---------------------------------------------------------------------------
# _check_lol_prediction_finished: cuando disparar la resolucion
# ---------------------------------------------------------------------------

class TestLoLFinished:

    async def test_finished_WhenLocalResultKnown_SchedulesResolution(self, arbiter, runtime_cfg):
        arbiter._reports["agent"] = agent("League of Legends", "55", source="lcu", phase="post_game", result="win")
        arbiter._schedule_prediction_result = MagicMock()
        await arbiter._check_lol_prediction_finished({"match_key": "lol:55"})
        arbiter._schedule_prediction_result.assert_called_once()

    async def test_finished_WhenSameMatchStillInGame_DoesNothing(self, arbiter, runtime_cfg):
        arbiter.lol = lol_client(configured=True)
        arbiter._reports["agent"] = agent("League of Legends", "55", source="lcu", phase="in_game", result=None)
        arbiter._schedule_prediction_result = MagicMock()
        await arbiter._check_lol_prediction_finished({"match_key": "lol:55"})
        arbiter._schedule_prediction_result.assert_not_called()
        arbiter.lol.active_game_id.assert_not_called()

    async def test_finished_WhenOverWithoutLocalResult_AsksPublicApi(self, arbiter, runtime_cfg):
        arbiter.lol = lol_client(configured=True)
        arbiter._reports["agent"] = agent("League of Legends", None, source="lcu", phase="menus")
        arbiter._schedule_prediction_result = MagicMock()
        await arbiter._check_lol_prediction_finished({"match_key": "lol:55"})
        arbiter._schedule_prediction_result.assert_called_once()

    async def test_finished_WhenOverWithoutResultAndNoKey_DoesNotLoop(self, arbiter, runtime_cfg):
        # Sin resultado local ni key no hay nada que consultar: no se lanza
        # una resolucion que va a fallar cada minuto.
        arbiter.lol = lol_client(configured=False)
        arbiter._reports["agent"] = agent("League of Legends", None, source="lcu", phase="menus")
        arbiter._schedule_prediction_result = MagicMock()
        await arbiter._check_lol_prediction_finished({"match_key": "lol:55"})
        arbiter._schedule_prediction_result.assert_not_called()


# ---------------------------------------------------------------------------
# _maybe_start_prediction y el flujo completo
# ---------------------------------------------------------------------------

class TestPredictionFlow:

    async def test_start_WhenLockedPredictionOpen_DoesNotCreateNorRetry(self, arbiter, mock_helix, mock_state, runtime_cfg):
        mock_helix.get_predictions = AsyncMock(return_value=[{"id": "old", "status": "LOCKED"}])
        arbiter._reports["agent"] = agent("League of Legends", "55", source="lcu", phase="in_game")
        await arbiter._maybe_start_prediction("League of Legends")
        mock_helix.create_prediction.assert_not_called()
        assert mock_state.get("prediction_created_key") == "lol:55"

    async def test_start_WhenSameMatchAlreadyCreated_DoesNotDuplicate(self, arbiter, mock_helix, mock_state, runtime_cfg):
        arbiter._reports["agent"] = agent("League of Legends", "55", source="lcu", phase="in_game")
        await arbiter._maybe_start_prediction("League of Legends")
        await arbiter._maybe_start_prediction("League of Legends")
        assert mock_helix.create_prediction.await_count == 1

    async def test_flow_WhenLoLMatchStartsAndEnds_CreatesAndResolvesLocally(
        self, arbiter, mock_helix, mock_state, runtime_cfg
    ):
        # Riot no esta disponible: todo sale del cliente local.
        arbiter.lol = lol_client(configured=False)

        arbiter._reports["agent"] = agent("League of Legends", "55", source="lcu", phase="in_game", result=None)
        await arbiter._maybe_start_prediction("League of Legends")
        mock_helix.create_prediction.assert_awaited_once()
        assert mock_state.get("prediction_active")["match_key"] == "lol:55"

        arbiter._reports["agent"] = agent("League of Legends", "55", source="lcu", phase="post_game", result="win")
        with patch("src.gamesource.asyncio.sleep", AsyncMock()):
            await arbiter._resolve_prediction_later()

        mock_helix.resolve_prediction.assert_awaited_once()
        assert mock_helix.resolve_prediction.await_args.kwargs["winning_outcome_id"] == "o-win"
        assert mock_state.get("prediction_active") is None

    async def test_flow_WhenValorantPregame_CreatesPredictionAutomatically(
        self, arbiter, mock_helix, mock_state, runtime_cfg
    ):
        arbiter._reports["agent"] = agent("VALORANT", "val-1", source="presence", phase="pregame")
        await arbiter._maybe_start_prediction("VALORANT")
        mock_helix.create_prediction.assert_awaited_once()
        assert mock_state.get("prediction_active")["match_key"] == "valorant:val-1"


# ---------------------------------------------------------------------------
# _skip: sin repetir el mismo motivo cada minuto
# ---------------------------------------------------------------------------

class TestSkipLogging:

    async def test_skip_WhenSameReasonRepeats_LogsOnce(self, arbiter, runtime_cfg, caplog):
        arbiter._reports["agent"] = agent("League of Legends", None, source="lcu", phase="menus")
        with caplog.at_level(logging.INFO, logger="gamesource"):
            for _ in range(3):
                await arbiter._prediction_match_key("league of legends")
        assert sum("Not creating prediction" in r.message for r in caplog.records) == 1

    async def test_skip_WhenReasonChanges_LogsAgain(self, arbiter, runtime_cfg, caplog):
        with caplog.at_level(logging.INFO, logger="gamesource"):
            arbiter._reports["agent"] = agent("League of Legends", None, source="lcu", phase="menus")
            await arbiter._prediction_match_key("league of legends")
            arbiter._reports["agent"] = agent("League of Legends", None, source="lcu", phase="pregame")
            await arbiter._prediction_match_key("league of legends")
        assert sum("Not creating prediction" in r.message for r in caplog.records) == 2


# ---------------------------------------------------------------------------
# Cliente local de Valorant del bot (respaldo cuando corre en la misma PC)
# ---------------------------------------------------------------------------

def _b64(data: dict) -> str:
    return base64.b64encode(json.dumps(data).encode()).decode()


class TestValorantPresenceHelpers:

    def test_decode_WhenValidBase64Json_ReturnsDict(self):
        assert decode_presence_private(_b64({"sessionLoopState": "INGAME"})) == {"sessionLoopState": "INGAME"}

    def test_decode_WhenUrlSafeWithoutPadding_ReturnsDict(self):
        raw = base64.urlsafe_b64encode(json.dumps({"a": ">>>?"}).encode()).decode().rstrip("=")
        assert decode_presence_private(raw) == {"a": ">>>?"}

    # "bm8gZXMganNvbg==" es base64 valido de "no es json".
    @pytest.mark.parametrize("value", [None, "", 123, "@@@", "bm8gZXMganNvbg=="])
    def test_decode_WhenInvalid_ReturnsNone(self, value):
        assert decode_presence_private(value) is None

    def test_decode_WhenJsonIsNotAnObject_ReturnsNone(self):
        assert decode_presence_private(base64.b64encode(b"[1, 2]").decode()) is None

    def test_phase_WhenFlatFormat_ReturnsNormalized(self):
        assert parse_presence_phase({"sessionLoopState": "INGAME"}) == "in_game"

    def test_phase_WhenNestedFormat_ReturnsNormalized(self):
        assert parse_presence_phase({"matchPresenceData": {"sessionLoopState": "PREGAME"}}) == "pregame"

    def test_phase_WhenUnknownState_ReturnsRawValue(self):
        assert parse_presence_phase({"sessionLoopState": "REPLAY"}) == "REPLAY"

    def test_phase_WhenMissing_ReturnsNone(self):
        assert parse_presence_phase({"partyId": "x"}) is None


class TestValorantLocalClient:

    def _client(self, *phases: str | None) -> ValorantLocalClient:
        client = ValorantLocalClient(MagicMock())
        client._read_lockfile = MagicMock(return_value=(1234, "pw"))
        client.match_phase = AsyncMock(side_effect=list(phases))
        return client

    async def test_activeMatchId_WhenEnteringPregame_CreatesKeyAndKeepsItInGame(self):
        client = self._client("menus", "pregame", "in_game")
        assert await client.active_match_id() is None
        key = await client.active_match_id()
        assert key and key.startswith("val-")
        assert await client.active_match_id() == key

    async def test_activeMatchId_WhenBackToMenus_ReleasesKey(self):
        client = self._client("in_game", "menus")
        assert await client.active_match_id()
        assert await client.active_match_id() is None

    async def test_activeMatchId_WhenPhaseUnreadable_KeepsCurrentMatch(self):
        client = self._client("in_game", None)
        key = await client.active_match_id()
        assert await client.active_match_id() == key

    async def test_activeMatchId_WhenClientClosed_ResetsState(self):
        client = self._client("in_game")
        assert await client.active_match_id()
        client._read_lockfile = MagicMock(return_value=None)
        assert await client.active_match_id() is None
        assert client._match_id is None

    async def test_localPuuid_WhenSessionUsesPuuidField_ReadsIt(self):
        # Formato real visto en el cliente: el campo es `puuid`, no `subject`.
        client = ValorantLocalClient(MagicMock())
        client._read_lockfile = MagicMock(return_value=(1234, "pw"))
        client._local_get = AsyncMock(return_value={"puuid": "65a61def", "game_name": "ChaarQueen"})
        assert await client.local_puuid() == "65a61def"

    async def test_matchPhase_WhenOwnPresenceInGame_ReturnsInGame(self):
        client = ValorantLocalClient(MagicMock())
        client._read_lockfile = MagicMock(return_value=(1234, "pw"))
        client._puuid = "me"
        client._local_get = AsyncMock(return_value={"presences": [
            {"puuid": "friend", "product": "valorant", "private": _b64({"sessionLoopState": "MENUS"})},
            {"puuid": "me", "product": "valorant", "private": _b64({"sessionLoopState": "INGAME"})},
        ]})
        assert await client.match_phase() == "in_game"
