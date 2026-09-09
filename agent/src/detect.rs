//! Deteccion de juegos por proceso en ejecucion.
//!
//! Trabaja con lista blanca: solo los ejecutables declarados en agent.toml se
//! comparan y solo esos pueden reportarse. El resto de los procesos se miran
//! y se descartan en el acto, nunca salen de la maquina.

use std::collections::HashMap;

use sysinfo::{ProcessRefreshKind, ProcessesToUpdate, RefreshKind, System};

use crate::config::GameRule;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Detected {
    /// Nombre del juego tal como lo espera Twitch.
    pub game: String,
    /// Ejecutable que disparo la coincidencia. Solo para el log.
    pub exe: String,
}

struct Rule {
    game: String,
    priority: i32,
    /// Ejecutable en minusculas -> ejecutable original, para loguearlo lindo.
    processes: HashMap<String, String>,
}

pub struct Detector {
    rules: Vec<Rule>,
    system: System,
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

        Self { rules, system }
    }

    /// Cuantos juegos distintos hay configurados.
    pub fn rule_count(&self) -> usize {
        self.rules.len()
    }

    /// El juego de mayor prioridad que este corriendo, o None.
    pub fn detect(&mut self) -> Option<Detected> {
        // `true` descarta los procesos muertos, si no la lista crece sola.
        self.system
            .refresh_processes(ProcessesToUpdate::All, true);

        let running: Vec<String> = self
            .system
            .processes()
            .values()
            .map(|p| p.name().to_string_lossy().to_lowercase())
            .collect();

        let mut best: Option<(i32, Detected)> = None;

        for rule in &self.rules {
            for name in &running {
                if let Some(original) = rule.processes.get(name) {
                    let candidate = Detected {
                        game: rule.game.clone(),
                        exe: original.clone(),
                    };
                    // Empate de prioridad: gana el primero de agent.toml,
                    // asi el orden del archivo es un desempate predecible.
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
        // `true` descarta los procesos muertos, si no la lista crece sola.
        self.system
            .refresh_processes(ProcessesToUpdate::All, true);
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
