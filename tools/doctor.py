"""Diagnostics: checks that every piece is configured correctly before starting.

    python tools/doctor.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import cfg  # noqa: E402
from src.storage import JsonStore  # noqa: E402
from src.twitch import auth as twitch_auth  # noqa: E402

OK, WARN, BAD = "[OK] ", "[!]  ", "[X]  "
_fails = 0


def line(status: str, text: str) -> None:
    global _fails
    if status == BAD:
        _fails += 1
    print(f"  {status}{text}")


def header(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


async def check_twitch(session: aiohttp.ClientSession) -> None:
    header("Twitch")
    if not cfg.twitch_client_id or not cfg.twitch_client_secret:
        line(BAD, "TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET missing in .env")
        return
    line(OK, f"App configured (client_id ...{cfg.twitch_client_id[-6:]})")

    store = JsonStore(cfg.token_file)
    auth = twitch_auth.TwitchAuth(session, store)

    try:
        await auth.app_bearer()
        line(OK, "App access token: obtained fine")
    except Exception as exc:  # noqa: BLE001
        line(BAD, f"App access token failed: {exc}")
        return

    for role, needed in twitch_auth.SCOPES.items():
        entry = store.get(role) or {}
        if not entry.get("refresh_token"):
            level = BAD if role == "broadcaster" else WARN
            line(level, f"Role '{role}' not authorized -> python tools/auth_twitch.py {role}")
            continue
        try:
            token = await auth.bearer(role)
            info = await twitch_auth.validate(session, token)
        except Exception as exc:  # noqa: BLE001
            line(BAD, f"Role '{role}': invalid token ({exc})")
            continue
        scopes = set(info.get("scopes") or [])
        missing = [s for s in needed if s not in scopes]
        if missing:
            line(BAD, f"Role '{role}' ({info.get('login')}): missing scopes {missing} "
                      f"-> authorize again")
        else:
            line(OK, f"Role '{role}': {info.get('login')} (id {info.get('user_id')}), scopes OK")

        expected = cfg.twitch_channel if role == "broadcaster" else cfg.bot_login
        if expected and str(info.get("login", "")).lower() != expected.lower():
            line(WARN, f"Role '{role}' authorized as '{info.get('login')}' "
                       f"but '{expected}' was expected")


async def check_discord() -> None:
    header("Discord (auto-categorizer)")
    if not cfg.discord_token:
        line(WARN, "DISCORD_BOT_TOKEN empty: the auto-categorizer will not start")
        return
    if not cfg.discord_streamer_id:
        line(BAD, "DISCORD_STREAMER_ID empty or not numeric: the bot does not know who to watch")
        return
    # Discord IDs are 17-20 digit snowflakes. A short number is usually the old
    # discriminator (#8553), which identifies nobody.
    digits = len(str(cfg.discord_streamer_id))
    if not 17 <= digits <= 20:
        line(BAD, f"DISCORD_STREAMER_ID has {digits} digits and should have 17-20. "
                  "That is not a user ID: enable Developer Mode and use "
                  "right click on her > Copy User ID.")
        return
    try:
        import discord  # noqa: F401
    except ImportError:
        line(BAD, "discord.py not installed -> pip install -r requirements.txt")
        return
    if not cfg.game_map_file.exists():
        line(WARN, f"{cfg.game_map_file} does not exist; only the automatic search will be used")
    else:
        line(OK, "config/game_map.json present")

    # Connect for real: it is the only way to know whether the privileged
    # intents are enabled, whether the bot shares a server with the streamer
    # and whether her presence is visible. Discord does not expose it via API.
    await _check_discord_live()


async def _check_discord_live() -> None:
    import discord

    intents = discord.Intents.none()
    intents.guilds = True
    intents.members = True
    intents.presences = True

    client = discord.Client(intents=intents)
    ready = asyncio.Event()

    @client.event
    async def on_ready() -> None:  # noqa: ANN202
        try:
            line(OK, f"Connected as {client.user} (privileged intents enabled)")

            # fetch_user works even without a shared server: it confirms the
            # ID exists and whose it is.
            try:
                streamer = await client.fetch_user(cfg.discord_streamer_id)
                line(OK, f"The ID belongs to @{streamer.name}")
            except discord.NotFound:
                line(BAD, f"No Discord user exists with the ID "
                          f"{cfg.discord_streamer_id}")
                return

            if not client.guilds:
                line(BAD, "The bot is not in any server. Invite it with:\n"
                          f"       https://discord.com/oauth2/authorize"
                          f"?client_id={client.user.id}&permissions=0&scope=bot")
                return
            line(OK, f"In {len(client.guilds)} server(s): "
                     f"{', '.join(g.name for g in client.guilds)}")

            member = None
            for guild in client.guilds:
                member = guild.get_member(cfg.discord_streamer_id)
                if member is not None:
                    break

            if member is None:
                line(BAD, f"@{streamer.name} is not in any of the bot's servers. "
                          "She has to be in the same server for her presence "
                          "to be read.")
                return

            line(OK, f"Shares the server '{member.guild.name}'")

            from src.discord_presence.bot import playing_name

            playing = playing_name(member)
            if playing:
                line(OK, f"Right now I see her playing: {playing}")
            elif str(member.status) == "offline":
                line(WARN, "She shows as offline on Discord: no presence to read. "
                           "Try again when she is online.")
            else:
                line(OK, "Presence visible, no game open right now")
        finally:
            ready.set()
            await client.close()

    try:
        await asyncio.wait_for(client.start(cfg.discord_token), timeout=30)
    except discord.PrivilegedIntentsRequired:
        line(BAD, "The privileged intents are missing. In "
                  "discord.com/developers > your app > Bot > "
                  "Privileged Gateway Intents, enable PRESENCE INTENT and "
                  "SERVER MEMBERS INTENT.")
    except discord.LoginFailure:
        line(BAD, "Invalid DISCORD_BOT_TOKEN. Get it from dev portal > Bot > Reset Token.")
    except asyncio.TimeoutError:
        if not ready.is_set():
            line(BAD, "Discord did not answer within 30 s")
    except Exception as exc:  # noqa: BLE001
        line(BAD, f"Failed to connect to Discord: {exc}")
    finally:
        if not client.is_closed():
            await client.close()


async def check_agent() -> None:
    header("Desktop agent")
    if not cfg.agent_token:
        line(WARN, "AGENT_TOKEN empty: the agent endpoint will not start "
                   "(the category comes from Discord only)")
        return
    if len(cfg.agent_token) < 16:
        line(BAD, "AGENT_TOKEN too short. Generate one with: "
                  'python -c "import secrets; print(secrets.token_urlsafe(32))"')
        return
    line(OK, f"Endpoint at http://{cfg.agent_host}:{cfg.agent_port}/game")

    # A port already in use is only discovered on startup, when it is too late.
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("" if cfg.agent_host == "0.0.0.0" else cfg.agent_host, cfg.agent_port))
        line(OK, f"Port {cfg.agent_port} is free")
    except OSError as exc:
        line(BAD, f"Cannot listen on {cfg.agent_host}:{cfg.agent_port} -> {exc}")
    finally:
        probe.close()

    agent_toml = cfg.root / "agent" / "agent.toml"
    if agent_toml.exists():
        content = agent_toml.read_text(encoding="utf-8", errors="replace")
        if cfg.agent_token in content:
            line(OK, "agent/agent.toml uses the same token")
        else:
            line(WARN, "agent/agent.toml has a different token than .env "
                       "(may be fine if that copy is only yours)")
    else:
        line(WARN, "No local agent/agent.toml (it is copied to the streamer's PC)")


async def check_riot(session: aiohttp.ClientSession) -> None:
    header("Riot / League of Legends")
    if not cfg.riot_api_key:
        line(WARN, "RIOT_API_KEY empty: !rank and !partida are disabled")
        return
    if not cfg.riot_id or "#" not in cfg.riot_id:
        line(BAD, "RIOT_ID empty or malformed (must be Name#TAG)")
        return
    name, _, tag = cfg.riot_id.rpartition("#")
    url = (f"https://{cfg.riot_region}.api.riotgames.com"
           f"/riot/account/v1/accounts/by-riot-id/{name}/{tag}")
    async with session.get(url, headers={"X-Riot-Token": cfg.riot_api_key}) as resp:
        if resp.status == 200:
            line(OK, f"{cfg.riot_id} resolved in region {cfg.riot_region}")
        elif resp.status in (401, 403):
            line(BAD, "API key rejected. Development keys expire every 24 h.")
        elif resp.status == 404:
            line(BAD, f"{cfg.riot_id} does not exist in region '{cfg.riot_region}'")
        else:
            line(WARN, f"Riot answered HTTP {resp.status}")


async def check_valorant(session: aiohttp.ClientSession) -> None:
    header("Valorant (HenrikDev)")
    if not cfg.henrik_api_key:
        line(WARN, "HENRIK_API_KEY empty: !valorant is disabled")
        return
    if not cfg.valorant_id or "#" not in cfg.valorant_id:
        line(BAD, "VALORANT_ID empty or malformed (must be Name#TAG)")
        return
    name, _, tag = cfg.valorant_id.rpartition("#")
    url = f"https://api.henrikdev.xyz/valorant/v3/mmr/{cfg.valorant_region}/pc/{name}/{tag}"
    async with session.get(url, headers={"Authorization": cfg.henrik_api_key}) as resp:
        if resp.status == 200:
            line(OK, f"{cfg.valorant_id} resolved in {cfg.valorant_region}")
        elif resp.status in (401, 403):
            line(BAD, "HenrikDev key rejected")
        elif resp.status == 404:
            line(WARN, f"HenrikDev did not find {cfg.valorant_id} in '{cfg.valorant_region}'")
        else:
            line(WARN, f"HenrikDev answered HTTP {resp.status}")


async def check_clips(session: aiohttp.ClientSession) -> None:
    header("Clips pipeline")
    if not cfg.clips_enabled:
        line(WARN, "CLIPS_ENABLED=false")
    try:
        import yt_dlp
        line(OK, f"yt-dlp {yt_dlp.version.__version__}")
    except ImportError:
        line(BAD, "yt-dlp not installed -> pip install -r requirements.txt")

    if not cfg.sa_path.exists():
        line(BAD, f"No service account JSON at {cfg.sa_path}")
        return
    line(OK, f"Service account at {cfg.sa_path}")
    if not cfg.gdrive_folder_id:
        line(BAD, "GDRIVE_FOLDER_ID empty")
        return

    from src.drive import DriveUploader

    drive = DriveUploader(session)
    try:
        token = await drive.token()
    except Exception as exc:  # noqa: BLE001
        line(BAD, f"Could not sign the Google token: {exc}")
        return
    url = f"https://www.googleapis.com/drive/v3/files/{cfg.gdrive_folder_id}"
    params = {"fields": "id,name,driveId,mimeType", "supportsAllDrives": "true"}
    async with session.get(
        url, headers={"Authorization": f"Bearer {token}"}, params=params
    ) as resp:
        body = await resp.json(content_type=None)
        if resp.status == 200:
            line(OK, f"Destination folder: {body.get('name')}")
            if body.get("driveId"):
                line(OK, "It is in a Shared Drive (correct)")
            else:
                line(WARN, "The folder is in 'My Drive'. Service accounts have no "
                           "quota there and the upload will fail with "
                           "storageQuotaExceeded. Move the folder to a Shared Drive.")
        elif resp.status == 404:
            line(BAD, "The folder does not exist or is not shared with the service account. "
                      f"Share it with the client_email in the JSON.")
        else:
            line(BAD, f"Drive answered HTTP {resp.status}: {body}")


async def main() -> int:
    print(f"Diagnostics for {cfg.twitch_channel or '???'}'s bot")
    if not (cfg.root / ".env").exists():
        print("\n[X] There is no .env. Copy .env.example to .env and fill it in.")
        return 1
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        await check_twitch(session)
        await check_discord()
        await check_agent()
        await check_riot(session)
        await check_valorant(session)
        await check_clips(session)
    print(f"\n{'=' * 40}")
    if _fails:
        print(f"{_fails} blocking problem(s). Check the lines marked [X].")
        return 1
    print("All set to start:  python run.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
