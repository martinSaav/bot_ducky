//! Envio del estado al bot.
//!
//! Siempre se manda el estado ABSOLUTO ("ahora esta jugando X" o "ahora no
//! juega nada"), nunca un delta. Asi un mensaje perdido se corrige solo en el
//! siguiente latido y no hace falta cola ni reintentos con memoria.

use std::time::Duration;

use serde_json::json;

use crate::clock;
use crate::detect::Detected;

pub struct Reporter {
    endpoint: String,
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
            .unwrap_or_else(|_| "desconocido".to_string());

        Self {
            endpoint,
            token,
            host,
            http,
        }
    }

    pub fn endpoint(&self) -> &str {
        &self.endpoint
    }

    pub fn send(&self, detected: Option<&Detected>) -> Result<(), String> {
        let body = json!({
            "source": "agent",
            "game": detected.map(|d| d.game.as_str()),
            "match_id": detected.and_then(|d| d.match_id.as_deref()),
            "exe": detected.map(|d| d.exe.as_str()),
            "host": self.host,
            "agent_version": env!("CARGO_PKG_VERSION"),
            "sent_at": clock::iso_utc(),
        });

        let result = self
            .http
            .post(&self.endpoint)
            .set("Authorization", &format!("Bearer {}", self.token))
            .send_json(body);

        match result {
            Ok(_) => Ok(()),
            Err(ureq::Error::Status(401, _)) => Err(
                "el bot rechazo el token (401). Tiene que ser el mismo en agent.toml y en el .env"
                    .to_string(),
            ),
            Err(ureq::Error::Status(code, resp)) => {
                let detail = resp
                    .into_string()
                    .unwrap_or_else(|_| "sin cuerpo".to_string());
                let detail = detail.chars().take(200).collect::<String>();
                Err(format!("el bot respondio HTTP {code}: {detail}"))
            }
            Err(ureq::Error::Transport(t)) => {
                Err(format!("no pude contactar al bot en {}: {t}", self.endpoint))
            }
        }
    }
}
