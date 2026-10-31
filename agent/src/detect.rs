//! Game detection from running processes.
//!
//! Works with an allowlist: only the executables declared in agent.toml are
//! compared, and only those can be reported. Every other process is looked
//! at and discarded on the spot; it never leaves the machine.
//!
//! For Valorant and League the match state is also read from each client's
//! local API (see `valorant.rs` and `lol.rs`).

use std::collections::HashMap;
use std::path::PathBuf;

use serde_json::Value;
use sysinfo::{ProcessRefreshKind, ProcessesToUpdate, RefreshKind, System, UpdateKind};

use crate::config::GameRule;
use crate::local::{Diagnostics, Snapshot};
use crate::lol::{self, LolTracker};
use crate::valorant::ValorantTracker;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Detected {
    /// Game name exactly as Twitch expects it.
    pub game: String,
    /// Executable that triggered the match. Only for the log.
    pub exe: String,
    /// Active match: the LoL gameId, or the agent's own key in Valorant.
    pub match_id: Option<String>,
    /// Match state according to the local client (phase, result, score).
    pub metadata: Option<Value>,
}

struct Rule {
    game: String,
    priority: i32,
    /// Lowercase executable -> original executable, to log it nicely.
    processes: HashMap<String, String>,
}

/// League client processes, with how many folders to climb from their
/// executable to reach the root of the install (where the lockfile is).
const LEAGUE_PROCESSES: [(&str, usize); 3] = [
    ("leagueclientux.exe", 0),
    ("leagueclient.exe", 0),
    ("league of legends.exe", 1),
];

pub struct Detector {
    rules: Vec<Rule>,
    system: System,
    valorant: ValorantTracker,
    lol: LolTracker,
    diag: Diagnostics,
}

impl Detector {
    pub fn new(config: &[GameRule]) -> Self {
        let rules = config
            .iter()
            .map(|g| Rule {
                game: g.name.clone(),
                priority: g.priority,
                processes: g
                    .processes
                    .iter()
                    .map(|p| (p.to_lowercase(), p.clone()))
                    .collect(),
            })
            .collect();

        // Only the process list: no CPU, memory or command line. It is
        // cheaper, and we read nothing we do not need.
        let system = System::new_with_specifics(
            RefreshKind::new().with_processes(ProcessRefreshKind::new()),
        );

        Self {
            rules,
            system,
            valorant: ValorantTracker::new(),
            lol: LolTracker::new(),
            diag: Diagnostics::new(),
        }
    }

    /// Cuantos juegos distintos hay configurados.
    pub fn rule_count(&self) -> usize {
        self.rules.len()
    }

    fn refresh_names(&mut self) {
        // `ProcessRefreshKind::new()` fetches names only. A bare
        // `refresh_processes` would add CPU, memory, disk and path for every
        // process, every 5 seconds, on the PC where she is playing and
        // streaming. `true` drops dead processes, otherwise the list grows.
        self.system
            .refresh_processes_specifics(ProcessesToUpdate::All, true, ProcessRefreshKind::new());
    }

    /// The highest-priority game that is running, or None.
    pub fn detect(&mut self) -> Option<Detected> {
        self.refresh_names();

        let running: Vec<String> = self
            .system
            .processes()
            .values()
            .map(|p| p.name().to_string_lossy().to_lowercase())
            .collect();

        let Some(mut detected) = self.select_winner(&running) else {
            self.leave_games(None);
            return None;
        };

        let game = detected.game.to_lowercase();
        let snapshot = if game.contains("valorant") {
            self.leave_games(Some("valorant"));
            self.valorant.poll(&mut self.diag)
        } else if game.contains("league of legends") {
            self.leave_games(Some("lol"));
            let hint = self.league_lockfile();
            self.lol.poll(hint, &mut self.diag)
        } else {
            self.leave_games(None);
            Snapshot::default()
        };

        detected.match_id = snapshot.match_id;
        detected.metadata = snapshot.metadata;
        Some(detected)
    }

