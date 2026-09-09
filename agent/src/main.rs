//! Agente que corre en la PC del streamer.
//!
//! Cada pocos segundos mira si hay algun juego de la lista blanca corriendo y
//! se lo avisa al bot. No toca Twitch, no guarda credenciales de nadie y no
//! reporta ningun proceso que no este declarado en agent.toml.

mod clock;
mod config;
mod detect;
mod report;

use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use config::Config;
use detect::{Detected, Detector};
use report::Reporter;

const VERSION: &str = env!("CARGO_PKG_VERSION");

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();

    if args.iter().any(|a| a == "--help" || a == "-h") {
        print_help();
        return;
    }

    let base = exe_dir();
    let config_path = match arg_value(&args, "--config") {
        Some(p) => Config::resolve(&base, Path::new(&p)),
        None => base.join("agent.toml"),
    };

    // --scan no necesita config: sirve justamente para armarla.
    if args.iter().any(|a| a == "--scan") {
        scan();
        return;
    }

    let cfg = match Config::load(&config_path) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[X] {e}");
            std::process::exit(1);
        }
    };

    let log_path = Config::resolve(&base, &cfg.log_file);
    let mut log = Logger::new(log_path);

    let mut detector = Detector::new(&cfg.games);

    if args.iter().any(|a| a == "--once") {
        match detector.detect() {
            Some(d) => println!("Detectado: {} (proceso {})", d.game, d.exe),
            None => println!("Ningun juego de la lista esta corriendo."),
        }
        return;
    }

    let reporter = Reporter::new(cfg.endpoint.clone(), cfg.token.clone());

    log.line(&format!(
        "agente v{VERSION} iniciado | {} juegos vigilados | cada {}s | destino {}",
        detector.rule_count(),
        cfg.poll_seconds,
        reporter.endpoint()
    ));

    run_loop(&cfg, &mut detector, &reporter, &mut log);
}

fn run_loop(cfg: &Config, detector: &mut Detector, reporter: &Reporter, log: &mut Logger) {
    let poll = Duration::from_secs(cfg.poll_seconds);
    let heartbeat = Duration::from_secs(cfg.heartbeat_seconds);

    let mut last_reported: Option<Detected> = None;
    let mut ever_sent = false;
    let mut last_send = Instant::now();
    let mut failures: u32 = 0;

    loop {
        let current = detector.detect();

        let changed = !ever_sent || current != last_reported;
        let heartbeat_due = last_send.elapsed() >= heartbeat;

        // Un envio fallido deja `ever_sent` en false, asi el proximo tick
        // reintenta sin necesidad de una cola aparte.
        if changed || heartbeat_due {
            match reporter.send(current.as_ref()) {
                Ok(()) => {
                    if changed {
                        match &current {
                            Some(d) => log.line(&format!("-> {} ({})", d.game, d.exe)),
                            None => log.line("-> sin juego"),
                        }
                    }
                    if failures > 0 {
                        log.line(&format!("conexion recuperada tras {failures} intentos"));
                        failures = 0;
                    }
                    last_reported = current;
                    ever_sent = true;
                    last_send = Instant::now();
                }
                Err(e) => {
                    // Sin esto un bot apagado llenaria el log con una linea
                    // igual cada 5 segundos.
                    if failures < 3 || failures % 60 == 0 {
                        log.line(&format!("[!] {e}"));
                    }
                    failures = failures.saturating_add(1);
                    ever_sent = false;
                    last_send = Instant::now();
                }
            }
        }

        std::thread::sleep(poll);
    }
}

fn scan() {
    println!("Procesos en ejecucion (esto se imprime solo aca, no se envia a ningun lado):\n");
    let mut detector = Detector::new(&[]);
    for name in detector.running_processes() {
        println!("  {name}");
    }
    println!("\nCopia el ejecutable del juego a un bloque [[games]] de agent.toml.");
}

fn print_help() {
    println!(
        "\
twitch-game-agent v{VERSION}
Avisa al bot que juego esta corriendo, para que cambie la categoria del canal.

USO:
  twitch-game-agent.exe              corre en segundo plano (uso normal)
  twitch-game-agent.exe --once       dice que detecta ahora y sale
  twitch-game-agent.exe --scan       lista los procesos, para agregar un juego
  twitch-game-agent.exe --config X   usa otro archivo de configuracion
  twitch-game-agent.exe --help       esta ayuda

La configuracion vive en agent.toml, al lado del ejecutable.
Solo se reportan los juegos declarados ahi."
    );
}

fn arg_value(args: &[String], flag: &str) -> Option<String> {
    let idx = args.iter().position(|a| a == flag)?;
    args.get(idx + 1).cloned()
}

fn exe_dir() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf))
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Log a archivo y a consola. Si el archivo no se puede abrir seguimos igual:
/// no poder loguear no es motivo para que el agente deje de funcionar.
struct Logger {
    path: PathBuf,
}

impl Logger {
    fn new(path: PathBuf) -> Self {
        Self { path }
    }

    fn line(&mut self, msg: &str) {
        let entry = format!("{}  {msg}", clock::log_stamp());
        println!("{entry}");

        if let Ok(mut file) = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
        {
            let _ = writeln!(file, "{entry}");
        }
    }
}
