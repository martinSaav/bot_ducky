//! League of Legends match state, read from the local client (LCU).
//!
//! The League client exposes an API on 127.0.0.1, protected by the password
//! in the lockfile it leaves in its install folder:
//!
//!   GET /lol-gameflow/v1/gameflow-phase     -> which screen it is on
//!   GET /lol-gameflow/v1/session            -> gameData.gameId of the match
//!   GET /lol-end-of-game/v1/eog-stats-block -> who won, when it ends
//!
//! No API key is needed: it is her client talking to her own PC. The gameId
//! is the same number Match-V5 uses, so if the bot has a valid Riot key it
//! can confirm the result through the public API even when the agent misses
//! the end-of-game screen.
//!
//! The agent used to query the Live Client Data API (port 2999) with
//! `Invoke-RestMethod -SkipCertificateCheck`, a parameter that only exists in
//! PowerShell 7. On `powershell.exe` (5.1) it always failed, silently.

use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use crate::local::{self, Diagnostics, Fetch, Level, Snapshot};

const SCOPE: &str = "lol";

/// Default install path, for when it cannot be derived from the process.
const DEFAULT_LOCKFILE: &str = "C:\\Riot Games\\League of Legends\\lockfile";

/// Maps the client phase to the shared vocabulary the bot understands.
pub fn normalize_phase(raw: &str) -> &'static str {
    match raw {
        "None" | "Lobby" | "Matchmaking" | "ReadyCheck" | "CheckedIntoTournament"
        | "TerminatedInError" => "menus",
        "ChampSelect" => "pregame",
        "GameStart" | "InProgress" | "Reconnect" => "in_game",
        "WaitingForStats" | "PreEndOfGame" | "EndOfGame" => "post_game",
        _ => "unknown",
    }
}

fn positive_id(v: &Value) -> Option<String> {
    let n = v.as_i64().or_else(|| v.as_str()?.parse().ok())?;
    (n > 0).then(|| n.to_string())
}

/// `gameData.gameId` from /lol-gameflow/v1/session. It is 0 outside a match.
pub fn parse_game_id(session: &Value) -> Option<String> {
    positive_id(session.get("gameData")?.get("gameId")?)
}

pub fn parse_queue_id(session: &Value) -> Option<i64> {
    session.get("gameData")?.get("queue")?.get("id")?.as_i64()
}

/// (gameId, won) from the end-of-game block, or None when there is no clear
/// winner (remake, incomplete data): in that case let the public API or a
/// mod decide, instead of guessing.
pub fn parse_eog_result(eog: &Value) -> Option<(String, bool)> {
    let game_id = positive_id(eog.get("gameId")?)?;
    let local_player = eog.get("localPlayer")?;

    if let (Some(team_id), Some(teams)) = (
        local_player.get("teamId").and_then(Value::as_i64),
        eog.get("teams").and_then(Value::as_array),
    ) {
        let winning = |t: &Value| t.get("isWinningTeam").and_then(Value::as_bool);
        let mine = teams
            .iter()
            .find(|t| t.get("teamId").and_then(Value::as_i64) == Some(team_id))
            .and_then(winning);
        let someone_won = teams.iter().any(|t| winning(t) == Some(true));
        if let (Some(won), true) = (mine, someone_won) {
            return Some((game_id, won));
        }
    }

    let stats = local_player.get("stats")?;
    let flag = |k: &str| stats.get(k).and_then(Value::as_i64).unwrap_or(0) > 0;
    match (flag("WIN"), flag("LOSE")) {
        (true, false) => Some((game_id, true)),
        (false, true) => Some((game_id, false)),
        _ => None,
    }
}

fn result_json(won: Option<bool>) -> Value {
    match won {
        Some(true) => json!("win"),
        Some(false) => json!("loss"),
        None => Value::Null,
    }
}

/// A match and, once known, its result.
#[derive(Debug, Clone, PartialEq)]
struct Outcome {
    id: String,
    won: Option<bool>,
}

