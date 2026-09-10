"""Contenedor de dependencias compartido por todos los subsistemas."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import aiohttp

from .category import CategoryResolver
from .chat_history import ChatHistory
from .gamesource import GameArbiter
from .riot.lol import LolClient
from .riot.valorant import ValorantClient
from .storage import JsonStore
from .twitch.auth import TwitchAuth
from .twitch.helix import Helix

if TYPE_CHECKING:  # evita imports circulares en runtime
    from .discord_presence.bot import PresenceBot
    from .twitch.chat import ChatClient


@dataclass
class Services:
    session: aiohttp.ClientSession
    tokens: JsonStore
    state: JsonStore
    auth: TwitchAuth
    helix: Helix
    resolver: CategoryResolver
    arbiter: GameArbiter
    lol: LolClient
    valorant: ValorantClient
    broadcaster_id: str
    history: ChatHistory
    chat: "ChatClient | None" = None
    presence: "PresenceBot | None" = None
    clips: Any = None
