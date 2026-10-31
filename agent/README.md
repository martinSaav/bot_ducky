# Desktop agent

A small Rust program that runs on the streamer's PC. Every 5 seconds it checks
whether a game from a list is running and tells the bot, which is the one that
changes the channel category.

**It does not touch Twitch.** It stores no tokens, does not have the client
secret, and knows nothing about anyone's account. It only says "VALORANT is
running now" and that is it.

## Why it exists

Discord presence works, but it depends on her having activity detection turned
on, on Discord being open, and on the game being in Discord's database. Looking
at the process is direct: if `cs2.exe` is in memory, she is playing CS2.

The agent **wins over** Discord when both are active. If the agent goes down or
the PC has it off, the bot falls back to Discord presence on its own after 210
seconds without news.

## What is sent, exactly

```json
{
  "source": "agent",
  "game": "VALORANT",
  "exe": "VALORANT-Win64-Shipping.exe",
  "match_id": "val-1761867240000",
  "metadata": {
    "source": "presence",
    "phase": "in_game",
    "queue": "competitive",
    "score": { "ally": 7, "enemy": 4 }
  },
  "host": "CHAARS-PC",
  "agent_version": "0.1.0",
  "sent_at": "2026-10-30T23:34:00Z"
}
```

`match_id` and `metadata` only appear for Valorant and League, and they come from
each game's local client: which screen it is on, the match in progress and, in
LoL, who won. The details of what is queried are in
[`docs/agent.md`](../docs/agent.md).

That is all. **It works with an allowlist:** only the games declared in
`agent.toml` can show up in that field. Any other open program is discarded on
the spot and never leaves the machine. The process list, window titles and
anything else are not sent.

It sends when the game changes, and as a heartbeat every 60 seconds. It is always
the full state ("playing X" / "playing nothing"), never an incremental change: if
a message is lost, the next heartbeat fixes it on its own.

## Building

> **The agent is Windows-only.** It detects processes by `.exe` name and asks
> `kernel32` for the time, so it does not build on Linux. That is on purpose: it
> runs on the PC where the games are played. The bot, on the other hand, runs
> the same on Windows or on a Linux VPS — the agent talks to it over HTTP and
> does not care what system is on the other side.

```bash
cargo build --release
```

The result is `target/release/twitch-game-agent.exe` (~1 MB, no runtime
dependencies — nothing needs to be installed on the target PC).

If you use rustup's `x86_64-pc-windows-gnu` toolchain without Visual Studio, you
do not need a C compiler: the dependencies are chosen to avoid it. That is why
the time comes from kernel32 calls and the HTTPS queries to Riot's local APIs
(Valorant and LoL) are delegated to `curl.exe`, which ships with Windows 10/11:
no TLS stack has to be compiled in Rust.

> LoL used to use `Invoke-RestMethod -SkipCertificateCheck`, but that parameter
> only exists in PowerShell 7 and `powershell.exe` is version 5.1. It always
> failed, silently.

## Configuring

```bash
cp agent.example.toml agent.toml
```

> Same as with the bot's `.env`: `cp` overwrites the destination without
> asking. If you already had a configured `agent.toml`, use `cp -n` or copy it
> by hand.

Two things are required:

- **`endpoint`** — the bot's URL. With Tailscale, `tailscale ip -4` on the PC
  where the bot runs gives you the IP: `http://100.x.y.z:8787/game`
- **`token`** — the same value as `AGENT_TOKEN` in the bot's `.env`. Generate it with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Test it before leaving it running

```bash
./twitch-game-agent.exe --once
```

It says what it detects right now and exits. With a game open it should name it.
For Valorant and League it also shows what it reads from the local client (the
phase, the match in progress and any problem with the API): the first thing to
check when a prediction is not created.

```bash
./twitch-game-agent.exe --scan
```

Lists the running processes, to find out the executable of a missing game.
**It only prints on screen; it sends nothing anywhere.**

### Adding a game

Open the game, run `--scan`, find the executable and add it:

```toml
[[games]]
name = "Silksong"        # EXACT name of the Twitch category
priority = 20
processes = ["Silksong.exe"]
```

`priority` breaks ties when two rules match at once. That is why the Riot client
is at 10 and the match at 20: with both open, the match wins.

## Leaving it running on its own

```powershell
powershell -ExecutionPolicy Bypass -File install-autostart.ps1
```

`-ExecutionPolicy Bypass` is not optional: Windows blocks PowerShell scripts by
default, and without it the script fails with *"running scripts is disabled on
this system"*. The other option is right-clicking the `.ps1` > **"Run with
PowerShell"** ("Ejecutar con PowerShell" on a Spanish Windows), which does it on
its own.

It creates a scheduled task that starts the agent on sign-in, with no window. It
does not need administrator rights. Before installing anything it validates the
configuration, so you are not left with a task that will never work.

To remove it:

```powershell
powershell -ExecutionPolicy Bypass -File uninstall-autostart.ps1
```

While it runs it shows up in Task Manager as `twitch-game-agent.exe` and can be
closed from there. The log is in `agent.log`, next to the executable.

Every new line is sent right away, authenticated, to the backend's logs
endpoint. The dashboard shows them under the agent source; if the backend is not
available, they stay in the local file and are re-sent when the connection comes
back.

## Network

The bot listens on port 8787. Since it runs on Martin's PC and the agent on hers,
one has to reach the other. **Tailscale** is the simplest: install it on both PCs
with the same account and that is it — it builds a private network, without
opening ports on the router or exposing anything to the internet.

To check they can see each other, from her PC:

```
http://100.x.y.z:8787/health
```

It should return `{"ok": true, ...}`.

**About TLS:** the binary is built without HTTPS support on purpose. The
Tailscale tunnel is already encrypted with WireGuard, so HTTP inside the tailnet
does not travel in the clear over the internet. If the bot ever moves to a VPS
with a public URL, put a TLS reverse proxy in front of it, or rebuild `ureq`
with the `native-tls` feature. The agent rejects an `endpoint` with `https://`
instead of failing in an odd way on the first send.

## Layout

```
src/main.rs      CLI, main loop and logging
src/config.rs    reading and validating agent.toml
src/detect.rs    process enumeration and allowlist
src/report.rs    HTTP sending to the bot
src/clock.rs     date and time through kernel32, no dependencies
src/local.rs     curl, lockfile, base64 and diagnostics for the local APIs
src/valorant.rs  match state from the Riot Client's local presence
src/lol.rs       match state and result from the local client (LCU)
```
