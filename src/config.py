"""Carga y validación de la configuración desde .env."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
DOWNLOAD_DIR = ROOT / "downloads"

load_dotenv(ROOT / ".env")


def _str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "si", "sí", "on")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw and raw.strip() else default
    except ValueError:
        return default


class Config:
    # --- Twitch ---
    twitch_client_id = _str("TWITCH_CLIENT_ID")
    twitch_client_secret = _str("TWITCH_CLIENT_SECRET")
    twitch_redirect_uri = _str("TWITCH_REDIRECT_URI", "http://localhost:3000/callback")
    twitch_channel = _str("TWITCH_CHANNEL").lstrip("@").lower()
    twitch_bot_login = _str("TWITCH_BOT_LOGIN").lstrip("@").lower()

    #: Como se presenta el bot en el chat y en los logs. Es solo cosmetico:
    #: el nombre que ve el chat en cada mensaje es el de la cuenta de Twitch.
    bot_name = _str("BOT_NAME")

    # --- Discord ---
    discord_token = _str("DISCORD_BOT_TOKEN")
    discord_streamer_id = _int("DISCORD_STREAMER_ID", 0)
    presence_debounce = _int("PRESENCE_DEBOUNCE_SECONDS", 45)
    presence_check_seconds = _int("PRESENCE_CHECK_SECONDS", 60)
    autocat_dry_run = _bool("AUTOCAT_DRY_RUN", False)
    autocat_enabled = _bool("AUTOCAT_ENABLED", True)
    autocat_only_when_live = _bool("AUTOCAT_ONLY_WHEN_LIVE", False)
    autocat_idle_category = _str("AUTOCAT_IDLE_CATEGORY", "Just Chatting")
    autocat_announce = _bool("AUTOCAT_ANNOUNCE", True)
    prediction_enabled = _bool("PREDICTION_ENABLED", True)
    prediction_window = _int("PREDICTION_WINDOW_SECONDS", 300)

    # --- agente de escritorio (Rust) ---
    agent_token = _str("AGENT_TOKEN")
    agent_host = _str("AGENT_HOST", "0.0.0.0")
    agent_port = _int("AGENT_PORT", 8787)
    agent_debounce = _int("AGENT_DEBOUNCE_SECONDS", 15)

    # --- control local del chat ---
    chat_control_host = _str("CHAT_CONTROL_HOST", "127.0.0.1")
    chat_control_port = _int("CHAT_CONTROL_PORT", 8790)

    # --- historial persistente del chat ---
    database_url = _str("DATABASE_URL")
    llm_api_key = _str("LLM_API_KEY")
    llm_model = _str("LLM_MODEL", "gpt-4o-mini")
    llm_base_url = _str("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_max_messages = _int("LLM_MAX_MESSAGES", 300)
    embedding_model = _str("EMBEDDING_MODEL", "models/gemini-embedding-2")
    embedding_batch_size = _int("EMBEDDING_BATCH_SIZE", 20)

    chat_ignored_authors = {
        author.strip().lower()
        for author in _str("CHAT_IGNORED_AUTHORS", "nightbot").split(",")
        if author.strip()
    }

    # --- Riot ---
    riot_api_key = _str("RIOT_API_KEY")
    riot_id = _str("RIOT_ID")
    riot_platform = _str("RIOT_PLATFORM", "la2").lower()
    riot_region = _str("RIOT_REGION", "americas").lower()

    henrik_api_key = _str("HENRIK_API_KEY")
    valorant_id = _str("VALORANT_ID")
    valorant_region = _str("VALORANT_REGION", "latam").lower()

    # --- Clips ---
    clips_enabled = _bool("CLIPS_ENABLED", True)
    clips_run_at = _str("CLIPS_RUN_AT", "05:00")
    clips_lookback_hours = _int("CLIPS_LOOKBACK_HOURS", 24)
    clips_max = _int("CLIPS_MAX", 15)
    clips_min_views = _int("CLIPS_MIN_VIEWS", 3)

    google_sa_file = _str("GOOGLE_SERVICE_ACCOUNT_FILE", "secrets/service-account.json")
    gdrive_folder_id = _str("GDRIVE_FOLDER_ID")

    # --- rutas ---
    root = ROOT
    data_dir = DATA_DIR
    log_dir = LOG_DIR
    download_dir = DOWNLOAD_DIR
    token_file = DATA_DIR / "tokens.json"
    state_file = DATA_DIR / "state.json"
    game_map_file = ROOT / "config" / "game_map.json"

    @property
    def bot_login(self) -> str:
        """Cuenta que escribe en el chat; cae al broadcaster si no se configuró."""
        return self.twitch_bot_login or self.twitch_channel

    def say_as(self, text: str) -> str:
        """Devuelve el mensaje sin una firma redundante del bot."""
        return text

    @property
    def sa_path(self) -> Path:
        p = Path(self.google_sa_file)
        return p if p.is_absolute() else ROOT / p

    def missing(self, *keys: str) -> list[str]:
        """Devuelve los nombres de config vacíos entre los pedidos."""
        return [k for k in keys if not getattr(self, k, None)]


cfg = Config()

for _d in (DATA_DIR, LOG_DIR, DOWNLOAD_DIR):
    _d.mkdir(parents=True, exist_ok=True)