/// What was read from the client in one poll.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Observation {
    pub raw_phase: String,
    pub game_id: Option<String>,
    pub queue_id: Option<i64>,
    pub eog: Option<(String, bool)>,
}

#[derive(Debug, Default)]
pub struct LolTracker {
    phase: Option<&'static str>,
    raw_phase: Option<String>,
    queue_id: Option<i64>,
    /// The match in progress, or the one that just ended while the end screen lasts.
    current: Option<Outcome>,
    /// The last match that ended. If it still has no result we keep looking:
    /// the end-of-game block stays available for a while in the lobby.
    last_match: Option<Outcome>,
}

impl LolTracker {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn reset(&mut self) {
        *self = Self::default();
    }

    pub fn is_idle(&self) -> bool {
        self.phase.is_none() && self.current.is_none() && self.last_match.is_none()
    }

    /// `lockfile_hint`: path derived from the client process, when possible.
    pub fn poll(&mut self, lockfile_hint: Option<PathBuf>, diag: &mut Diagnostics) -> Snapshot {
        if let Some(obs) = self.observe(lockfile_hint, diag) {
            self.advance(obs, diag);
        }
        self.snapshot()
    }

    fn needs_result(&self) -> bool {
        let pending = |o: &Option<Outcome>| o.as_ref().is_some_and(|o| o.won.is_none());
        pending(&self.current) || pending(&self.last_match)
    }

    fn observe(&self, hint: Option<PathBuf>, diag: &mut Diagnostics) -> Option<Observation> {
        let path = hint.unwrap_or_else(|| PathBuf::from(DEFAULT_LOCKFILE));
        let lockfile = match local::read_lockfile(&path) {
            Ok(l) => {
                diag.status(SCOPE, "client", Level::Info, format!("League client local API on port {}", l.port));
                l
            }
            Err(e) => {
                diag.status(SCOPE, "client", Level::Warning, format!("League client lockfile unavailable: {e}"));
                return None;
            }
        };
        let auth = lockfile.auth();
        let get = |p: &str| local::get(&lockfile.url(p), Some(&auth));

        let raw_phase = match get("/lol-gameflow/v1/gameflow-phase") {
            Fetch::Ok(Value::String(p)) => p,
            Fetch::Ok(other) => {
                diag.status(SCOPE, "phase", Level::Warning, format!("unexpected gameflow phase: {other}"));
                return None;
            }
            Fetch::NotFound => {
                diag.status(SCOPE, "phase", Level::Warning, "gameflow phase returned 404");
                return None;
            }
            Fetch::Failed(e) => {
                diag.status(SCOPE, "phase", Level::Warning, format!("could not read gameflow phase: {e}"));
                return None;
            }
        };
        diag.status(SCOPE, "phase", Level::Info, format!("client phase: {raw_phase}"));
        let phase = normalize_phase(&raw_phase);

        let mut obs = Observation { raw_phase, ..Observation::default() };

        if matches!(phase, "in_game" | "post_game") {
            match get("/lol-gameflow/v1/session") {
                Fetch::Ok(session) => {
                    obs.game_id = parse_game_id(&session);
                    obs.queue_id = parse_queue_id(&session);
                    if obs.game_id.is_none() {
                        diag.status(SCOPE, "session", Level::Warning, "gameflow session has no gameId");
                    } else {
                        diag.status(SCOPE, "session", Level::Info, "gameflow session OK");
                    }
                }
                Fetch::NotFound => diag.status(SCOPE, "session", Level::Warning, "gameflow session returned 404"),
                Fetch::Failed(e) => {
                    diag.status(SCOPE, "session", Level::Warning, format!("could not read gameflow session: {e}"))
                }
            }
        }

        if phase == "post_game" || self.needs_result() {
            match get("/lol-end-of-game/v1/eog-stats-block") {
                Fetch::Ok(eog) => match parse_eog_result(&eog) {
                    Some(result) => obs.eog = Some(result),
                    None => diag.status(
                        SCOPE,
                        "eog",
                        Level::Warning,
                        format!(
                            "end-of-game block has no clear winner; fields: {}",
                            local::clip(&describe(&eog), 400)
                        ),
                    ),
                },
                // Normal while the stats are still being computed.
                Fetch::NotFound => {}
                Fetch::Failed(e) => {
                    diag.status(SCOPE, "eog", Level::Warning, format!("could not read end-of-game block: {e}"))
                }
            }
        }

        Some(obs)
    }

