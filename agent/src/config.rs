//! Agent configuration, read from agent.toml next to the executable.

use serde::Deserialize;
use std::path::{Path, PathBuf};

/// Placeholder shipped in the example file. If it is still there, the config was not edited.
const TOKEN_PLACEHOLDER: &str = "CHANGEME";

#[derive(Debug, Deserialize)]
pub struct Config {
    /// URL of the bot endpoint, for example http://100.72.14.3:8787/game
    pub endpoint: String,

    /// Secret shared with the bot. Sent in the Authorization header.
    pub token: String,

    /// How often processes are checked.
    #[serde(default = "default_poll")]
    pub poll_seconds: u64,

    /// How often the state is re-sent even if it did not change, so the bot
    /// knows the agent is alive and does not keep a stale value.
    #[serde(default = "default_heartbeat")]
    pub heartbeat_seconds: u64,

    /// Log file. Relative to the executable unless it is an absolute path.
    #[serde(default = "default_log")]
    pub log_file: PathBuf,

    /// Games to detect. Only these are reported: anything not listed here
    /// never leaves the machine.
    pub games: Vec<GameRule>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct GameRule {
    /// Name exactly as Twitch expects it, for example "League of Legends".
    pub name: String,

    /// When two rules match at once, the higher number wins.
    /// This is how the game beats its launcher.
    #[serde(default)]
    pub priority: i32,

    /// Executables that count as this game. Compared case-insensitively.
    pub processes: Vec<String>,
}

fn default_poll() -> u64 {
    5
}

fn default_heartbeat() -> u64 {
    60
}

fn default_log() -> PathBuf {
    PathBuf::from("agent.log")
}

impl Config {
    pub fn load(path: &Path) -> Result<Self, String> {
        let raw = std::fs::read_to_string(path).map_err(|e| {
            format!(
                "Could not read {}: {e}\n\
                 Copy agent.example.toml to agent.toml and complete it.",
                path.display()
            )
        })?;

        let cfg: Config =
            toml::from_str(&raw).map_err(|e| format!("{} has an error: {e}", path.display()))?;

        cfg.validate()?;
        Ok(cfg)
    }

    fn validate(&self) -> Result<(), String> {
        if self.endpoint.trim().is_empty() {
            return Err("Missing 'endpoint' in agent.toml".into());
        }
        if self.endpoint.starts_with("https://") {
            // Better a clear error here than an opaque transport failure on
            // the first send: this binary is built without TLS.
            return Err(
                "This build does not support https. Use http:// over Tailscale, or rebuild ureq with native-tls for a public endpoint."
                    .to_string(),
            );
        }
        if !self.endpoint.starts_with("http://") {
            return Err(format!(
                "'endpoint' must start with http:// (got: {})",
                self.endpoint
            ));
        }
        if self.token.trim().is_empty() || self.token.contains(TOKEN_PLACEHOLDER) {
            return Err("Set a real 'token' in agent.toml; it must match the bot token".into());
        }
        if self.games.is_empty() {
            return Err("No [[games]] entry is configured in agent.toml".into());
        }
        if self.poll_seconds == 0 {
            return Err("'poll_seconds' must be at least 1".into());
        }
        for game in &self.games {
            if game.processes.is_empty() {
                return Err(format!("Game '{}' has no 'processes'", game.name));
            }
        }
        Ok(())
    }

