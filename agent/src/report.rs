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
            Err(ureq::Error::Transport(error)) => Err(describe_unreachable(&self.log_endpoint, &error.to_string())),
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
            Err(ureq::Error::Transport(t)) => Err(describe_unreachable(&self.endpoint, &t.to_string())),
        }
    }
}

/// One readable line for a failed send: where, what happened and the likely cause.
///
/// ureq's message repeats the URL and carries the Windows error text in the
/// system language ("os error 10060"); the cause is recognised from it and
/// said in plain words instead.
pub fn describe_unreachable(endpoint: &str, error: &str) -> String {
    let lower = error.to_lowercase();
    // "status line" first: it also carries 10060, but the connection did open.
    let cause = if lower.contains("status line") {
        "connected, but the bot did not answer (it may be starting or stuck)".to_string()
    } else if lower.contains("os error 10061") || lower.contains("refused") {
        "connection refused (the PC is on but the bot is not running; is Docker up?)".to_string()
    } else if lower.contains("os error 10054") || lower.contains("reset") {
        "the connection was cut (the bot restarted or the network dropped)".to_string()
    } else if lower.contains("timed out") || lower.contains("os error 10060") {
        "no answer (the bot's PC may be off or asleep, or a firewall blocks the port)".to_string()
    } else {
        let detail = error.strip_prefix(endpoint).unwrap_or(error);
        let detail = detail.trim_start_matches(':').split_whitespace().collect::<Vec<_>>().join(" ");
        detail.chars().take(200).collect()
    };
    format!("Bot unreachable at {}: {cause}", host_port(endpoint))
}

/// "http://192.168.0.188:8787/game" -> "192.168.0.188:8787".
pub fn host_port(endpoint: &str) -> &str {
    let rest = endpoint.split_once("://").map_or(endpoint, |(_, rest)| rest);
    rest.split('/').next().unwrap_or(rest)
}

#[cfg(test)]
mod tests {
    use super::*;

    const ENDPOINT: &str = "http://192.168.0.188:8787/game";

    // Real errors from agent.log (Windows in Spanish), as ureq prints them.
    const TIMED_OUT: &str = "http://192.168.0.188:8787/game: Connection Failed: Connect error: connection timed out";
    const REFUSED: &str = "http://192.168.0.188:8787/game: Connection Failed: Connect error: No se puede establecer \
        una conexión ya que el equipo de destino denegó expresamente dicha conexión. (os error 10061)";
    const NO_RESPONSE: &str = "http://192.168.0.188:8787/game: Network Error: Network Error: Error encountered in \
        the status line: Se produjo un error durante el intento de conexión ya que la parte conectada no respondió \
        adecuadamente tras un periodo de tiempo, o bien se produjo un error en la conexión establecida ya que el \
        host conectado no ha podido responder. (os error 10060)";
    const RESET: &str = "http://192.168.0.188:8787/game: Network Error: Se ha forzado la interrupción de una \
        conexión existente por el host remoto. (os error 10054)";

    #[test]
    fn describe_when_connect_times_out_says_the_pc_may_be_off() {
        assert_eq!(
            describe_unreachable(ENDPOINT, TIMED_OUT),
            "Bot unreachable at 192.168.0.188:8787: no answer (the bot's PC may be off or asleep, or a firewall blocks the port)"
        );
    }

    #[test]
    fn describe_when_refused_says_the_bot_is_not_running() {
        assert_eq!(
            describe_unreachable(ENDPOINT, REFUSED),
            "Bot unreachable at 192.168.0.188:8787: connection refused (the PC is on but the bot is not running; is Docker up?)"
        );
    }

    #[test]
    fn describe_when_connected_but_no_reply_says_the_bot_did_not_answer() {
        assert_eq!(
            describe_unreachable(ENDPOINT, NO_RESPONSE),
            "Bot unreachable at 192.168.0.188:8787: connected, but the bot did not answer (it may be starting or stuck)"
        );
    }

    #[test]
    fn describe_when_reset_says_the_connection_was_cut() {
        assert_eq!(
            describe_unreachable(ENDPOINT, RESET),
            "Bot unreachable at 192.168.0.188:8787: the connection was cut (the bot restarted or the network dropped)"
        );
    }

    #[test]
    fn describe_when_unknown_keeps_the_detail_without_repeating_the_url() {
        assert_eq!(
            describe_unreachable(ENDPOINT, "http://192.168.0.188:8787/game: Something odd happened"),
            "Bot unreachable at 192.168.0.188:8787: Something odd happened"
        );
    }

    #[test]
    fn describe_never_repeats_the_url_or_the_os_text() {
        for error in [TIMED_OUT, REFUSED, NO_RESPONSE, RESET] {
            let line = describe_unreachable(ENDPOINT, error);
            assert!(!line.contains("http://"), "{line}");
            assert!(!line.contains("os error"), "{line}");
        }
    }

    #[test]
    fn describe_uses_host_and_port_of_any_endpoint() {
        let line = describe_unreachable("http://bot.tail1234.ts.net:8787/game", "x: connection timed out");
        assert!(line.starts_with("Bot unreachable at bot.tail1234.ts.net:8787: no answer"), "{line}");
    }
}
