//! Valorant match state, read from the client's local presence.
//!
//! Everything comes from the Riot Client's local API (127.0.0.1 + lockfile):
//!
//!   GET /chat/v1/session    -> PUUID of the signed-in account
//!   GET /chat/v4/presences  -> presences; our own has a base64 `private`
//!                              field with the state of the game session
//!
//! The agent used to ask this same API for /core-game and /pregame, but those
//! endpoints live on Riot's remote servers (glz-*.a.pvp.net) and need a
//! different authentication. On localhost they always return 404, and the
//! agent read that as "no match".
//!
//! The presence carries no match ID, so the agent creates its own key when
//! she leaves the menus and releases it when she goes back. That is enough to
//! keep the bot from creating two predictions for the same match.

use std::path::PathBuf;

use serde_json::{json, Value};

use crate::local::{self, Diagnostics, Fetch, Level, Lockfile, Snapshot};

const SCOPE: &str = "valorant";

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Phase {
    Menus,
    Pregame,
    InGame,
    Other(String),
}

impl Phase {
    fn from_raw(raw: &str) -> Phase {
        match raw.to_ascii_uppercase().as_str() {
            "MENUS" => Phase::Menus,
            "PREGAME" => Phase::Pregame,
            "INGAME" => Phase::InGame,
            _ => Phase::Other(raw.to_string()),
        }
    }

    /// Vocabulary shared with the LoL tracker: this is what the bot reads.
    pub fn as_str(&self) -> &str {
        match self {
            Phase::Menus => "menus",
            Phase::Pregame => "pregame",
            Phase::InGame => "in_game",
            Phase::Other(raw) => raw,
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct PresenceState {
    pub phase: Option<Phase>,
    pub queue: Option<String>,
    pub ally: Option<i64>,
    pub enemy: Option<i64>,
}

/// First non-null value among several possible paths.
///
/// Riot restructured the private presence: the old format is flat
/// (`sessionLoopState`) and the new one groups fields into objects
/// (`matchPresenceData`, `partyPresenceData`). Both are tried so the agent
/// does not depend on the client version.
fn first<'a>(v: &'a Value, paths: &[&[&str]]) -> Option<&'a Value> {
    paths.iter().find_map(|path| {
        let mut cur = v;
        for key in *path {
            cur = cur.get(*key)?;
        }
        (!cur.is_null()).then_some(cur)
    })
}

pub fn parse_private(private: &Value) -> PresenceState {
    let phase = first(
        private,
        &[
            &["matchPresenceData", "sessionLoopState"],
            &["sessionLoopState"],
            &["partyPresenceData", "partyOwnerSessionLoopState"],
            &["partyOwnerSessionLoopState"],
        ],
    )
    .and_then(Value::as_str)
    .map(Phase::from_raw);

    let queue = first(
        private,
        &[&["matchPresenceData", "queueId"], &["queueId"], &["partyPresenceData", "queueId"]],
    )
    .and_then(Value::as_str)
    .filter(|q| !q.is_empty())
    .map(str::to_string);

    let score = |field: &str| {
        first(private, &[&["partyPresenceData", field], &["matchPresenceData", field], &[field]])
            .and_then(Value::as_i64)
    };

    PresenceState {
        phase,
        queue,
        ally: score("partyOwnerMatchScoreAllyTeam"),
        enemy: score("partyOwnerMatchScoreEnemyTeam"),
    }
}

/// The fields of the decoded presence, for the log when the state is not
/// found: that way the first real session tells us what to adjust.
pub fn describe_keys(v: &Value) -> String {
    let Some(obj) = v.as_object() else {
        return "(not an object)".to_string();
    };
    let mut parts: Vec<String> = obj
        .iter()
        .map(|(k, val)| match val.as_object() {
            Some(inner) => {
                let keys: Vec<&str> = inner.keys().map(String::as_str).collect();
                format!("{k}{{{}}}", keys.join(","))
            }
            None => k.clone(),
        })
        .collect();
    parts.sort();
    local::clip(&parts.join(", "), 600)
}

pub fn decode_private(b64: &str) -> Option<Value> {
    serde_json::from_slice(&local::base64_decode(b64)?).ok()
}

/// Our own Valorant presence within the /chat/v4/presences response.
pub fn own_presence<'a>(presences: &'a Value, puuid: &str) -> Option<&'a Value> {
    presences.get("presences")?.as_array()?.iter().find(|p| {
        p.get("puuid").and_then(Value::as_str) == Some(puuid)
            && p.get("product").and_then(Value::as_str) == Some("valorant")
    })
}