    /// Advances the state machine with one reading of the client.
    fn advance(&mut self, obs: Observation, diag: &mut Diagnostics) {
        let phase = normalize_phase(&obs.raw_phase);
        match phase {
            "in_game" => {
                if let Some(id) = obs.game_id.clone() {
                    if self.current.as_ref().map(|c| &c.id) != Some(&id) {
                        if let Some(previous) = self.current.take() {
                            self.last_match = Some(previous);
                        }
                        diag.event(SCOPE, Level::Info, format!("game {id} started"));
                        self.current = Some(Outcome { id, won: None });
                    }
                }
                if obs.queue_id.is_some() {
                    self.queue_id = obs.queue_id;
                }
            }
            "post_game" => {
                // The agent started with the end screen already open.
                if self.current.is_none() {
                    if let Some(id) = obs.game_id.clone() {
                        self.current = Some(Outcome { id, won: None });
                    }
                }
            }
            "menus" | "pregame" => {
                if let Some(finished) = self.current.take() {
                    self.last_match = Some(finished);
                }
                self.queue_id = None;
            }
            _ => {}
        }

        if let Some((id, won)) = obs.eog {
            for slot in [self.current.as_mut(), self.last_match.as_mut()].into_iter().flatten() {
                if slot.id == id && slot.won.is_none() {
                    slot.won = Some(won);
                    diag.event(SCOPE, Level::Info, format!("game {id} ended: {}", if won { "win" } else { "loss" }));
                }
            }
        }

        self.phase = Some(phase);
        self.raw_phase = Some(obs.raw_phase);
    }

    fn snapshot(&self) -> Snapshot {
        let Some(phase) = self.phase else {
            return Snapshot::default();
        };
        let mut meta = json!({ "source": "lcu", "phase": phase });
        if let Some(raw) = &self.raw_phase {
            meta["client_phase"] = json!(raw);
        }
        if let Some(queue) = self.queue_id {
            meta["queue_id"] = json!(queue);
        }

        let live = matches!(phase, "in_game" | "post_game");
        let match_id = if live { self.current.as_ref().map(|c| c.id.clone()) } else { None };
        if let (true, Some(current)) = (live, &self.current) {
            meta["result"] = result_json(current.won);
        }
        if let Some(last) = &self.last_match {
            meta["last_match"] = json!({ "id": last.id, "result": result_json(last.won) });
        }
        Snapshot { match_id, metadata: Some(meta) }
    }
}

fn describe(v: &Value) -> String {
    match v.as_object() {
        Some(obj) => {
            let mut keys: Vec<&str> = obj.keys().map(String::as_str).collect();
            keys.sort_unstable();
            keys.join(",")
        }
        None => "(not an object)".to_string(),
    }
}

