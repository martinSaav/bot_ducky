"""Flujo OAuth de un solo uso para capturar los tokens de Twitch.

Uso:
    python tools/auth_twitch.py broadcaster   # lo corre el streamer
    python tools/auth_twitch.py bot           # cuenta que escribe en el chat

Levanta un servidor local en la redirect URI, abre el navegador, captura
el ?code= y guarda access + refresh token en data/tokens.json.
"""
from __future__ import annotations

import asyncio
import secrets
import sys
import urllib.parse
import webbrowser
from pathlib import Path

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import cfg  # noqa: E402
from src.storage import JsonStore  # noqa: E402
from src.twitch import auth as twitch_auth  # noqa: E402

PAGE = """<!doctype html><meta charset="utf-8">
<title>{title}</title>
<style>
 body{{font-family:system-ui,sans-serif;background:#0e0e10;color:#efeff1;
      display:grid;place-items:center;height:100vh;margin:0;text-align:center}}
 .card{{max-width:32rem;padding:2rem}}
 h1{{color:{color};font-size:1.4rem;margin:0 0 .5rem}}
 p{{color:#adadb8;line-height:1.5}}
</style>
<div class="card"><h1>{title}</h1><p>{msg}</p></div>
"""


def _page(title: str, msg: str, ok: bool = True) -> web.Response:
    return web.Response(
        text=PAGE.format(title=title, msg=msg, color="#00c8a0" if ok else "#ff6b6b"),
        content_type="text/html",
    )


async def run(role: str) -> int:
    missing = cfg.missing("twitch_client_id", "twitch_client_secret", "twitch_channel")
    if missing:
        print(f"[X] Falta configurar en .env: {', '.join(missing)}")
        return 1
    if role not in twitch_auth.SCOPES:
        print(f"[X] Rol invalido '{role}'. Usa: {' | '.join(twitch_auth.SCOPES)}")
        return 1

    parsed = urllib.parse.urlparse(cfg.twitch_redirect_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"

    state = secrets.token_urlsafe(24)
    store = JsonStore(cfg.token_file)
    done: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def handler(request: web.Request) -> web.Response:
        if request.query.get("error"):
            desc = request.query.get("error_description", request.query["error"])
            if not done.done():
                done.set_exception(RuntimeError(f"Autorizacion rechazada: {desc}"))
            return _page("Autorizacion cancelada", desc, ok=False)
        # El state protege contra que un tercero te haga canjear su code.
        if request.query.get("state") != state:
            return _page("State invalido", "La peticion no coincide con esta sesion.", ok=False)
        code = request.query.get("code")
        if not code:
            return _page("Falta el code", "Twitch no devolvio un codigo.", ok=False)
        if not done.done():
            done.set_result(code)
        return _page(
            "Listo",
            "Ya podes cerrar esta pestana y volver a la terminal.",
        )

    app = web.Application()
    app.router.add_get(path, handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    try:
        await site.start()
    except OSError as exc:
        print(f"[X] No se pudo escuchar en {host}:{port} -> {exc}")
        print("    Cerra lo que este usando ese puerto o cambia TWITCH_REDIRECT_URI")
        print("    (acordate de actualizar tambien la OAuth Redirect URL en dev.twitch.tv).")
        await runner.cleanup()
        return 1

    url = twitch_auth.authorize_url(role, state)
    print(f"\n=== Autorizacion de Twitch para el rol: {role} ===")
    print(f"Scopes: {', '.join(twitch_auth.SCOPES[role])}")
    print("\nAbri este link con la cuenta correcta e inicia sesion:\n")
    print(url + "\n")
    webbrowser.open(url)
    print(f"Esperando el redirect en {cfg.twitch_redirect_uri} ... (Ctrl+C para cancelar)")

    try:
        code = await asyncio.wait_for(done, timeout=300)
    except asyncio.TimeoutError:
        print("[X] Se agoto el tiempo (5 min) esperando la autorizacion.")
        await runner.cleanup()
        return 1
    except RuntimeError as exc:
        print(f"[X] {exc}")
        await runner.cleanup()
        return 1
    finally:
        # Damos un instante para que el navegador reciba la respuesta HTML.
        await asyncio.sleep(0.5)

    try:
        async with aiohttp.ClientSession() as session:
            tokens = await twitch_auth.exchange_code(session, code)
            info = await twitch_auth.validate(session, tokens["access_token"])
    except twitch_auth.TwitchAuthError as exc:
        print(f"\n[X] {exc}")
        print("    Revisa que en dev.twitch.tv coincidan exactamente:")
        print("      - Client ID y Client Secret con los del .env")
        print(f"      - la OAuth Redirect URL con {cfg.twitch_redirect_uri}")
        await runner.cleanup()
        return 1

    login = info.get("login", "")
    expected = cfg.twitch_channel if role == "broadcaster" else cfg.bot_login
    forzar = "--force" in sys.argv

    if expected and login.lower() != expected.lower() and not forzar:
        # No guardamos: pisar un token correcto con uno de otra cuenta deja el
        # bot apuntando al canal equivocado, y eso no se nota hasta que actua.
        print(f"\n[X] Autorizaste con '{login}' pero para el rol '{role}' se esperaba "
              f"'{expected}'.")
        print("    NO se guardo nada, tu token anterior sigue intacto.")
        print()
        print("    Para autorizar con la cuenta correcta:")
        print("      1. Abri una ventana privada del navegador, o cerra sesion en twitch.tv")
        print(f"      2. Volve a correr:  python tools/auth_twitch.py {role}")
        print(f"      3. Inicia sesion como '{expected}'")
        print()
        print("    Si de verdad querias esta cuenta:  --force")
        await runner.cleanup()
        return 1

    import time

    store.set_sync(
        role,
        {
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "expires_at": time.time() + float(tokens.get("expires_in", 14400)),
            "scopes": info.get("scopes", []),
            "login": info.get("login", ""),
            "user_id": info.get("user_id", ""),
        },
    )
    await runner.cleanup()

    print(f"\n[OK] Token guardado en {cfg.token_file}")
    print(f"     Cuenta: {login}  (user_id {info.get('user_id')})")
    print(f"     Scopes: {', '.join(info.get('scopes', []))}")

    if forzar and expected and login.lower() != expected.lower():
        print(f"\n[!] Guardado con --force pese a que se esperaba '{expected}'.")
    return 0


def main() -> int:
    role = sys.argv[1].lower() if len(sys.argv) > 1 else "broadcaster"
    try:
        return asyncio.run(run(role))
    except KeyboardInterrupt:
        print("\nCancelado.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
