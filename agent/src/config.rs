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