    /// When a game stops being played its state is forgotten: if it comes
    /// back later it starts fresh instead of dragging an old match along.
    fn leave_games(&mut self, keep: Option<&str>) {
        if keep != Some("valorant") && !self.valorant.is_idle() {
            self.valorant.reset();
            self.diag.forget("valorant");
        }
        if keep != Some("lol") && !self.lol.is_idle() {
            self.lol.reset();
            self.diag.forget("lol");
        }
    }

    /// League client lockfile, derived from the path of its executable.
    ///
    /// The install folder differs between PCs. Only the client process's path
    /// is requested, not every process's.
    fn league_lockfile(&mut self) -> Option<PathBuf> {
        for (name, levels_up) in LEAGUE_PROCESSES {
            let pid = self
                .system
                .processes()
                .iter()
                .find(|(_, p)| p.name().to_string_lossy().eq_ignore_ascii_case(name))
                .map(|(pid, _)| *pid);
            let Some(pid) = pid else { continue };

            self.system.refresh_processes_specifics(
                ProcessesToUpdate::Some(&[pid]),
                false,
                ProcessRefreshKind::new().with_exe(UpdateKind::OnlyIfNotSet),
            );
            let exe = self.system.process(pid).and_then(|p| p.exe());
            if let Some(path) = exe.and_then(|exe| lol::lockfile_from_exe(exe, levels_up)) {
                return Some(path);
            }
        }
        None
    }

    pub fn take_diagnostics(&mut self) -> Vec<String> {
        self.diag.take()
    }

    /// Pure selection logic: given a slice of process names (already
    /// lowercase) returns the highest-priority Detected, or None.
    ///
    /// Kept apart from `detect` so it can be tested without the real OS.
    fn select_winner(&self, running: &[String]) -> Option<Detected> {
        let mut best: Option<(i32, Detected)> = None;

        for rule in &self.rules {
            for name in running {
                if let Some(original) = rule.processes.get(name) {
                    let candidate = Detected {
                        game: rule.game.clone(),
                        exe: original.clone(),
                        match_id: None,
                        metadata: None,
                    };
                    if best.as_ref().map_or(true, |(p, _)| rule.priority > *p) {
                        best = Some((rule.priority, candidate));
                    }
                    break;
                }
            }
        }

        best.map(|(_, d)| d)
    }