    /// Resolves a relative path against the executable's folder, so the
    /// agent behaves the same no matter who launches it.
    pub fn resolve(base: &Path, path: &Path) -> PathBuf {
        if path.is_absolute() {
            path.to_path_buf()
        } else {
            base.join(path)
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
//
// Naming: [method]_when_[scenario]_[expected_result]
// They touch neither the file system nor the network.

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    /// Valid reference config. Each test starts from it and changes only the
    /// field it wants to test, keeping the rest valid.
    fn valid() -> Config {
        Config {
            endpoint: "http://localhost:8787/game".to_string(),
            token: "my-secret-token".to_string(),
            poll_seconds: 5,
            heartbeat_seconds: 60,
            log_file: PathBuf::from("agent.log"),
            games: vec![GameRule {
                name: "League of Legends".to_string(),
                priority: 0,
                processes: vec!["League of Legends.exe".to_string()],
            }],
        }
    }

    // --- validate ---

    #[test]
    fn validate_when_config_is_valid_returns_ok() {
        assert!(valid().validate().is_ok());
    }

    #[test]
    fn validate_when_endpoint_is_empty_returns_err() {
        let mut cfg = valid();
        cfg.endpoint = "".to_string();
        let err = cfg.validate().unwrap_err();
        assert!(err.contains("endpoint"), "message: {err}");
    }

    #[test]
    fn validate_when_endpoint_is_only_whitespace_returns_err() {
        let mut cfg = valid();
        cfg.endpoint = "   ".to_string();
        assert!(cfg.validate().is_err());
    }

    #[test]
    fn validate_when_endpoint_uses_https_returns_err_with_hint() {
        let mut cfg = valid();
        cfg.endpoint = "https://example.com/game".to_string();
        let err = cfg.validate().unwrap_err();
        assert!(
            err.contains("https") || err.contains("TLS") || err.contains("tls"),
            "message: {err}"
        );
    }

    #[test]
    fn validate_when_endpoint_has_no_scheme_returns_err() {
        let mut cfg = valid();
        cfg.endpoint = "localhost:8787/game".to_string();
        let err = cfg.validate().unwrap_err();
        assert!(err.contains("http://"), "message: {err}");
    }

    #[test]
    fn validate_when_token_is_empty_returns_err() {
        let mut cfg = valid();
        cfg.token = "".to_string();
        let err = cfg.validate().unwrap_err();
        assert!(err.contains("token"), "message: {err}");
    }

    #[test]
    fn validate_when_token_is_placeholder_returns_err() {
        let mut cfg = valid();
        cfg.token = "CHANGEME".to_string();
        let err = cfg.validate().unwrap_err();
        assert!(err.contains("token"), "message: {err}");
    }

    #[test]
    fn validate_when_games_list_is_empty_returns_err() {
        let mut cfg = valid();
        cfg.games = vec![];
        let err = cfg.validate().unwrap_err();
        assert!(err.contains("games") || err.contains("game"), "message: {err}");
    }

    #[test]
    fn validate_when_poll_seconds_is_zero_returns_err() {
        let mut cfg = valid();
        cfg.poll_seconds = 0;
        let err = cfg.validate().unwrap_err();
        assert!(err.contains("poll"), "message: {err}");
    }

    #[test]
    fn validate_when_game_has_no_processes_returns_err_with_game_name() {
        let mut cfg = valid();
        cfg.games[0].processes = vec![];
        let err = cfg.validate().unwrap_err();
        assert!(
            err.contains("League of Legends") || err.contains("processes"),
            "message: {err}"
        );
    }

    #[test]
    fn validate_when_one_valid_and_one_empty_game_returns_err() {
        let mut cfg = valid();
        cfg.games.push(GameRule {
            name: "Broken Game".to_string(),
            priority: 0,
            processes: vec![],
        });
        assert!(cfg.validate().is_err());
    }

    // --- resolve ---

    #[test]
    fn resolve_when_path_is_absolute_returns_same_path() {
        let base = Path::new("C:\\agent");
        let abs = Path::new("C:\\logs\\agent.log");
        assert_eq!(Config::resolve(base, abs), PathBuf::from("C:\\logs\\agent.log"));
    }

    #[test]
    fn resolve_when_path_is_relative_joins_with_base() {
        let base = Path::new("/opt/agent");
        let rel = Path::new("agent.log");
        assert_eq!(
            Config::resolve(base, rel),
            PathBuf::from("/opt/agent/agent.log")
        );
    }

    #[test]
    fn resolve_when_path_is_dot_slash_joins_correctly() {
        let base = Path::new("/opt/agent");
        let rel = Path::new("logs/agent.log");
        assert_eq!(
            Config::resolve(base, rel),
            PathBuf::from("/opt/agent/logs/agent.log")
        );
    }
}
