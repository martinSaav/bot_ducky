//! Agent that runs on the streamer's PC.
//!
//! Every few seconds it checks whether a game from the allowlist is running
//! and tells the bot. It does not touch Twitch, stores nobody's credentials,
//! and reports no process that is not declared in agent.toml.

mod clock;
mod config;
mod detect;
mod local;
mod lol;
mod report;
mod valorant;

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

    // --scan needs no config: its whole point is to help write one.
    if args.iter().any(|a| a == "--scan") {
        scan();
        return;
    }

    let cfg = match Config::load(&config_path) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("{} ERROR   [agent] [config] {e}", clock::log_stamp());
            std::process::exit(1);
        }
    };

    let mut detector = Detector::new(&cfg.games);

    if args.iter().any(|a| a == "--once") {
        // Also shows what the client's local API says: the first thing to
        // check when a prediction is not created.
        let detected = detector.detect();
        for line in detector.take_diagnostics() {
            println!("  {line}");
        }
        match detected {
            Some(d) => {
                println!("Detected: {} (process {})", d.game, d.exe);
                if let Some(id) = &d.match_id {
                    println!("Active match: {id}");
                }
                if let Some(meta) = &d.metadata {
                    println!("Match state: {meta}");
                }
            }
            None => println!("No configured game is running."),
        }
        return;
    }

    let reporter = Reporter::new(cfg.endpoint.clone(), cfg.token.clone());
    let log_path = Config::resolve(&base, &cfg.log_file);
    let mut log = Logger::new(log_path, &reporter);

    log.info(&format!(
        "Agent v{VERSION} started | watching {} games | every {}s | destination {}",
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
        for diagnostic in detector.take_diagnostics() {
            log.raw(&diagnostic);
        }

        let changed = !ever_sent || current != last_reported;
        // The Valorant score changes every round: that is sent, but
        // "Detected" is only logged when the game or the match changes.
        let identity_changed = !ever_sent || identity(&current) != identity(&last_reported);
        let heartbeat_due = last_send.elapsed() >= heartbeat;

        // A failed send leaves `ever_sent` false, so the next tick retries
        // without needing a separate queue.
        if changed || heartbeat_due {
                match reporter.send(current.as_ref(), log.pending()) {
                Ok(()) => {
                        log.clear_pending();
                    if identity_changed {
                        match &current {
                            Some(d) => {
                                let match_part = d.match_id.as_deref()
                                    .map(|id| format!(" [match: {}]", &id[..id.len().min(36)]))
                                    .unwrap_or_default();
                                log.info(&format!("Detected {} ({}){}", d.game, d.exe, match_part));
                            }
                            None => log.info("No configured game is running"),
                        }
                    }
                    if failures > 0 {
                        log.info(&format!("Connection recovered after {failures} attempts"));
                        failures = 0;
                    }
                    last_reported = current;
                    ever_sent = true;
                    last_send = Instant::now();
                }
                Err(e) => {
                    // Without this, a bot that is down would fill the log with
                    // the same line every 5 seconds.
                    if failures < 3 || failures % 60 == 0 {
                        log.warning(&e);
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

/// What identifies a report for the log: game, executable and match.
fn identity(d: &Option<Detected>) -> Option<(&str, &str, Option<&str>)> {
    d.as_ref().map(|d| (d.game.as_str(), d.exe.as_str(), d.match_id.as_deref()))
}

fn scan() {
    println!("Running processes (printed locally only; never sent anywhere):\n");
    let mut detector = Detector::new(&[]);
    for name in detector.running_processes() {
        println!("  {name}");
    }
    println!("\nCopy the game executable into a [[games]] block in agent.toml.");
}

fn print_help() {
    println!(
        "\
twitch-game-agent v{VERSION}
Reports the running game to the bot so it can update the channel category.

USAGE:
    twitch-game-agent.exe              run in the background (normal mode)
    twitch-game-agent.exe --once       print the current detection and exit
    twitch-game-agent.exe --scan       list processes to configure a game
    twitch-game-agent.exe --config X   use a different configuration file
    twitch-game-agent.exe --help       show this help

Configuration lives in agent.toml next to the executable.
Only games declared there are reported."
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

/// Logs to a file and to the console. If the file cannot be opened we carry
/// on: failing to log is no reason for the agent to stop working.
struct Logger<'a> {
    path: PathBuf,
    reporter: &'a Reporter,
    pending: Vec<String>,
}

impl<'a> Logger<'a> {
    fn new(path: PathBuf, reporter: &'a Reporter) -> Self {
        Self {
            path,
            reporter,
            pending: Vec::new(),
        }
    }

    fn raw(&mut self, msg: &str) {
        let (level, message) = if let Some(message) = msg.strip_prefix("[INFO] ") {
            ("INFO", message)
        } else if let Some(message) = msg.strip_prefix("[WARNING] ") {
            ("WARNING", message)
        } else if let Some(message) = msg.strip_prefix("[ERROR] ") {
            ("ERROR", message)
        } else if let Some(message) = msg.strip_prefix("[DEBUG] ") {
            ("DEBUG", message)
        } else {
            ("INFO", msg)
        };
        self.write(level, message);
    }

    fn info(&mut self, msg: &str) {
        self.write("INFO", msg);
    }

    fn warning(&mut self, msg: &str) {
        self.write("WARNING", msg);
    }

    fn write(&mut self, level: &str, msg: &str) {
        let entry = format!("{} {:<7} [agent] {msg}", clock::log_stamp(), level);
        println!("{entry}");

        if let Ok(mut file) = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
        {
            let _ = writeln!(file, "{entry}");
        }

        const MAX_PENDING: usize = 200;
        self.pending.push(entry);
        if self.pending.len() > MAX_PENDING {
            self.pending.drain(..self.pending.len() - MAX_PENDING);
        }
        if self.reporter.send_logs(&self.pending).is_ok() {
            self.pending.clear();
        }
    }

    fn pending(&self) -> &[String] {
        &self.pending
    }

    fn clear_pending(&mut self) {
        self.pending.clear();
    }
}