fn lockfile_path() -> PathBuf {
    let local = std::env::var("LOCALAPPDATA").unwrap_or_else(|_| {
        let profile = std::env::var("USERPROFILE").unwrap_or_else(|_| "C:\\Users\\Default".into());
        format!("{profile}\\AppData\\Local")
    });
    PathBuf::from(local)
        .join("Riot Games")
        .join("Riot Client")
        .join("Config")
        .join("lockfile")
}

#[derive(Debug, Clone, PartialEq)]
struct Finished {
    id: String,
    ally: Option<i64>,
    enemy: Option<i64>,
}

fn score_json(ally: Option<i64>, enemy: Option<i64>) -> Value {
    match (ally, enemy) {
        (Some(a), Some(e)) => json!({ "ally": a, "enemy": e }),
        _ => Value::Null,
    }
}

#[derive(Debug, Default)]
pub struct ValorantTracker {
    session: Option<Lockfile>,
    puuid: Option<String>,
    phase: Option<Phase>,
    queue: Option<String>,
    match_id: Option<String>,
    ally: Option<i64>,
    enemy: Option<i64>,
    last_match: Option<Finished>,
}

impl ValorantTracker {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn reset(&mut self) {
        *self = Self::default();
    }

    pub fn is_idle(&self) -> bool {
        self.phase.is_none() && self.last_match.is_none() && self.session.is_none()
    }

    pub fn poll(&mut self, diag: &mut Diagnostics) -> Snapshot {
        // If the presence could not be read this time, the previous state
        // stays as it was: a transient error must not end the match.
        if let Some(state) = self.read_presence(diag) {
            self.advance(state, local::unix_millis(), diag);
        }
        self.snapshot()
    }

    fn read_presence(&mut self, diag: &mut Diagnostics) -> Option<PresenceState> {
        let lockfile = match local::read_lockfile(&lockfile_path()) {
            Ok(l) => l,
            Err(e) => {
                diag.status(SCOPE, "client", Level::Warning, format!("Riot client lockfile unavailable: {e}"));
                return None;
            }
        };
        if self.session.as_ref() != Some(&lockfile) {
            // New or restarted client: the account may have changed.
            self.puuid = None;
            self.session = Some(lockfile.clone());
        }
        let auth = lockfile.auth();

        let puuid = match &self.puuid {
            Some(p) => p.clone(),
            None => {
                let p = self.fetch_puuid(&lockfile, &auth, diag)?;
                self.puuid = Some(p.clone());
                p
            }
        };

        let presences = match local::get(&lockfile.url("/chat/v4/presences"), Some(&auth)) {
            Fetch::Ok(v) => v,
            Fetch::NotFound => {
                diag.status(SCOPE, "presence", Level::Warning, "/chat/v4/presences returned 404");
                return None;
            }
            Fetch::Failed(e) => {
                diag.status(SCOPE, "presence", Level::Warning, format!("could not read presences: {e}"));
                return None;
            }
        };
        let Some(own) = own_presence(&presences, &puuid) else {
            diag.status(SCOPE, "presence", Level::Info, "no Valorant presence yet (game still loading?)");
            return None;
        };
        let Some(private) = own.get("private").and_then(Value::as_str).and_then(decode_private) else {
            diag.status(SCOPE, "presence", Level::Warning, "own presence has no decodable 'private' field");
            return None;
        };

        let state = parse_private(&private);
        if state.phase.is_none() {
            diag.status(
                SCOPE,
                "presence",
                Level::Warning,
                format!("game state not found in presence; fields: {}", describe_keys(&private)),
            );
            return None;
        }
        diag.status(SCOPE, "presence", Level::Info, "reading game state from local presence");
        Some(state)
    }

