"""One-off OAuth flow to capture the Twitch tokens.

Usage:
    python tools/auth_twitch.py broadcaster   # run by the streamer
    python tools/auth_twitch.py bot           # account that writes in chat

Starts a local server on the redirect URI, opens the browser, captures the
?code= and stores the access + refresh token in data/tokens.json.
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
        print(f"[X] Missing in .env: {', '.join(missing)}")
        return 1
    if role not in twitch_auth.SCOPES:
        print(f"[X] Invalid role '{role}'. Use: {' | '.join(twitch_auth.SCOPES)}")
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
                done.set_exception(RuntimeError(f"Authorization rejected: {desc}"))
            return _page("Authorization cancelled", desc, ok=False)
        # The state protects against a third party making you exchange their code.
        if request.query.get("state") != state:
            return _page("Invalid state", "The request does not match this session.", ok=False)
        code = request.query.get("code")
        if not code:
            return _page("Missing code", "Twitch did not return a code.", ok=False)
        if not done.done():
            done.set_result(code)
        return _page(
            "Done",
            "You can close this tab and go back to the terminal.",
        )

    app = web.Application()
    app.router.add_get(path, handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    try:
        await site.start()
    except OSError as exc:
        print(f"[X] Could not listen on {host}:{port} -> {exc}")
        print("    Close whatever is using that port or change TWITCH_REDIRECT_URI")
        print("    (remember to also update the OAuth Redirect URL at dev.twitch.tv).")
        await runner.cleanup()
        return 1

    url = twitch_auth.authorize_url(role, state)
    print(f"\n=== Twitch authorization for role: {role} ===")
    print(f"Scopes: {', '.join(twitch_auth.SCOPES[role])}")
    print("\nOpen this link with the right account and sign in:\n")
    print(url + "\n")
    webbrowser.open(url)
    print(f"Waiting for the redirect at {cfg.twitch_redirect_uri} ... (Ctrl+C to cancel)")

    try:
        code = await asyncio.wait_for(done, timeout=300)
    except asyncio.TimeoutError:
        print("[X] Timed out (5 min) waiting for the authorization.")
        await runner.cleanup()
        return 1
    except RuntimeError as exc:
        print(f"[X] {exc}")
        await runner.cleanup()
        return 1
    finally:
        # Give the browser a moment to receive the HTML response.
        await asyncio.sleep(0.5)

    try:
        async with aiohttp.ClientSession() as session:
            tokens = await twitch_auth.exchange_code(session, code)
            info = await twitch_auth.validate(session, tokens["access_token"])
    except twitch_auth.TwitchAuthError as exc:
        print(f"\n[X] {exc}")
        print("    Check that these match exactly at dev.twitch.tv:")
        print("      - Client ID and Client Secret, with the ones in .env")
        print(f"      - the OAuth Redirect URL, with {cfg.twitch_redirect_uri}")
        await runner.cleanup()
        return 1

    login = info.get("login", "")
    expected = cfg.twitch_channel if role == "broadcaster" else cfg.bot_login
    force = "--force" in sys.argv

    if expected and login.lower() != expected.lower() and not force:
        # Nothing is saved: overwriting a good token with another account's
        # leaves the bot pointing at the wrong channel, and that goes unnoticed
        # until it acts.
        print(f"\n[X] You authorized as '{login}' but role '{role}' expects "
              f"'{expected}'.")
        print("    NOTHING was saved; your previous token is untouched.")
        print()
        print("    To authorize with the right account:")
        print("      1. Open a private browser window, or sign out of twitch.tv")
        print(f"      2. Run again:  python tools/auth_twitch.py {role}")
        print(f"      3. Sign in as '{expected}'")
        print()
        print("    If you really meant this account:  --force")
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

    print(f"\n[OK] Token saved to {cfg.token_file}")
    print(f"     Account: {login}  (user_id {info.get('user_id')})")
    print(f"     Scopes: {', '.join(info.get('scopes', []))}")

    if force and expected and login.lower() != expected.lower():
        print(f"\n[!] Saved with --force even though '{expected}' was expected.")
    return 0


def main() -> int:
    role = sys.argv[1].lower() if len(sys.argv) > 1 else "broadcaster"
    try:
        return asyncio.run(run(role))
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