/// Install folder -> lockfile. `levels_up` is how many folders to climb from
/// the executable's folder: the client lives at the root of the install and
/// the game in `Game\`.
pub fn lockfile_from_exe(exe: &Path, levels_up: usize) -> Option<PathBuf> {
    let mut dir = exe.parent()?;
    for _ in 0..levels_up {
        dir = dir.parent()?;
    }
    Some(dir.join("lockfile"))
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
//
// Naming: [method]_when_[scenario]_[expected_result]
// They touch neither the network nor the real client: responses are built by hand.

#[cfg(test)]
mod tests {
    use super::*;

    fn obs(phase: &str, game_id: Option<&str>, eog: Option<(&str, bool)>) -> Observation {
        Observation {
            raw_phase: phase.to_string(),
            game_id: game_id.map(str::to_string),
            queue_id: Some(420),
            eog: eog.map(|(id, won)| (id.to_string(), won)),
        }
    }

    fn run(steps: &[Observation]) -> (LolTracker, Vec<String>) {
        let mut t = LolTracker::new();
        let mut d = Diagnostics::new();
        for step in steps {
            t.advance(step.clone(), &mut d);
        }
        (t, d.take())
    }

    // --- normalize_phase ---

    #[test]
    fn normalize_phase_maps_every_client_screen() {
        assert_eq!(normalize_phase("Lobby"), "menus");
        assert_eq!(normalize_phase("Matchmaking"), "menus");
        assert_eq!(normalize_phase("ChampSelect"), "pregame");
        assert_eq!(normalize_phase("InProgress"), "in_game");
        assert_eq!(normalize_phase("Reconnect"), "in_game");
        assert_eq!(normalize_phase("EndOfGame"), "post_game");
        assert_eq!(normalize_phase("SomethingNew"), "unknown");
    }

    // --- parsers ---

    #[test]
    fn parse_game_id_when_in_game_returns_id() {
        let s = json!({ "gameData": { "gameId": 1_570_331_942, "queue": { "id": 420 } } });
        assert_eq!(parse_game_id(&s).as_deref(), Some("1570331942"));
        assert_eq!(parse_queue_id(&s), Some(420));
    }

    #[test]
    fn parse_game_id_when_zero_returns_none() {
        assert_eq!(parse_game_id(&json!({ "gameData": { "gameId": 0 } })), None);
    }

    #[test]
    fn parse_eog_result_when_my_team_won_returns_win() {
        let eog = json!({
            "gameId": 99,
            "localPlayer": { "teamId": 100 },
            "teams": [
                { "teamId": 100, "isWinningTeam": true },
                { "teamId": 200, "isWinningTeam": false }
            ]
        });
        assert_eq!(parse_eog_result(&eog), Some(("99".into(), true)));
    }

    #[test]
    fn parse_eog_result_when_other_team_won_returns_loss() {
        let eog = json!({
            "gameId": 99,
            "localPlayer": { "teamId": 100 },
            "teams": [
                { "teamId": 100, "isWinningTeam": false },
                { "teamId": 200, "isWinningTeam": true }
            ]
        });
        assert_eq!(parse_eog_result(&eog), Some(("99".into(), false)));
    }

    #[test]
    fn parse_eog_result_when_nobody_won_returns_none() {
        // Remake: no team wins, better not to invent a result.
        let eog = json!({
            "gameId": 99,
            "localPlayer": { "teamId": 100, "stats": { "WIN": 0, "LOSE": 0 } },
            "teams": [
                { "teamId": 100, "isWinningTeam": false },
                { "teamId": 200, "isWinningTeam": false }
            ]
        });
        assert_eq!(parse_eog_result(&eog), None);
    }

    #[test]
    fn parse_eog_result_when_only_stats_uses_them() {
        let eog = json!({ "gameId": 7, "localPlayer": { "stats": { "WIN": 1, "LOSE": 0 } } });
        assert_eq!(parse_eog_result(&eog), Some(("7".into(), true)));
    }

    #[test]
    fn lockfile_from_exe_walks_up_from_the_game_folder() {
        let client = Path::new("D:\\Games\\League of Legends\\LeagueClientUx.exe");
        let game = Path::new("D:\\Games\\League of Legends\\Game\\League of Legends.exe");
        let expected = PathBuf::from("D:\\Games\\League of Legends\\lockfile");
        assert_eq!(lockfile_from_exe(client, 0), Some(expected.clone()));
        assert_eq!(lockfile_from_exe(game, 1), Some(expected));
    }

    // --- advance / snapshot ---

    #[test]
    fn advance_when_in_lobby_reports_no_match() {
        let (t, _) = run(&[obs("Lobby", None, None)]);
        let s = t.snapshot();
        assert_eq!(s.match_id, None);
        assert_eq!(s.metadata.unwrap()["phase"], "menus");
    }

    #[test]
    fn advance_when_champ_select_does_not_create_match() {
        // A dodge in champion select must not leave a prediction hanging.
        let (t, _) = run(&[obs("ChampSelect", Some("55"), None)]);
        assert_eq!(t.snapshot().match_id, None);
    }

    #[test]
    fn advance_when_game_starts_reports_its_game_id() {
        let (t, logs) = run(&[obs("Lobby", None, None), obs("InProgress", Some("55"), None)]);
        let s = t.snapshot();
        assert_eq!(s.match_id.as_deref(), Some("55"));
        let meta = s.metadata.unwrap();
        assert_eq!(meta["phase"], "in_game");
        assert_eq!(meta["queue_id"], 420);
        assert!(meta["result"].is_null());
        assert!(logs.iter().any(|l| l.contains("game 55 started")), "{logs:?}");
    }

    #[test]
    fn advance_when_end_screen_shows_result_reports_it_with_the_match() {
        let (t, logs) = run(&[
            obs("InProgress", Some("55"), None),
            obs("EndOfGame", Some("55"), Some(("55", true))),
        ]);
        let s = t.snapshot();
        assert_eq!(s.match_id.as_deref(), Some("55"));
        assert_eq!(s.metadata.unwrap()["result"], "win");
        assert!(logs.iter().any(|l| l.contains("game 55 ended: win")), "{logs:?}");
    }

    #[test]
    fn advance_when_back_in_lobby_moves_match_to_last_match() {
        let (t, _) = run(&[
            obs("InProgress", Some("55"), None),
            obs("EndOfGame", Some("55"), Some(("55", false))),
            obs("Lobby", None, None),
        ]);
        let s = t.snapshot();
        assert_eq!(s.match_id, None);
        assert_eq!(s.metadata.unwrap()["last_match"], json!({ "id": "55", "result": "loss" }));
    }

    #[test]
    fn advance_when_end_screen_was_skipped_finds_result_from_lobby() {
        // "Play again" skips the end screen before the next poll: the result
        // still arrives from the lobby.
        let (mut t, _) = run(&[obs("InProgress", Some("55"), None), obs("Lobby", None, None)]);
        assert!(t.needs_result());
        let mut d = Diagnostics::new();
        t.advance(obs("Lobby", None, Some(("55", true))), &mut d);
        assert_eq!(t.snapshot().metadata.unwrap()["last_match"]["result"], "win");
        assert!(!t.needs_result());
    }

    #[test]
    fn advance_when_result_belongs_to_another_game_ignores_it() {
        let (t, _) = run(&[
            obs("InProgress", Some("55"), None),
            obs("EndOfGame", Some("55"), Some(("44", true))),
        ]);
        assert!(t.snapshot().metadata.unwrap()["result"].is_null());
    }

    #[test]
    fn advance_when_new_game_starts_keeps_previous_as_last_match() {
        let (t, _) = run(&[
            obs("InProgress", Some("55"), None),
            obs("InProgress", Some("66"), None),
        ]);
        let s = t.snapshot();
        assert_eq!(s.match_id.as_deref(), Some("66"));
        assert_eq!(s.metadata.unwrap()["last_match"]["id"], "55");
    }

    #[test]
    fn advance_when_result_already_known_does_not_log_it_twice() {
        let (_, logs) = run(&[
            obs("EndOfGame", Some("55"), Some(("55", true))),
            obs("EndOfGame", Some("55"), Some(("55", true))),
        ]);
        assert_eq!(logs.iter().filter(|l| l.contains("ended")).count(), 1, "{logs:?}");
    }

    #[test]
    fn snapshot_when_never_read_returns_empty() {
        assert_eq!(LolTracker::new().snapshot(), Snapshot::default());
    }
}
