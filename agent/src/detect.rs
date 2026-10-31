//! Deteccion de juegos por proceso en ejecucion.
//!
//! Trabaja con lista blanca: solo los ejecutables declarados en agent.toml se
//! comparan y solo esos pueden reportarse. El resto de los procesos se miran
//! y se descartan en el acto, nunca salen de la maquina.
//!
//! Para Valorant y League ademas se lee el estado de la partida de la API
//! local de cada cliente (ver `valorant.rs` y `lol.rs`).

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
    /// Nombre del juego tal como lo espera Twitch.
    pub game: String,
    /// Ejecutable que disparo la coincidencia. Solo para el log.
    pub exe: String,
    /// Partida activa: el gameId de LoL, o una clave propia en Valorant.
    pub match_id: Option<String>,
    /// Estado de la partida segun el cliente local (fase, resultado, score).
    pub metadata: Option<Value>,
}

struct Rule {
    game: String,
    priority: i32,
    /// Ejecutable en minusculas -> ejecutable original, para loguearlo lindo.
    processes: HashMap<String, String>,
}

/// Procesos del cliente de League, con cuantas carpetas hay que subir desde
/// su ejecutable para llegar a la raiz de la instalacion (donde esta el lockfile).
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

        // Solo pedimos la lista de procesos: sin CPU, memoria ni linea de
        // comandos. Es mas barato y no leemos nada que no necesitemos.
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
        // `ProcessRefreshKind::new()` trae solo nombres. `refresh_processes`
        // a secas sumaria CPU, memoria, disco y ruta de cada proceso, cada
        // 5 segundos, en la PC donde ella esta jugando y transmitiendo.
        // `true` descarta los procesos muertos, si no la lista crece sola.
        self.system
            .refresh_processes_specifics(ProcessesToUpdate::All, true, ProcessRefreshKind::new());
    }

    /// El juego de mayor prioridad que este corriendo, o None.
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

    /// Al dejar de jugar un juego se olvida su estado: si vuelve mas tarde,
    /// arranca de cero en vez de arrastrar una partida vieja.
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

    /// Lockfile del cliente de League, deducido de la ruta de su ejecutable.
    ///
    /// La carpeta de instalacion cambia de PC en PC. Se pide la ruta solo del
    /// proceso del cliente, no la de todos.
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

    /// Logica de seleccion pura: dado un slice de nombres de procesos (ya en
    /// minusculas) devuelve el Detected de mayor prioridad, o None.
    ///
    /// Separada de `detect` para poder testearse sin tocar el SO real.
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

    /// Procesos en ejecucion, ordenados y sin repetir.
    ///
    /// Solo lo usa `--scan`, que imprime en pantalla para poder averiguar el
    /// ejecutable de un juego nuevo. Nunca se envia por red.
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
// Todos trabajan con process-lists sinteticas: no leen el SO real.

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::GameRule;

    /// Construye un Detector a partir de reglas en texto plano.
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

    /// Shorthand: lista de nombres de procesos ya en minusculas.
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
        let result = d.select_winner(&running).expect("debe detectar LoL");
        assert_eq!(result.game, "League of Legends");
    }

    #[test]
    fn select_winner_when_process_name_is_uppercase_still_matches() {
        // Los nombres llegan en minusculas desde detect(), pero nos aseguramos
        // de que la logica sea robusta igual.
        let d = detector(&[("VALORANT", 10, &["VALORANT-Win64-Shipping.exe"])]);
        let running = procs(&["valorant-win64-shipping.exe"]);
        let result = d.select_winner(&running).expect("debe detectar VALORANT");
        assert_eq!(result.game, "VALORANT");
    }

    #[test]
    fn select_winner_when_higher_priority_game_running_returns_it() {
        let d = detector(&[
            ("League of Legends", 5, &["League of Legends.exe"]),
            ("VALORANT", 10, &["VALORANT-Win64-Shipping.exe"]),
        ]);
        let running = procs(&["league of legends.exe", "valorant-win64-shipping.exe"]);
        let result = d.select_winner(&running).expect("debe detectar algo");
        assert_eq!(result.game, "VALORANT"); // prioridad 10 > 5
    }

    #[test]
    fn select_winner_when_only_lower_priority_game_running_returns_it() {
        let d = detector(&[
            ("Launcher", 1, &["launcher.exe"]),
            ("VALORANT", 10, &["VALORANT-Win64-Shipping.exe"]),
        ]);
        // Solo el launcher esta corriendo, VALORANT no
        let running = procs(&["launcher.exe"]);
        let result = d.select_winner(&running).expect("debe detectar el launcher");
        assert_eq!(result.game, "Launcher");
    }

    #[test]
    fn select_winner_when_priority_tied_returns_first_rule_in_config() {
        // Empate de prioridad: gana la primer regla declarada en agent.toml
        let d = detector(&[
            ("Juego A", 10, &["juego_a.exe"]),
            ("Juego B", 10, &["juego_b.exe"]),
        ]);
        let running = procs(&["juego_a.exe", "juego_b.exe"]);
        let result = d.select_winner(&running).expect("debe detectar algo");
        assert_eq!(result.game, "Juego A"); // primera regla gana el empate
    }

    #[test]
    fn select_winner_when_game_has_multiple_executables_matches_any() {
        let d = detector(&[("League of Legends", 10, &[
            "League of Legends.exe",
            "LeagueClient.exe",
            "LeagueClientUxRender.exe",
        ])]);
        // Solo el cliente, no el juego en si
        let running = procs(&["leagueclient.exe"]);
        let result = d.select_winner(&running).expect("debe detectar LoL");
        assert_eq!(result.game, "League of Legends");
    }

    #[test]
    fn select_winner_when_match_found_exe_field_is_original_case() {
        // El campo `exe` del Detected debe tener el nombre tal como esta en
        // la config (con mayusculas originales), no en minusculas.
        let d = detector(&[("League of Legends", 10, &["League of Legends.exe"])]);
        let running = procs(&["league of legends.exe"]);
        let result = d.select_winner(&running).expect("debe detectar LoL");
        assert_eq!(result.exe, "League of Legends.exe");
    }
}
