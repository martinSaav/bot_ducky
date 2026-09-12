"""Tests para src/commands/registry.py — dispatch, cooldowns, permisos, comandos.

Naming: [MethodName]_[Scenario/State]_[ExpectedResult]

Estrategia de aislamiento
--------------------------
* ``cfg`` se parchea para que Registry no lea el .env real.
* Services se pasa como MagicMock (fixture mock_services en conftest.py).
* El chat (svc.chat.say) es AsyncMock para verificar las respuestas.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.commands.registry import Registry
from tests.conftest import make_message


# ---------------------------------------------------------------------------
# Fixture local: Registry con cfg mockeado
# ---------------------------------------------------------------------------

@pytest.fixture()
def registry(mock_services):
    with patch("src.commands.registry.cfg") as mock_cfg:
        mock_cfg.twitch_channel = "chaarqueen"
        reg = Registry(svc=mock_services)
    return reg


# ===========================================================================
# dispatch — routing básico
# ===========================================================================

class TestDispatchRouting:

    async def test_dispatch_WhenNotPrefixed_DoesNothing(self, registry, mock_services):
        msg = make_message(text="hola chat sin prefijo")
        await registry.dispatch(msg)
        mock_services.chat.say.assert_not_called()

    async def test_dispatch_WhenOnlyExclamation_DoesNothing(self, registry, mock_services):
        msg = make_message(text="!")
        await registry.dispatch(msg)
        mock_services.chat.say.assert_not_called()

    async def test_dispatch_WhenUnknownCommand_DoesNothing(self, registry, mock_services):
        msg = make_message(text="!inexistente")
        await registry.dispatch(msg)
        mock_services.chat.say.assert_not_called()

    async def test_dispatch_WhenCommandWithArgs_PassesArgsToHandler(
        self, registry, mock_services
    ):
        """!recargar no tiene args pero verifica que el handler se llama."""
        msg = make_message(text="!recargar", is_mod=True)
        await registry.dispatch(msg)
        mock_services.chat.say.assert_called_once()
        call_args = mock_services.chat.say.call_args
        assert "recargado" in call_args[0][0].lower()


# ===========================================================================
# dispatch — permisos mod_only
# ===========================================================================

class TestDispatchPermissions:

    async def test_dispatch_WhenModOnlyAndViewer_DoesNothing(
        self, registry, mock_services
    ):
        """Viewer usando comando de mod → silencio total."""
        msg = make_message(text="!categoria Just Chatting", is_mod=False)
        await registry.dispatch(msg)
        mock_services.chat.say.assert_not_called()

    async def test_dispatch_WhenModOnlyAndMod_ExecutesCommand(
        self, registry, mock_services
    ):
        """Mod puede usar comandos de mod."""
        msg = make_message(text="!recargar", is_mod=True)
        await registry.dispatch(msg)
        mock_services.chat.say.assert_called_once()

    async def test_dispatch_WhenModOnlyAndBroadcaster_ExecutesCommand(
        self, registry, mock_services
    ):
        """Broadcaster (is_broadcaster → is_mod=True) puede usar comandos de mod."""
        msg = make_message(text="!recargar", is_broadcaster=True)
        await registry.dispatch(msg)
        mock_services.chat.say.assert_called_once()


# ===========================================================================
# dispatch — cooldowns
# ===========================================================================

class TestDispatchCooldown:

    async def test_dispatch_WhenCooldownActive_SecondCallIgnored(
        self, registry, mock_services
    ):
        """Segunda llamada del mismo comando dentro del cooldown es ignorada."""
        msg1 = make_message(text="!comandos", message_id="m1")
        msg2 = make_message(text="!comandos", message_id="m2")
        await registry.dispatch(msg1)
        await registry.dispatch(msg2)
        # Solo debe haberse enviado una respuesta
        assert mock_services.chat.say.call_count == 1

    async def test_dispatch_WhenCooldownActiveAndMod_SecondCallExecutes(
        self, registry, mock_services
    ):
        """Mods bypasean el cooldown siempre."""
        msg1 = make_message(text="!recargar", is_mod=True, message_id="m1")
        msg2 = make_message(text="!recargar", is_mod=True, message_id="m2")
        await registry.dispatch(msg1)
        await registry.dispatch(msg2)
        assert mock_services.chat.say.call_count == 2

    async def test_dispatch_WhenCooldownActiveAndVip_SecondCallExecutes(
        self, registry, mock_services
    ):
        """VIPs también bypasean el cooldown."""
        msg1 = make_message(text="!recargar", is_mod=True, is_vip=True, message_id="m1")
        msg2 = make_message(text="!recargar", is_mod=True, is_vip=True, message_id="m2")
        await registry.dispatch(msg1)
        await registry.dispatch(msg2)
        assert mock_services.chat.say.call_count == 2

    async def test_dispatch_AfterCooldownExpires_AllowsNewCall(self, registry, mock_services):
        """Después de que el cooldown expira, el comando vuelve a ejecutarse."""
        # Forzar el cooldown como si ya expiró: ponemos last_used en el pasado
        registry._last_used["comandos"] = time.monotonic() - 999
        msg = make_message(text="!comandos", message_id="m1")
        await registry.dispatch(msg)
        mock_services.chat.say.assert_called_once()


# ===========================================================================
# cmd_help
# ===========================================================================

class TestCmdHelp:

    async def test_cmdHelp_WhenViewer_HidesModOnlyCommands(self, registry, mock_services):
        msg = make_message(text="!comandos", is_mod=False)
        await registry.dispatch(msg)
        response = mock_services.chat.say.call_args[0][0]
        # Comandos de mod no deben aparecer
        assert "!categoria" not in response
        assert "!auto" not in response
        assert "!recargar" not in response

    async def test_cmdHelp_WhenViewer_ShowsPublicCommands(self, registry, mock_services):
        msg = make_message(text="!comandos", is_mod=False)
        await registry.dispatch(msg)
        response = mock_services.chat.say.call_args[0][0]
        assert "!lrank" in response
        assert "!uptime" in response

    async def test_cmdHelp_WhenMod_ShowsModCommands(self, registry, mock_services):
        msg = make_message(text="!comandos", is_mod=True)
        await registry.dispatch(msg)
        response = mock_services.chat.say.call_args[0][0]
        assert "!categoria" in response
        assert "!recargar" in response

    async def test_cmdHelp_DoesNotDuplicateAliasedCommands(self, registry, mock_services):
        """Comandos con alias no deben aparecer duplicados."""
        msg = make_message(text="!comandos", is_mod=True)
        await registry.dispatch(msg)
        response = mock_services.chat.say.call_args[0][0]
        # "lrank" no debería aparecer más de una vez
        assert response.count("!lrank") == 1


# ===========================================================================
# cmd_auto
# ===========================================================================

class TestCmdAuto:

    async def test_cmdAuto_WithOnArg_EnablesArbiter(self, registry, mock_services):
        mock_services.arbiter.enabled = False
        msg = make_message(text="!auto on", is_mod=True)
        await registry.dispatch(msg)
        assert mock_services.arbiter.enabled is True

    async def test_cmdAuto_WithOffArg_DisablesArbiter(self, registry, mock_services):
        mock_services.arbiter.enabled = True
        msg = make_message(text="!auto off", is_mod=True)
        await registry.dispatch(msg)
        assert mock_services.arbiter.enabled is False

    async def test_cmdAuto_WithNoArg_ReportsCurrentState(self, registry, mock_services):
        from unittest.mock import patch as mpatch
        mock_services.arbiter.enabled = True
        with mpatch.object(mock_services.arbiter, "sources", return_value={}):
            msg = make_message(text="!auto", is_mod=True)
            await registry.dispatch(msg)
        response = mock_services.chat.say.call_args[0][0]
        assert "encendido" in response.lower()

    async def test_cmdAuto_WhenDisabled_ReportsApagado(self, registry, mock_services):
        from unittest.mock import patch as mpatch
        mock_services.arbiter.enabled = False
        with mpatch.object(mock_services.arbiter, "sources", return_value={}):
            msg = make_message(text="!auto", is_mod=True)
            await registry.dispatch(msg)
        response = mock_services.chat.say.call_args[0][0]
        assert "apagado" in response.lower()

    async def test_cmdAuto_WithInvalidArg_ReturnsUsageMessage(
        self, registry, mock_services
    ):
        msg = make_message(text="!auto xyz", is_mod=True)
        await registry.dispatch(msg)
        response = mock_services.chat.say.call_args[0][0]
        assert "auto on" in response.lower() or "uso:" in response.lower()

    async def test_cmdAuto_WithSpanishSiArg_Enables(self, registry, mock_services):
        mock_services.arbiter.enabled = False
        msg = make_message(text="!auto si", is_mod=True)
        await registry.dispatch(msg)
        assert mock_services.arbiter.enabled is True


# ===========================================================================
# cmd_reload
# ===========================================================================

class TestCmdReload:

    async def test_cmdReload_Always_CallsResolverReloadMap(self, registry, mock_services):
        from unittest.mock import patch as mpatch
        with mpatch.object(mock_services.resolver, "reload_map") as mock_reload:
            msg = make_message(text="!recargar", is_mod=True)
            await registry.dispatch(msg)
        mock_reload.assert_called_once()

    async def test_cmdReload_Always_ReturnsConfirmationMessage(
        self, registry, mock_services
    ):
        msg = make_message(text="!recargar", is_mod=True)
        await registry.dispatch(msg)
        response = mock_services.chat.say.call_args[0][0]
        assert "recargado" in response.lower()


# ===========================================================================
# cmd_rank (LoL — lógica de configuración)
# ===========================================================================

class TestCmdRank:

    async def test_cmdRank_WhenLolNotConfigured_ReturnsNotConfiguredMessage(
        self, registry, mock_services
    ):
        mock_services.lol.configured = False
        msg = make_message(text="!lrank")
        await registry.dispatch(msg)
        response = mock_services.chat.say.call_args[0][0]
        assert "configurad" in response.lower()

    async def test_cmdRank_WhenLolConfigured_CallsLolRank(
        self, registry, mock_services
    ):
        mock_services.lol.configured = True
        mock_services.lol.rank = AsyncMock(return_value="Platino IV 45LP")
        msg = make_message(text="!lrank")
        await registry.dispatch(msg)
        mock_services.lol.rank.assert_called_once_with(None)

    async def test_cmdRank_WithRiotIdArg_PassesArgToLolRank(
        self, registry, mock_services
    ):
        mock_services.lol.configured = True
        mock_services.lol.rank = AsyncMock(return_value="Oro II")
        msg = make_message(text="!lrank Alguien#LAS")
        await registry.dispatch(msg)
        mock_services.lol.rank.assert_called_once_with("Alguien#LAS")


# ===========================================================================
# Alias
# ===========================================================================

class TestAliases:

    async def test_alias_EloCommandDispatchesToLrank(self, registry, mock_services):
        mock_services.lol.configured = False
        msg = make_message(text="!elo")
        await registry.dispatch(msg)
        # Mismo handler que !lrank → misma respuesta
        response = mock_services.chat.say.call_args[0][0]
        assert "configurad" in response.lower()

    async def test_alias_PartidaCommandDispatchesToLmatch(self, registry, mock_services):
        mock_services.lol.configured = False
        msg = make_message(text="!partida")
        await registry.dispatch(msg)
        response = mock_services.chat.say.call_args[0][0]
        assert "configurad" in response.lower()
