//! Sending the state to the bot.
//!
//! The ABSOLUTE state is always sent ("now playing X" or "now playing
//! nothing"), never a delta. A lost message fixes itself on the next
//! heartbeat, with no queue and no retries that need memory.

use std::time::Duration;

use serde_json::json;

use crate::clock;
use crate::detect::Detected;

pub struct Reporter {
    endpoint: String,
    log_endpoint: String,
    token: String,
    host: String,
    http: ureq::Agent,
}

impl Reporter {
    pub fn new(endpoint: String, token: String) -> Self {
        let http = ureq::AgentBuilder::new()
            .timeout_connect(Duration::from_secs(5))
            .timeout(Duration::from_secs(10))
            .user_agent(concat!("twitch-game-agent/", env!("CARGO_PKG_VERSION")))
            .build();

        let host = std::env::var("COMPUTERNAME")
            .or_else(|_| std::env::var("HOSTNAME"))
            .unwrap_or_else(|_| "unknown".to_string());

        Self {
            log_endpoint: endpoint
                .strip_suffix("/game")
                .map(|base| format!("{base}/agent/logs"))
                .unwrap_or_else(|| format!("{}/agent/logs", endpoint.trim_end_matches('/'))),
            endpoint,
            token,
            host,
            http,
        }
    }

    pub fn endpoint(&self) -> &str {
        &self.endpoint
    }

    pub fn send_logs(&self, logs: &[String]) -> Result<(), String> {
        if logs.is_empty() {
            return Ok(());
        }

        let body = json!({
            "source": "agent",
            "logs": logs,
            "sent_at": clock::iso_utc(),
        });

        match self
            .http
            .post(&self.log_endpoint)
            .set("Authorization", &format!("Bearer {}", self.token))
            .send_json(body)
        {
            Ok(_) => Ok(()),
            Err(ureq::Error::Status(401, _)) => Err("Bot rejected the token while sending logs".to_string()),
            Err(ureq::Error::Status(code, _)) => Err(format!("Bot returned HTTP {code} while sending logs")),
            Err(ureq::Error::Transport(error)) => Err(format!("Could not send logs to bot: {error}")),
        }
    }

    pub fn send(&self, detected: Option<&Detected>, logs: &[String]) -> Result<(), String> {
        let body = json!({
            "source": "agent",
            "game": detected.map(|d| d.game.as_str()),
            "match_id": detected.and_then(|d| d.match_id.as_deref()),
            "metadata": detected.and_then(|d| d.metadata.as_ref()),
            "exe": detected.map(|d| d.exe.as_str()),
            "host": self.host,
            "agent_version": env!("CARGO_PKG_VERSION"),
            "sent_at": clock::iso_utc(),
            "logs": logs,
        });

        let result = self
            .http
            .post(&self.endpoint)
            .set("Authorization", &format!("Bearer {}", self.token))
            .send_json(body);

        match result {
            Ok(_) => Ok(()),
            Err(ureq::Error::Status(401, _)) => Err(
                "Bot rejected the token (401); agent.toml and .env must use the same token"
                    .to_string(),
            ),
            Err(ureq::Error::Status(code, resp)) => {
                let detail = resp
                    .into_string()
                    .unwrap_or_else(|_| "no body".to_string());
                let detail = detail.chars().take(200).collect::<String>();
                Err(format!("Bot returned HTTP {code}: {detail}"))
            }
            Err(ureq::Error::Transport(t)) => {
                Err(format!("Could not contact bot at {}: {t}", self.endpoint))
            }
        }
    }
}
