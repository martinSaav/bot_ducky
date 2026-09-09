"""Diagnostico: revisa que cada pieza este bien configurada antes de arrancar.

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
        line(BAD, "Falta TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET en .env")
        return
    line(OK, f"App configurada (client_id ...{cfg.twitch_client_id[-6:]})")

    store = JsonStore(cfg.token_file)
    auth = twitch_auth.TwitchAuth(session, store)

    try:
        await auth.app_bearer()
        line(OK, "App access token: se obtiene bien")
    except Exception as exc:  # noqa: BLE001
        line(BAD, f"App access token fallo: {exc}")
        return

    for role, needed in twitch_auth.SCOPES.items():
        entry = store.get(role) or {}
        if not entry.get("refresh_token"):
            level = BAD if role == "broadcaster" else WARN
            line(level, f"Rol '{role}' sin autorizar -> python tools/auth_twitch.py {role}")
            continue
        try:
            token = await auth.bearer(role)
            info = await twitch_auth.validate(session, token)
        except Exception as exc:  # noqa: BLE001
            line(BAD, f"Rol '{role}': token invalido ({exc})")
            continue
        scopes = set(info.get("scopes") or [])
        faltan = [s for s in needed if s not in scopes]
        if faltan:
            line(BAD, f"Rol '{role}' ({info.get('login')}): faltan scopes {faltan} "
                      f"-> reautorizar")
        else:
            line(OK, f"Rol '{role}': {info.get('login')} (id {info.get('user_id')}), scopes OK")

        expected = cfg.twitch_channel if role == "broadcaster" else cfg.bot_login
        if expected and str(info.get("login", "")).lower() != expected.lower():
            line(WARN, f"Rol '{role}' autorizado como '{info.get('login')}' "
                       f"pero se esperaba '{expected}'")


async def check_discord() -> None:
    header("Discord (auto-categorizador)")
    if not cfg.discord_token:
        line(WARN, "DISCORD_BOT_TOKEN vacio: el auto-categorizador no arranca")
        return
    if not cfg.discord_streamer_id:
        line(BAD, "DISCORD_STREAMER_ID vacio: el bot no sabe a quien mirar")
        return
    try:
        import discord  # noqa: F401
    except ImportError:
        line(BAD, "discord.py no instalado -> pip install -r requirements.txt")
        return
    line(OK, f"Token cargado, vigilando al usuario {cfg.discord_streamer_id}")
    line(WARN, "Verifica a mano: PRESENCE INTENT y SERVER MEMBERS INTENT activados "
               "en dev portal > Bot")
    if not cfg.game_map_file.exists():
        line(WARN, f"No existe {cfg.game_map_file}; se usara solo la busqueda automatica")
    else:
        line(OK, "config/game_map.json presente")


async def check_agent() -> None:
    header("Agente de escritorio")
    if not cfg.agent_token:
        line(WARN, "AGENT_TOKEN vacio: el endpoint del agente no se levanta "
                   "(la categoria queda solo con Discord)")
        return
    if len(cfg.agent_token) < 16:
        line(BAD, "AGENT_TOKEN demasiado corto. Genera uno con: "
                  'python -c "import secrets; print(secrets.token_urlsafe(32))"')
        return
    line(OK, f"Endpoint en http://{cfg.agent_host}:{cfg.agent_port}/game")

    # Un puerto ya ocupado se descubre recien al arrancar, cuando ya es tarde.
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("" if cfg.agent_host == "0.0.0.0" else cfg.agent_host, cfg.agent_port))
        line(OK, f"El puerto {cfg.agent_port} esta libre")
    except OSError as exc:
        line(BAD, f"No puedo escuchar en {cfg.agent_host}:{cfg.agent_port} -> {exc}")
    finally:
        probe.close()

    agent_toml = cfg.root / "agent" / "agent.toml"
    if agent_toml.exists():
        contenido = agent_toml.read_text(encoding="utf-8", errors="replace")
        if cfg.agent_token in contenido:
            line(OK, "agent/agent.toml usa el mismo token")
        else:
            line(WARN, "agent/agent.toml tiene un token distinto al del .env "
                       "(puede ser normal si esa copia es solo tuya)")
    else:
        line(WARN, "No hay agent/agent.toml local (se copia a la PC del streamer)")


async def check_riot(session: aiohttp.ClientSession) -> None:
    header("Riot / League of Legends")
    if not cfg.riot_api_key:
        line(WARN, "RIOT_API_KEY vacio: !rank y !partida quedan desactivados")
        return
    if not cfg.riot_id or "#" not in cfg.riot_id:
        line(BAD, "RIOT_ID vacio o mal formado (debe ser Nombre#TAG)")
        return
    name, _, tag = cfg.riot_id.rpartition("#")
    url = (f"https://{cfg.riot_region}.api.riotgames.com"
           f"/riot/account/v1/accounts/by-riot-id/{name}/{tag}")
    async with session.get(url, headers={"X-Riot-Token": cfg.riot_api_key}) as resp:
        if resp.status == 200:
            line(OK, f"{cfg.riot_id} resuelto en la region {cfg.riot_region}")
        elif resp.status in (401, 403):
            line(BAD, "API key rechazada. Las keys de desarrollo vencen cada 24 h.")
        elif resp.status == 404:
            line(BAD, f"No existe {cfg.riot_id} en la region '{cfg.riot_region}'")
        else:
            line(WARN, f"Riot respondio HTTP {resp.status}")


async def check_valorant(session: aiohttp.ClientSession) -> None:
    header("Valorant (HenrikDev)")
    if not cfg.henrik_api_key:
        line(WARN, "HENRIK_API_KEY vacio: !valorant queda desactivado")
        return
    if not cfg.valorant_id or "#" not in cfg.valorant_id:
        line(BAD, "VALORANT_ID vacio o mal formado (debe ser Nombre#TAG)")
        return
    name, _, tag = cfg.valorant_id.rpartition("#")
    url = f"https://api.henrikdev.xyz/valorant/v3/mmr/{cfg.valorant_region}/pc/{name}/{tag}"
    async with session.get(url, headers={"Authorization": cfg.henrik_api_key}) as resp:
        if resp.status == 200:
            line(OK, f"{cfg.valorant_id} resuelto en {cfg.valorant_region}")
        elif resp.status in (401, 403):
            line(BAD, "Key de HenrikDev rechazada")
        elif resp.status == 404:
            line(WARN, f"HenrikDev no encontro {cfg.valorant_id} en '{cfg.valorant_region}'")
        else:
            line(WARN, f"HenrikDev respondio HTTP {resp.status}")


async def check_clips(session: aiohttp.ClientSession) -> None:
    header("Pipeline de clips")
    if not cfg.clips_enabled:
        line(WARN, "CLIPS_ENABLED=false")
    try:
        import yt_dlp
        line(OK, f"yt-dlp {yt_dlp.version.__version__}")
    except ImportError:
        line(BAD, "yt-dlp no instalado -> pip install -r requirements.txt")

    if not cfg.sa_path.exists():
        line(BAD, f"No existe el JSON de service account en {cfg.sa_path}")
        return
    line(OK, f"Service account en {cfg.sa_path}")
    if not cfg.gdrive_folder_id:
        line(BAD, "GDRIVE_FOLDER_ID vacio")
        return

    from src.drive import DriveUploader

    drive = DriveUploader(session)
    try:
        token = await drive.token()
    except Exception as exc:  # noqa: BLE001
        line(BAD, f"No se pudo firmar el token de Google: {exc}")
        return
    url = f"https://www.googleapis.com/drive/v3/files/{cfg.gdrive_folder_id}"
    params = {"fields": "id,name,driveId,mimeType", "supportsAllDrives": "true"}
    async with session.get(
        url, headers={"Authorization": f"Bearer {token}"}, params=params
    ) as resp:
        body = await resp.json(content_type=None)
        if resp.status == 200:
            line(OK, f"Carpeta destino: {body.get('name')}")
            if body.get("driveId"):
                line(OK, "Esta en una Unidad compartida (correcto)")
            else:
                line(WARN, "La carpeta esta en 'Mi unidad'. Las service accounts no "
                           "tienen cuota ahi y la subida va a fallar con "
                           "storageQuotaExceeded. Mové la carpeta a una Unidad compartida.")
        elif resp.status == 404:
            line(BAD, "La carpeta no existe o no esta compartida con la service account. "
                      f"Compartila con el client_email del JSON.")
        else:
            line(BAD, f"Drive respondio HTTP {resp.status}: {body}")


async def main() -> int:
    print(f"Diagnostico del bot de {cfg.twitch_channel or '???'}")
    if not (cfg.root / ".env").exists():
        print("\n[X] No existe .env. Copia .env.example a .env y completalo.")
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
        print(f"{_fails} problema(s) bloqueante(s). Revisa las lineas con [X].")
        return 1
    print("Todo listo para arrancar:  python run.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