    /// Running processes, sorted and deduplicated.
    ///
    /// Only used by `--scan`, which prints them on screen to find out the
    /// executable of a new game. Never sent over the network.
    pub fn running_processes(&mut self) -> Vec<String> {
        self.refresh_names();
        let mut names: Vec<String> = self
            .system
            .processes()
            .values()
            .map(|p| p.name().to_string_lossy().to_string())
            .collect();
        names.sort_by_key(|n| n.to_lowercase());
        names.dedup_by_key(|n| n.to_lowercase());
        names
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
//
// Naming: [method]_when_[scenario]_[expected_result]
// All of them use synthetic process lists: they do not read the real OS.

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::GameRule;

    /// Builds a Detector from plain-text rules.
    fn detector(games: &[(&str, i32, &[&str])]) -> Detector {
        let rules: Vec<GameRule> = games
            .iter()
            .map(|(name, prio, procs)| GameRule {
                name: name.to_string(),
                priority: *prio,
                processes: procs.iter().map(|p| p.to_string()).collect(),
            })
            .collect();
        Detector::new(&rules)
    }

    /// Shorthand: list of process names, already lowercase.
    fn procs(names: &[&str]) -> Vec<String> {
        names.iter().map(|n| n.to_lowercase()).collect()
    }

    // --- rule_count ---

    #[test]
    fn rule_count_when_empty_config_returns_zero() {
        let d = detector(&[]);
        assert_eq!(d.rule_count(), 0);
    }

    #[test]
    fn rule_count_when_two_games_configured_returns_two() {
        let d = detector(&[
            ("League of Legends", 10, &["League of Legends.exe"]),
            ("VALORANT", 10, &["VALORANT-Win64-Shipping.exe"]),
        ]);
        assert_eq!(d.rule_count(), 2);
    }

    // --- select_winner ---

    #[test]
    fn select_winner_when_no_processes_running_returns_none() {
        let d = detector(&[("League of Legends", 10, &["League of Legends.exe"])]);
        assert!(d.select_winner(&[]).is_none());
    }

    #[test]
    fn select_winner_when_no_matching_process_returns_none() {
        let d = detector(&[("League of Legends", 10, &["League of Legends.exe"])]);
        let running = procs(&["chrome.exe", "explorer.exe", "notepad.exe"]);
        assert!(d.select_winner(&running).is_none());
    }

    #[test]
    fn select_winner_when_single_match_returns_that_game() {
        let d = detector(&[("League of Legends", 10, &["League of Legends.exe"])]);
        let running = procs(&["chrome.exe", "league of legends.exe"]);
        let result = d.select_winner(&running).expect("should detect LoL");
        assert_eq!(result.game, "League of Legends");
    }

    #[test]
    fn select_winner_when_process_name_is_uppercase_still_matches() {
        // Names arrive lowercase from detect(), but we make sure the logic
        // is robust anyway.
        let d = detector(&[("VALORANT", 10, &["VALORANT-Win64-Shipping.exe"])]);
        let running = procs(&["valorant-win64-shipping.exe"]);
        let result = d.select_winner(&running).expect("should detect VALORANT");
        assert_eq!(result.game, "VALORANT");
    }

    #[test]
    fn select_winner_when_higher_priority_game_running_returns_it() {
        let d = detector(&[
            ("League of Legends", 5, &["League of Legends.exe"]),
            ("VALORANT", 10, &["VALORANT-Win64-Shipping.exe"]),
        ]);
        let running = procs(&["league of legends.exe", "valorant-win64-shipping.exe"]);
        let result = d.select_winner(&running).expect("should detect something");
        assert_eq!(result.game, "VALORANT"); // priority 10 > 5
    }

    #[test]
    fn select_winner_when_only_lower_priority_game_running_returns_it() {
        let d = detector(&[
            ("Launcher", 1, &["launcher.exe"]),
            ("VALORANT", 10, &["VALORANT-Win64-Shipping.exe"]),
        ]);
        // Only the launcher is running, not VALORANT
        let running = procs(&["launcher.exe"]);
        let result = d.select_winner(&running).expect("should detect the launcher");
        assert_eq!(result.game, "Launcher");
    }

    #[test]
    fn select_winner_when_priority_tied_returns_first_rule_in_config() {
        // Priority tie: the first rule declared in agent.toml wins
        let d = detector(&[
            ("Game A", 10, &["game_a.exe"]),
            ("Game B", 10, &["game_b.exe"]),
        ]);
        let running = procs(&["game_a.exe", "game_b.exe"]);
        let result = d.select_winner(&running).expect("should detect something");
        assert_eq!(result.game, "Game A"); // the first rule wins the tie
    }

    #[test]
    fn select_winner_when_game_has_multiple_executables_matches_any() {
        let d = detector(&[("League of Legends", 10, &[
            "League of Legends.exe",
            "LeagueClient.exe",
            "LeagueClientUxRender.exe",
        ])]);
        // Only the client, not the game itself
        let running = procs(&["leagueclient.exe"]);
        let result = d.select_winner(&running).expect("should detect LoL");
        assert_eq!(result.game, "League of Legends");
    }

    #[test]
    fn select_winner_when_match_found_exe_field_is_original_case() {
        // Detected's `exe` field must carry the name as written in the
        // config (original casing), not lowercase.
        let d = detector(&[("League of Legends", 10, &["League of Legends.exe"])]);
        let running = procs(&["league of legends.exe"]);
        let result = d.select_winner(&running).expect("should detect LoL");
        assert_eq!(result.exe, "League of Legends.exe");
    }
}
