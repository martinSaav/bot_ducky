//! Configuracion del agente: se lee de agent.toml, al lado del ejecutable.

use serde::Deserialize;
use std::path::{Path, PathBuf};

/// Marcador que trae el archivo de ejemplo. Si sigue ahi, la config no se toco.
const TOKEN_PLACEHOLDER: &str = "CAMBIAME";

#[derive(Debug, Deserialize)]
pub struct Config {
    /// URL del endpoint del bot, por ejemplo http://100.72.14.3:8787/game
    pub endpoint: String,

    /// Secreto compartido con el bot. Viaja en el header Authorization.
    pub token: String,

    /// Cada cuanto se miran los procesos.
    #[serde(default = "default_poll")]
    pub poll_seconds: u64,

    /// Cada cuanto se reenvia el estado aunque no haya cambiado, para que el
    /// bot sepa que el agente sigue vivo y no se quede con un dato viejo.
    #[serde(default = "default_heartbeat")]
    pub heartbeat_seconds: u64,

    /// Archivo de log. Relativo al ejecutable si no es ruta absoluta.
    #[serde(default = "default_log")]
    pub log_file: PathBuf,

    /// Juegos a detectar. Solo estos se reportan: lo que no esta aca no sale
    /// nunca de la maquina.
    pub games: Vec<GameRule>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct GameRule {
    /// Nombre tal cual lo espera Twitch, por ejemplo "League of Legends".
    pub name: String,

    /// Cuando hay dos coincidencias a la vez gana la de numero mas alto.
    /// Sirve para que el juego le gane al launcher.
    #[serde(default)]
    pub priority: i32,

    /// Ejecutables que cuentan como este juego. Se comparan sin distinguir
    /// mayusculas.
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
                "No pude leer {}: {e}\n\
                 Copia agent.example.toml a agent.toml y completalo.",
                path.display()
            )
        })?;

        let cfg: Config =
            toml::from_str(&raw).map_err(|e| format!("{} tiene un error: {e}", path.display()))?;

        cfg.validate()?;
        Ok(cfg)
    }

    fn validate(&self) -> Result<(), String> {
        if self.endpoint.trim().is_empty() {
            return Err("Falta 'endpoint' en agent.toml".into());
        }
        if self.endpoint.starts_with("https://") {
            // Mejor un error claro aca que un fallo de transporte opaco al
            // primer envio: este binario se compila sin TLS.
            return Err(
                "esta build no soporta https. Sobre Tailscale usa http:// (el tunel                  ya va cifrado); para exponerlo publico, recompila ureq con la                  feature native-tls o pone un proxy inverso adelante."
                    .to_string(),
            );
        }
        if !self.endpoint.starts_with("http://") {
            return Err(format!(
                "'endpoint' tiene que empezar con http:// (esta: {})",
                self.endpoint
            ));
        }
        if self.token.trim().is_empty() || self.token.contains(TOKEN_PLACEHOLDER) {
            return Err("Falta poner un 'token' real en agent.toml (el mismo que el bot)".into());
        }
        if self.games.is_empty() {
            return Err("No hay ningun [[games]] configurado en agent.toml".into());
        }
        if self.poll_seconds == 0 {
            return Err("'poll_seconds' tiene que ser 1 o mas".into());
        }
        for game in &self.games {
            if game.processes.is_empty() {
                return Err(format!("El juego '{}' no tiene 'processes'", game.name));
            }
        }
        Ok(())
    }

    /// Resuelve una ruta relativa contra la carpeta del ejecutable, para que
    /// el agente funcione igual lo lance quien lo lance.
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
// No tocan el sistema de archivos ni la red.

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    /// Config valida de referencia. Cada test parte de esta y muta solo el
    /// campo que quiere probar, manteniendo los demas en estado valido.
    fn valid() -> Config {
        Config {
            endpoint: "http://localhost:8787/game".to_string(),
            token: "mi-token-secreto".to_string(),
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
        assert!(err.contains("endpoint"), "mensaje: {err}");
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
            "mensaje: {err}"
        );
    }

    #[test]
    fn validate_when_endpoint_has_no_scheme_returns_err() {
        let mut cfg = valid();
        cfg.endpoint = "localhost:8787/game".to_string();
        let err = cfg.validate().unwrap_err();
        assert!(err.contains("http://"), "mensaje: {err}");
    }

    #[test]
    fn validate_when_token_is_empty_returns_err() {
        let mut cfg = valid();
        cfg.token = "".to_string();
        let err = cfg.validate().unwrap_err();
        assert!(err.contains("token"), "mensaje: {err}");
    }

    #[test]
    fn validate_when_token_is_placeholder_returns_err() {
        let mut cfg = valid();
        cfg.token = "CAMBIAME".to_string();
        let err = cfg.validate().unwrap_err();
        assert!(err.contains("token"), "mensaje: {err}");
    }

    #[test]
    fn validate_when_games_list_is_empty_returns_err() {
        let mut cfg = valid();
        cfg.games = vec![];
        let err = cfg.validate().unwrap_err();
        assert!(err.contains("games") || err.contains("juego"), "mensaje: {err}");
    }

    #[test]
    fn validate_when_poll_seconds_is_zero_returns_err() {
        let mut cfg = valid();
        cfg.poll_seconds = 0;
        let err = cfg.validate().unwrap_err();
        assert!(err.contains("poll"), "mensaje: {err}");
    }

    #[test]
    fn validate_when_game_has_no_processes_returns_err_with_game_name() {
        let mut cfg = valid();
        cfg.games[0].processes = vec![];
        let err = cfg.validate().unwrap_err();
        assert!(
            err.contains("League of Legends") || err.contains("processes"),
            "mensaje: {err}"
        );
    }

    #[test]
    fn validate_when_one_valid_and_one_empty_game_returns_err() {
        let mut cfg = valid();
        cfg.games.push(GameRule {
            name: "Juego Roto".to_string(),
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