    fn fetch_puuid(&self, lockfile: &Lockfile, auth: &str, diag: &mut Diagnostics) -> Option<String> {
        let body = match local::get(&lockfile.url("/chat/v1/session"), Some(auth)) {
            Fetch::Ok(body) => body,
            Fetch::NotFound => {
                diag.status(SCOPE, "client", Level::Info, "Riot client is open but not signed in yet");
                return None;
            }
            Fetch::Failed(e) => {
                diag.status(SCOPE, "client", Level::Warning, format!("could not read chat session: {e}"));
                return None;
            }
        };
        // The field is called `puuid`; some older versions used `subject`.
        let puuid = body
            .get("puuid")
            .or_else(|| body.get("subject"))
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty());
        match puuid {
            Some(p) => {
                diag.status(
                    SCOPE,
                    "client",
                    Level::Info,
                    format!("signed in as {} (local API on port {})", local::mask(p), lockfile.port),
                );
                Some(p.to_string())
            }
            None => {
                diag.status(
                    SCOPE,
                    "client",
                    Level::Warning,
                    format!("chat session has no puuid: {}", local::clip(&body.to_string(), 300)),
                );
                None
            }
        }
    }

    /// Advances the state machine with one reading of the presence.
    fn advance(&mut self, state: PresenceState, now_ms: u128, diag: &mut Diagnostics) {
        let phase = state.phase.unwrap_or_else(|| Phase::Other("unknown".into()));
        match &phase {
            Phase::Pregame | Phase::InGame => {
                if self.match_id.is_none() {
                    let id = format!("val-{now_ms}");
                    diag.event(SCOPE, Level::Info, format!("match started ({}), key {id}", phase.as_str()));
                    self.match_id = Some(id);
                    self.ally = None;
                    self.enemy = None;
                }
                if state.ally.is_some() && state.enemy.is_some() {
                    self.ally = state.ally;
                    self.enemy = state.enemy;
                }
            }
            Phase::Menus => {
                if let Some(id) = self.match_id.take() {
                    let shown = match (self.ally, self.enemy) {
                        (Some(a), Some(e)) => format!("final score {a}-{e}"),
                        _ => "final score unknown".to_string(),
                    };
                    diag.event(SCOPE, Level::Info, format!("match {id} ended, {shown}"));
                    self.last_match = Some(Finished { id, ally: self.ally, enemy: self.enemy });
                    self.ally = None;
                    self.enemy = None;
                }
            }
            // A state we do not know neither ends nor starts anything: better
            // to miss a prediction than to invent the end of a match.
            Phase::Other(_) => {}
        }
        self.phase = Some(phase);
        self.queue = state.queue;
    }

    fn snapshot(&self) -> Snapshot {
        let Some(phase) = &self.phase else {
            return Snapshot::default();
        };
        let mut meta = json!({ "source": "presence", "phase": phase.as_str() });
        if let Some(queue) = &self.queue {
            meta["queue"] = json!(queue);
        }
        if self.match_id.is_some() {
            let score = score_json(self.ally, self.enemy);
            if !score.is_null() {
                meta["score"] = score;
            }
        }
        if let Some(f) = &self.last_match {
            meta["last_match"] = json!({ "id": f.id, "score": score_json(f.ally, f.enemy) });
        }
        Snapshot {
            match_id: self.match_id.clone(),
            metadata: Some(meta),
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
//
// Naming: [method]_when_[scenario]_[expected_result]
// They touch neither the network nor the real client: presences are built by hand.

#[cfg(test)]
mod tests {
    use super::*;

    fn state(phase: &str, ally: Option<i64>, enemy: Option<i64>) -> PresenceState {
        PresenceState {
            phase: Some(Phase::from_raw(phase)),
            queue: Some("competitive".into()),
            ally,
            enemy,
        }
    }

    // --- parse_private ---

    #[test]
    fn parse_private_when_flat_format_reads_state_queue_and_score() {
        let p = json!({
            "sessionLoopState": "INGAME",
            "queueId": "competitive",
            "partyOwnerMatchScoreAllyTeam": 7,
            "partyOwnerMatchScoreEnemyTeam": 4
        });
        let s = parse_private(&p);
        assert_eq!(s.phase, Some(Phase::InGame));
        assert_eq!(s.queue.as_deref(), Some("competitive"));
        assert_eq!((s.ally, s.enemy), (Some(7), Some(4)));
    }

    #[test]
    fn parse_private_when_nested_format_reads_state_queue_and_score() {
        let p = json!({
            "matchPresenceData": { "sessionLoopState": "PREGAME", "queueId": "unrated" },
            "partyPresenceData": {
                "partyOwnerMatchScoreAllyTeam": 0,
                "partyOwnerMatchScoreEnemyTeam": 0
            }
        });
        let s = parse_private(&p);
        assert_eq!(s.phase, Some(Phase::Pregame));
        assert_eq!(s.queue.as_deref(), Some("unrated"));
        assert_eq!((s.ally, s.enemy), (Some(0), Some(0)));
    }

    #[test]
    fn parse_private_when_state_missing_returns_no_phase() {
        let s = parse_private(&json!({ "isValid": true, "partyId": "x" }));
        assert_eq!(s.phase, None);
    }

    #[test]
    fn parse_private_when_queue_is_empty_string_returns_none() {
        let s = parse_private(&json!({ "sessionLoopState": "MENUS", "queueId": "" }));
        assert_eq!(s.queue, None);
    }

    #[test]
    fn phase_from_raw_when_unknown_keeps_the_raw_value() {
        assert_eq!(Phase::from_raw("REPLAY"), Phase::Other("REPLAY".into()));
        assert_eq!(Phase::from_raw("REPLAY").as_str(), "REPLAY");
    }

    // --- decode_private / own_presence ---

    #[test]
    fn decode_private_when_valid_base64_json_returns_value() {
        let encoded = local::base64_encode(br#"{"sessionLoopState":"INGAME"}"#);
        assert_eq!(decode_private(&encoded), Some(json!({ "sessionLoopState": "INGAME" })));
    }

    #[test]
    fn decode_private_when_not_json_returns_none() {
        assert_eq!(decode_private(&local::base64_encode(b"not json")), None);
    }

    #[test]
    fn own_presence_when_present_returns_own_valorant_entry() {
        let presences = json!({ "presences": [
            { "puuid": "friend", "product": "valorant", "private": "a" },
            { "puuid": "me", "product": "league_of_legends", "private": "b" },
            { "puuid": "me", "product": "valorant", "private": "c" }
        ]});
        let own = own_presence(&presences, "me").expect("should find our own entry");
        assert_eq!(own["private"], "c");
    }

    #[test]
    fn own_presence_when_only_friends_returns_none() {
        let presences = json!({ "presences": [{ "puuid": "friend", "product": "valorant" }] });
        assert!(own_presence(&presences, "me").is_none());
    }

    // --- advance / snapshot ---

    #[test]
    fn advance_when_menus_has_no_match() {
        let mut t = ValorantTracker::new();
        let mut d = Diagnostics::new();
        t.advance(state("MENUS", None, None), 1, &mut d);
        let s = t.snapshot();
        assert_eq!(s.match_id, None);
        assert_eq!(s.metadata.unwrap()["phase"], "menus");
    }

    #[test]
    fn advance_when_entering_pregame_creates_match_key() {
        let mut t = ValorantTracker::new();
        let mut d = Diagnostics::new();
        t.advance(state("MENUS", None, None), 1, &mut d);
        t.advance(state("PREGAME", None, None), 1000, &mut d);
        let s = t.snapshot();
        assert_eq!(s.match_id.as_deref(), Some("val-1000"));
        assert_eq!(s.metadata.unwrap()["phase"], "pregame");
    }

    #[test]
    fn advance_when_pregame_becomes_ingame_keeps_the_same_key() {
        let mut t = ValorantTracker::new();
        let mut d = Diagnostics::new();
        t.advance(state("PREGAME", None, None), 1000, &mut d);
        t.advance(state("INGAME", Some(0), Some(0)), 2000, &mut d);
        t.advance(state("INGAME", Some(3), Some(1)), 3000, &mut d);
        let s = t.snapshot();
        assert_eq!(s.match_id.as_deref(), Some("val-1000"));
        assert_eq!(s.metadata.unwrap()["score"], json!({ "ally": 3, "enemy": 1 }));
    }

    #[test]
    fn advance_when_back_to_menus_ends_match_with_final_score() {
        let mut t = ValorantTracker::new();
        let mut d = Diagnostics::new();
        t.advance(state("INGAME", Some(12), Some(10)), 1000, &mut d);
        t.advance(state("INGAME", Some(13), Some(10)), 2000, &mut d);
        t.advance(state("MENUS", None, None), 3000, &mut d);
        let s = t.snapshot();
        assert_eq!(s.match_id, None);
        let meta = s.metadata.unwrap();
        assert_eq!(meta["last_match"]["id"], "val-1000");
        assert_eq!(meta["last_match"]["score"], json!({ "ally": 13, "enemy": 10 }));
        assert!(meta.get("score").is_none(), "outside a match there is no live score");
    }

    #[test]
    fn advance_when_next_match_starts_mints_a_new_key() {
        let mut t = ValorantTracker::new();
        let mut d = Diagnostics::new();
        t.advance(state("INGAME", None, None), 1000, &mut d);
        t.advance(state("MENUS", None, None), 2000, &mut d);
        t.advance(state("PREGAME", None, None), 3000, &mut d);
        assert_eq!(t.snapshot().match_id.as_deref(), Some("val-3000"));
    }

    #[test]
    fn advance_when_state_is_unknown_does_not_end_the_match() {
        let mut t = ValorantTracker::new();
        let mut d = Diagnostics::new();
        t.advance(state("INGAME", None, None), 1000, &mut d);
        t.advance(state("REPLAY", None, None), 2000, &mut d);
        assert_eq!(t.snapshot().match_id.as_deref(), Some("val-1000"));
    }

    #[test]
    fn advance_when_score_missing_keeps_last_known_score() {
        let mut t = ValorantTracker::new();
        let mut d = Diagnostics::new();
        t.advance(state("INGAME", Some(5), Some(5)), 1000, &mut d);
        t.advance(state("INGAME", None, None), 2000, &mut d);
        assert_eq!(t.snapshot().metadata.unwrap()["score"], json!({ "ally": 5, "enemy": 5 }));
    }

    #[test]
    fn advance_logs_match_start_and_end_as_events() {
        let mut t = ValorantTracker::new();
        let mut d = Diagnostics::new();
        t.advance(state("PREGAME", None, None), 1000, &mut d);
        t.advance(state("MENUS", None, None), 2000, &mut d);
        let logs = d.take();
        assert!(logs.iter().any(|l| l.contains("match started (pregame), key val-1000")), "{logs:?}");
        assert!(logs.iter().any(|l| l.contains("match val-1000 ended, final score unknown")), "{logs:?}");
    }

    #[test]
    fn snapshot_when_never_read_returns_empty() {
        assert_eq!(ValorantTracker::new().snapshot(), Snapshot::default());
    }

    #[test]
    fn describe_keys_lists_nested_objects() {
        let desc = describe_keys(&json!({ "b": 1, "a": { "y": 1, "x": 2 } }));
        assert!(desc.starts_with("a{"), "{desc}");
        assert!(desc.contains('b'), "{desc}");
    }
}
