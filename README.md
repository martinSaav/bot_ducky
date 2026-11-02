# Moderation bot — twitch.tv/chaarqueen

Three subsystems in a single Python process:

| # | What it does | Enabled by |
|---|---|---|
| 1 | **Auto-categorizer** — detects which game is running and changes the Twitch category | `AGENT_TOKEN` and/or Discord |
| 2 | **Chat bot** — Twitch IRC with live stats commands (LoL / Valorant) | the `bot` role token |
| 3 | **Clips pipeline** — the day's clips → `yt-dlp` → Google Drive, every day | `GDRIVE_FOLDER_ID` + service account |

The auto-categorizer has **two sources** and uses the best one available:

| Source | How it detects | Priority |
|---|---|---|
| **Desktop agent** ([`agent/`](agent/README.md), in Rust) | looks at the processes on the streamer's PC | wins |
| **Discord presence** | reads the "Playing…" status | fallback |

If the agent stops reporting for 210 seconds, the bot falls back to Discord on its own.

Each one starts **only if its configuration is complete**. You can start with
the auto-categorizer and add the rest later: the process starts what it can and
logs what was left off.

## Language

The code, comments, logs and documentation are in English. What **viewers see
in Twitch** is in Spanish on purpose, because the channel's audience speaks
Spanish: command names (`!comandos`, `!resumen`, `!categoria`…), the bot's
replies and announcements, the prediction title and options ("¿Gana esta
partida?", "Gana", "Pierde") and the end-of-stream summary.

---

## Requirements

- Python 3.10 or newer (tested on 3.14)
- On the streamer's PC, only the agent from [`agent/`](agent/README.md): a ~1 MB
  executable with no runtime dependencies. It is optional — without it, the
  category is synced from Discord presence.
- To build the agent: Rust (`rustup`, GNU toolchain, no Visual Studio)

## Installation

Create the virtual environment:

```bash
python -m venv .venv
```

Activate it. This is the only part that depends on where you are:

```bash
source .venv/Scripts/activate        # Windows, Git Bash
source .venv/bin/activate            # Linux / macOS
```

```powershell
.venv/Scripts/Activate.ps1           # Windows, PowerShell
```

> **In PowerShell it will fail the first time** with *"running scripts is
> disabled on this system"*: Windows blocks scripts by default. Enable them once,
> without administrator rights, with
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
> Git Bash and Linux do not have this restriction.

With the environment active, the rest is the same everywhere:

```bash
pip install -r requirements.txt
```

```bash
cp .env.example .env
```

> **`cp` overwrites without asking.** This command is only for the first time:
> if you already had a `.env` with credentials filled in, it overwrites it and
> you lose them. In Git Bash you can use `cp -n`, which refuses to touch a file
> that already exists.
>
> If it already happened, not everything is lost: VS Code keeps a local history
> of every file you edited. Right-click the tab > **Open Timeline**, or look
> directly in `%APPDATA%\Code\User\History`.

After filling in the `.env`, check that everything is fine before starting:

```bash
python tools/doctor.py
```

The doctor tests every credential against its real API and tells you exactly
what is missing. `[X]` blocks startup, `[!]` only turns that feature off.

> **If you prefer not to activate the environment**, prefix every command with
> the interpreter's path: `.venv/Scripts/python.exe` on Windows,
> `.venv/bin/python` on Linux. In `cmd.exe` you also need to flip the slashes
> and use `copy` instead of `cp`.

---

## Who owns what

Everything is created and managed from **your** account. The streamer needs
neither a Twitch nor a Discord developer portal.

| Piece | Created in | What it needs from the streamer |
|---|---|---|
| Twitch application (dev.twitch.tv) | your account | to authorize once through OAuth |
| Discord bot (developer portal) | your account | her user ID, and to share a server with the bot |
| The bot's chat account (BotDucky) | a new account, yours | nothing |
| Riot API / HenrikDev | your account | her Riot ID (`Name#TAG`), which is public |
| Google Cloud + Drive | your account | nothing |
| Desktop agent | your machine, compiled | to leave it running on her PC |

The streamer only does three things, all of them once: **authorize the Twitch
app** with her account, **join the Discord server** and **keep the agent
installed**. Nothing else goes through her.

A Discord user ID or a Riot ID is public data: anyone who has her added can see
it. Having them gives no access to her account.

---

## Configuration

### 1. Twitch application

At <https://dev.twitch.tv/console/apps> create an application:

- **OAuth Redirect URLs**: `http://localhost:3000/callback` — it must match
  `TWITCH_REDIRECT_URI` in `.env` *character for character*
- **Category**: Chat Bot

Copy the Client ID and Client Secret into `.env`.

### 2. OAuth authorization

You authorize **twice**, with two different accounts:

```bash
python tools/auth_twitch.py broadcaster
```

The broadcaster also needs the `channel:manage:predictions` scope to create
predictions. If you add this feature to an existing install, run the command
above again to renew that permission.

```bash
python tools/auth_twitch.py bot
```

The script starts a local server, opens your browser, captures the `?code=` and
stores the access + refresh token in `data/tokens.json`. **From then on the
refresh is automatic**: Twitch rotates the refresh token on every use and the bot
stores it on its own.

| Role | Who runs it | Scopes | What for |
|---|---|---|---|
| `broadcaster` | **the streamer**, with her account | `channel:manage:broadcast`, `channel:manage:predictions`, `clips:edit` | change the category, run predictions and create clips |
| `bot` | the account that writes in chat | `chat:read`, `chat:edit` | read and reply in chat |

#### You cannot just send her the link

The redirect points to `http://localhost:3000/callback`, that is, **the machine
where the script runs**. If you send her the link and she opens it at home,
Twitch redirects her to *her* localhost, where nothing is listening: the code is
lost and never reaches your `data/tokens.json`.

There are two ways around it:

**1. She signs in on your machine** (the simplest)

Open a **private window** in the browser, run the script, paste the link there
and she types in her details. You do not see her password, and your own Twitch
session is untouched because the private window does not share it. When done,
close the window.

**2. She runs the script on her PC and sends you the result**

She needs Python installed, and you give her the project with a `.env` holding
`TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `TWITCH_CHANNEL` and
`TWITCH_REDIRECT_URI`. She runs `python tools/auth_twitch.py broadcaster` and
sends you the resulting `data/tokens.json`.

It is more awkward and **you are giving her your client secret**, which is what
identifies your application to Twitch. With trusted people it is fine, but
option 1 avoids the problem.

> **The script saves nothing if the account does not match.** If you authorize
> with the wrong account it warns you and leaves your previous token untouched,
> so you do not lose a good one to an extra open session. If you really meant
> that account, use `--force`.
>
> And `run.py` refuses to start if the broadcaster token does not belong to the
> `TWITCH_CHANNEL` channel: carrying on would change the wrong channel's
> category.

If you are also going to use the streamer's account for chat, leave
`TWITCH_BOT_LOGIN` empty — you still need to run `auth_twitch.py bot` because the
scopes are different.

### 3. Naming the bot

The name lives in three different places:

| Where | Who sees it | How it is set |
|---|---|---|
| Chat account | chat, on every message | register a Twitch account with that name and run `python tools/auth_twitch.py bot` with it |
| Application name | the streamer, when authorizing | the **Name** field at dev.twitch.tv |
| Logs | you | `BOT_NAME` in `.env` |

The bot's messages go out under its Twitch account name, with no extra prefix:

```
Categoria actualizada a: VALORANT
Comandos: !lrank !lmatch !vrank !vmatch !uptime !clip
```

`BOT_NAME` is kept for compatibility with older configurations, but it is no
longer prepended to messages. **The name chat sees on every message is the
Twitch account's.**

> **The category change is not attributed to anyone.** Twitch does not expose
> who modified the channel information, neither in the API nor in the UI — the
> category simply changes. The only visible trace is the message the bot sends to
> chat, and that is turned off with `AUTOCAT_ANNOUNCE=false`.

### 4. Discord (auto-categorizer, fallback source)

1. Create an application at <https://discord.com/developers/applications> → **Bot**
2. In the **Bot** tab → **Privileged Gateway Intents**, enable **PRESENCE INTENT**
   and **SERVER MEMBERS INTENT**. Without them Discord rejects the bot's
   connection. `tools/doctor.py` checks this by really connecting; no need to
   eyeball it.
3. Invite the bot to a private server where only it and the streamer are.
   It needs no permissions: it only has to be in the server.
4. With Developer Mode on, right-click the streamer → **Copy User ID** → it goes
   in `DISCORD_STREAMER_ID`.

On the streamer's side, in Discord, the option that shows the current game as a
status has to be on ("Mostrar el juego actual como mensaje de estado", under
the activity settings, in her Spanish client). Without it there is no presence
to read.

> This source is optional if you use the desktop agent, which is more reliable.
> It is still worth having: it covers the times when her PC is not running the
> agent.

**How the category change works:**

1. Discord reports `"Playing League of Legends"`.
2. The bot waits `PRESENCE_DEBOUNCE_SECONDS` (45 by default) and **checks the
   activity again**. That way alt-tabbing to the launcher does not trigger three
   changes.
3. It looks the name up in `config/game_map.json`; if it is not there, it queries
   `/helix/search/categories` and only accepts the result if the similarity is
   above 0.82. If it is not, **it changes nothing** — it would rather keep the old
   category than set a wrong one.
4. It caches the result (hits and misses) in `data/state.json`.
5. `PATCH /helix/channels` with the broadcaster token.

Try it first with `AUTOCAT_DRY_RUN=true`: it logs what it would do without
touching Twitch.

### 5. Desktop agent (primary source)

The [`agent/`](agent/README.md) project is a small Rust program that runs on the
streamer's PC and reports which game is open by looking at the processes. It is
more reliable than Discord presence: it does not depend on her having a setting
right or on the game being in Discord's database.

**It does not touch Twitch or store credentials.** It only says "VALORANT is
running now" and that is it. It works with an allowlist: only the games declared
in its `agent.toml` can be reported; any other open program is discarded on the
machine and never leaves it.

On the bot's side, all it takes is generating a shared secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

That value goes in `AGENT_TOKEN` in `.env` and in `token` in `agent.toml`. The
bot starts the endpoint on `AGENT_PORT` (8787 by default).

**Network:** since the bot runs on your PC and the agent on hers, they need to
see each other. **Tailscale** is the simplest: install it on both with the same
account and it builds a private network, without opening ports on the router.
The IP that goes in `agent.toml` comes from `tailscale ip -4`.

To check from her PC that it gets through: `http://100.x.y.z:8787/health`
should return `{"ok": true, ...}`.

The rest — building, configuring, adding games, automatic start — is in
[`agent/README.md`](agent/README.md).

### 6. Riot API (LoL commands)

Request the key at <https://developer.riotgames.com>.

> **Watch out:** the *Development Key* **expires every 24 hours**. For a bot that
> runs unattended, request a **Personal API Key** ("Register Product" form →
> Personal). It is approved within days and does not expire. With the
> development key you will have to renew it by hand every morning.

`RIOT_ID` is the full Riot ID (`Name#TAG`), not the old summoner name.
For LAS: `RIOT_PLATFORM=la2` and `RIOT_REGION=americas`.

The commands use `Account-V1` (Riot ID → PUUID), `League-V4` (rank) and
`Spectator-V5` (match in progress), cached for 90 s / 30 s to save the quota.

### 7. Valorant (HenrikDev)

Riot gives no public access to the Valorant API, so the community wrapper is
used: <https://docs.henrikdev.xyz>. The key is requested on their Discord and
goes in `HENRIK_API_KEY`.

It is a third-party API: it can change without notice. The client parses the
responses defensively and tries `v3` → `v2` for the rank and `v4` → `v3` for the
matches, so a version change on their side does not leave you without the
command.

### 8. Google Drive (clips pipeline)

1. Create a project at <https://console.cloud.google.com> and **enable the Google Drive API**.
2. **IAM & Admin → Service Accounts → Create**. It does not need any roles.
3. On the account you created: **Keys → Add key → JSON**. Save it to
   `secrets/service-account.json`.

> ### ⚠️ The mistake that catches everyone
>
> **Service accounts have no storage quota in Drive.** If you point
> `GDRIVE_FOLDER_ID` at a folder in "My Drive" — even shared with editor
> permission — the upload fails with `storageQuotaExceeded`.
>
> **The fix:** the destination folder has to be in a **Shared Drive**, with the
> JSON's `client_email` added as **Content manager**. That way the files are
> owned by the shared drive, not by the service account.
>
> `tools/doctor.py` detects this and warns you before a whole run fails.

`GDRIVE_FOLDER_ID` is what comes after `/folders/` in the folder's URL. Share
that folder with the editor and you are done.

**How the daily run works:**

1. `GET /helix/clips` with `started_at` = now − `CLIPS_LOOKBACK_HOURS`, paging.
2. Drops the ones already uploaded (`data/state.json`) and the ones below `CLIPS_MIN_VIEWS`.
3. Sorts by views and keeps the first `CLIPS_MAX`.
4. `yt-dlp` downloads the `.mp4` (Twitch only exposes the clip's URL, not the file).
5. **Resumable upload in 2 MB chunks** to Drive. If the network drops, it asks
   Google how many bytes it received and continues from there, with up to 5
   retries and exponential backoff. A brief network drop does not bring down a
   200 MB upload.
6. Deletes the local file and records the clip as uploaded.

The file is named `2026-09-08_0142v_Clip title_ClipId.mp4` — sorted by date and
with the views up front, so the editor sees what performed best first. The file
description includes the clip's author, the views and the original link.

---

## Usage

Start everything:

```bash
python run.py
```

With the bot running, open another terminal in the project folder and run:

```bash
python tools/chat_console.py
```

Type the message and press Enter to send it to the channel. `:quit` (or `:salir`)
closes only that console. The client uses the existing IRC connection and its
rate limit, so it does not open another Twitch session.

Discord presence is re-checked every 60 seconds even if there is no new event.
That makes it possible to notice that a LoL or Valorant match started after the
game was opened.

### Chat summary

While running, the bot keeps the latest messages in memory. The `!resumen`
command shows a first structured version:

```text
!resumen       # last hour
!resumen 30m   # last 30 minutes
!resumen 2h    # last 2 hours
```

It includes the detected game, frequent words as approximate topics, and the
participants with the most messages.

When the channel goes from online to offline, the bot generates an LLM summary
of the session and writes it to `logs/bot.log` as `Final stream summary`.
If PostgreSQL is active, it also stores the session in `stream_sessions` with
its start, end, game, message count and summary.

**Semantic summary with an LLM:** set `LLM_API_KEY` to a key compatible with the
OpenAI API (e.g. Gemini). `!resumen` prefers the messages stored in PostgreSQL
(sampled evenly across the window when there are more than `LLM_MAX_MESSAGES`)
and falls back to the in-memory summary if the API fails or the key is empty.
The client uses a 45-second timeout and retries with exponential backoff.

The embeddings in `chat_embeddings` are generated and stored, ready for semantic
search (`EmbeddingWorker.search`), but no command uses them yet.

**Persistence across restarts:** set `DATABASE_URL` to a PostgreSQL database
with the `pgvector` extension installed. The worker stores the messages and
generates their embeddings in the background, without blocking the Twitch
connection. The schema (tables `chat_messages`, `chat_embeddings` and
`stream_sessions`) is created automatically at startup.

Nightbot and the bot's own account are excluded from the history automatically.
You can add other accounts in `CHAT_IGNORED_AUTHORS` (in `.env`),
comma-separated.

### Predictions

When it detects a League of Legends or VALORANT match, the bot creates a Twitch
prediction with the options `Gana` and `Pierde`. It does not create another one
while one is open (active or locked), and it lasts 5 minutes by default.

**The match state comes from the game's local client**, read by the agent on
the streamer's PC. It does not depend on any API key:

| Game | Created | Resolved |
|---|---|---|
| League of Legends | When the match starts (not in champion select, because of dodges) | With the local client's result |
| Valorant | On entering agent select | By hand with `!vwin` / `!vloss` |

The public APIs remain as a fallback when the agent has no data from the
client. For LoL, Spectator-V5 to know whether there is a match and Match-V5 for
the result: they use the same `gameId` as the local client, so nothing is
duplicated if the agent goes down mid-match. Those two need a valid Riot key. If
there is no result from anywhere, `!vwin` / `!vloss` resolve any open
prediction, LoL or Valorant.

`!vgame` still works to force a Valorant prediction by hand.

The details of what the agent queries on each client are in
[`docs/agent.md`](docs/agent.md).

### Other tools

Manual clips run, without waiting for the schedule:

```bash
python tools/run_clips.py
```

See which clips it would pick, without downloading or uploading anything:

```bash
python tools/run_clips.py --list --hours 72
```

Diagnostics for the whole configuration:

```bash
python tools/doctor.py
```

Logs go to the console and to `logs/bot.log` (rotating, 5 files of 5 MB).

---

## Chat commands

The commands and their replies are in Spanish because viewers use them.

| Command | Aliases | What it returns |
|---|---|---|
| `!lrank` | `!rank` `!elo` `!lol` | SoloQ and Flex rank with LP and win rate |
| `!lmatch` | `!partida` `!live` `!game` | Champion, queue, duration and the 5 opponents |
| `!vrank` | `!valorant` `!val` | Current rank, RR, change from the last match and peak |
| `!vmatch` | `!ultima` | Map, agent, K/D/A and result of the last match |
| `!uptime` | | Time live |
| `!clip` | | Creates a clip (30 s cooldown) |
| `!comandos` | `!ayuda` | Lists the available commands |

The first three accept a Riot ID: `!lrank Alguien#LAS` looks up that account
instead of the streamer's.

**Moderators only:**

| Command | What it does |
|---|---|
| `!categoria <juego>` | Changes the category by hand (with no argument, shows the current one) |
| `!gamef <juego>` | Pins the category for 4 hours; the auto-categorizer will not override it |
| `!auto on` / `!auto off` | Turns the auto-categorizer on and off live. With no argument it says which sources are alive |
| `!recargar` | Re-reads `config/game_map.json` without restarting the bot |
| `!vgame` | Manually confirms a Valorant match and starts the prediction |
| `!vwin` / `!vloss` | Manually resolves the prediction as a win or a loss |
| `!vcancel` | Cancels the current prediction and clears its state |

Behavior details:

- Commands have a global cooldown (8–30 s depending on the command). **Mods and
  VIPs skip it**, so they can fix something right away without waiting.
- A command that does not exist, or a mod command requested by a viewer,
  **gets no reply**: there is no point spamming chat with "you do not have
  permission".
- A manual `!categoria` tells the auto-categorizer, so it does not override your
  change on the next presence update.
- If an API goes down, the bot replies with the reason in plain language and
  keeps working. No command error brings the process down.

---

## `config/game_map.json`

Maps the name Discord reports to the **exact** name of the Twitch category:

```json
"map": { "counter-strike 2": "Counter-Strike" }
```

You only need to add entries when the automatic search fails or is ambiguous.
It ships with ~40 games and an `ignore` list (Spotify, VS Code, OBS, browsers)
so opening the editor does not change your category.

After editing it: `!recargar` in chat, no restart needed.

> **Known case:** TFT runs inside the LoL client, so Discord usually reports
> `"League of Legends"` instead of `"Teamfight Tactics"`. If that is a problem,
> use `!categoria Teamfight Tactics` by hand.

---

## Keeping it running

**Windows (Task Scheduler)** — create a task:

- Trigger: *At startup*
- Action: `C:\Users\Martin\Desktop\projects\twitch\.venv\Scripts\pythonw.exe`
- Arguments: `run.py`
- Start in: `C:\Users\Martin\Desktop\projects\twitch`
- Check *Run whether user is logged on or not*

With `pythonw.exe` no console stays open; the logs still go to `logs/bot.log`.

**VPS (Linux, systemd)**

On Linux the environment's interpreter is at `.venv/bin/python` (not in
`.venv/Scripts/`). The service calls it by absolute path, so nothing needs to be
activated. In `/etc/systemd/system/chaarbot.service`:

```ini
[Unit]
Description=chaarqueen's bot
After=network-online.target

[Service]
WorkingDirectory=/opt/chaarbot
ExecStart=/opt/chaarbot/.venv/bin/python run.py
Restart=always
RestartSec=10
User=chaarbot

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now chaarbot
journalctl -u chaarbot -f
```

Three things to keep in mind when moving it to a Linux VPS:

- **Redirect URI** — add the new one at dev.twitch.tv, or do the authorization on
  your machine and copy `data/tokens.json` to the server (simpler).
- **Agent port** — if you keep using the desktop agent, open 8787 only towards
  the tailnet: `sudo ufw allow in on tailscale0 to any port 8787`. Do not expose
  it to the internet: the agent speaks plain HTTP, which is fine inside Tailscale
  but not outside it.
- **`ffmpeg` is not needed** — the pipeline downloads clips as a single mp4
  file, with no tracks to merge.

---

## Common problems

| Symptom | Cause |
|---|---|
| `Login authentication failed` in chat | The `bot` role token expired or was revoked. The bot refreshes it on its own and reconnects; if it keeps happening, run `auth_twitch.py bot` again. |
| The category never changes | The PRESENCE INTENT is missing, or the streamer has activity detection off, or she does not share a server with the bot. Check the log: it says which of the three. |
| `storageQuotaExceeded` | The Drive folder is not in a Shared Drive. See the Google Drive section. |
| `!lrank` replies that the key expired | Riot's Development Key lasts 24 h. Request a Personal Key. |
| The agent does not reach the bot | Try `http://IP:8787/health` from her PC. If it does not answer: Tailscale is down, `AGENT_HOST` is not `0.0.0.0`, or the Windows firewall blocks the port. |
| The agent reports but nothing changes | Different token in `.env` and `agent.toml` (a 401 shows up in the agent log), or `!auto off` is active. |
| A prediction is not created | Run `twitch-game-agent.exe --once` on her PC with the game open: it shows what the local client reports. |
| `invalid client` when authorizing | The redirect URI in `.env` does not exactly match the one at dev.twitch.tv. |
| The bot does not reply in chat | The bot account may be restricted by follower/subscriber modes. Make it a channel mod — it also raises the send limit from 20 to 100 messages per 30 s. |

---

## Layout

```
run.py                      orchestrator: starts the three subsystems
config/game_map.json        Discord -> Twitch category
src/
  config.py                 configuration from .env
  storage.py                JSON with atomic writes (tokens, state)
  category.py               category resolution + PATCH to the channel
  drive.py                  resumable upload to Google Drive
  services.py               dependency container
  gamesource.py             arbiter between the agent and Discord; predictions
  agent_server.py           HTTP endpoint that receives the agent
  twitch/
    auth.py                 OAuth2, automatic refresh, app token
    helix.py                API client (retries, rate limit, 401)
    chat.py                 IRC over WebSocket, no external dependencies
  discord_presence/bot.py   Discord presence (fallback source)
  riot/lol.py               Account-V1, League-V4, Spectator-V5, Data Dragon
  riot/valorant.py          HenrikDev with version fallback
  commands/registry.py      commands, permissions and cooldowns
  clips/pipeline.py         daily selection, download and upload
agent/                      desktop agent in Rust (see its README)
docs/                       architecture, agent and embeddings diagrams
tools/
  auth_twitch.py            one-off OAuth flow
  doctor.py                 diagnostics for the whole configuration
  run_clips.py              manual pipeline run
  chat_console.py           send chat messages through the running bot
```

The only thing that runs on the streamer's PC is the agent from `agent/`, which
is optional and only reports games from an allowlist. Everything else lives on
your side.
