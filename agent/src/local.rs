//! Plumbing comun para las APIs locales de los clientes de Riot.
//!
//! El Riot Client (Valorant) y el cliente de League (LCU) exponen APIs HTTPS
//! en 127.0.0.1 con un certificado auto-firmado, protegidas con la contrasena
//! de un lockfile. Las llamadas se delegan a curl.exe, que viene con Windows
//! 10/11: el agente no necesita una pila TLS propia, y el toolchain GNU no
//! necesita un compilador de C para armarla.

use std::collections::HashMap;
use std::path::Path;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

const STATUS_MARKER: &str = "__HTTP_STATUS__:";

/// Resultado de un GET a una API local.
///
/// Distinguir el 404 importa: en estas APIs significa "eso no existe ahora"
/// (no hay partida, no hay pantalla de fin), mientras que cualquier otro
/// fallo significa que algo anda mal. Mezclar los dos fue lo que escondio que
/// el agente le pedia a localhost endpoints que solo existen en Riot.
#[derive(Debug, Clone, PartialEq)]
pub enum Fetch {
    Ok(Value),
    NotFound,
    Failed(String),
}

/// Lo que un juego le aporta al reporte: la partida activa y su estado.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Snapshot {
    pub match_id: Option<String>,
    pub metadata: Option<Value>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Lockfile {
    pub port: u16,
    pub password: String,
}

impl Lockfile {
    pub fn auth(&self) -> String {
        format!("Basic {}", base64_encode(format!("riot:{}", self.password).as_bytes()))
    }

    pub fn url(&self, path: &str) -> String {
        format!("https://127.0.0.1:{}{}", self.port, path)
    }
}

/// Formato del lockfile: `nombre:pid:puerto:contrasena:protocolo`.
pub fn parse_lockfile(content: &str) -> Result<Lockfile, String> {
    let parts: Vec<&str> = content.trim().split(':').collect();
    if parts.len() < 5 {
        return Err(format!("unexpected lockfile format ({} fields)", parts.len()));
    }
    let port = parts[2]
        .parse::<u16>()
        .map_err(|e| format!("invalid lockfile port '{}': {e}", parts[2]))?;
    if parts[3].is_empty() {
        return Err("lockfile has no password".to_string());
    }
    Ok(Lockfile {
        port,
        password: parts[3].to_string(),
    })
}

pub fn read_lockfile(path: &Path) -> Result<Lockfile, String> {
    let content = std::fs::read_to_string(path)
        .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    parse_lockfile(&content)
}

/// GET HTTPS via curl.exe, aceptando el certificado auto-firmado local.
pub fn get(url: &str, auth: Option<&str>) -> Fetch {
    let mut cmd = Command::new("curl.exe");
    cmd.args([
        "--silent",
        "--show-error",
        "--insecure",
        "--max-time",
        "4",
        "--write-out",
        "\n__HTTP_STATUS__:%{http_code}",
    ]);
    if let Some(auth) = auth {
        cmd.arg("--header").arg(format!("Authorization: {auth}"));
    }
    cmd.arg(url);
    hide_window(&mut cmd);

    let output = match cmd.output() {
        Ok(o) => o,
        Err(e) => return Fetch::Failed(format!("cannot start curl.exe: {e}")),
    };
    // curl sale con 0 ante cualquier respuesta HTTP (404 incluido) y con otro
    // codigo solo si no pudo hablar con el servidor: cliente cerrado, puerto
    // equivocado, timeout.
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Fetch::Failed(format!(
            "curl exit code {}: {}",
            output.status.code().unwrap_or(-1),
            stderr.trim()
        ));
    }
    parse_curl_output(&String::from_utf8_lossy(&output.stdout))
}

/// Separa el cuerpo del codigo HTTP que curl agrega al final con --write-out.
pub fn parse_curl_output(stdout: &str) -> Fetch {
    let Some((body, status)) = stdout.rsplit_once(STATUS_MARKER) else {
        return Fetch::Failed("curl returned no HTTP status".to_string());
    };
    let (body, status) = (body.trim(), status.trim());
    if status == "404" {
        return Fetch::NotFound;
    }
    if !status.starts_with('2') {
        return Fetch::Failed(format!("HTTP {status}: {}", clip(body, 160)));
    }
    if body.is_empty() {
        return Fetch::Failed("empty response".to_string());
    }
    match serde_json::from_str(body) {
        Ok(v) => Fetch::Ok(v),
        Err(e) => Fetch::Failed(format!("invalid JSON ({e}): {}", clip(body, 120))),
    }
}

/// Sin esto, cuando el agente corre oculto cada llamada a curl podria abrir
/// una consola fugaz en la PC de ella.
#[cfg(windows)]
fn hide_window(cmd: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    cmd.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn hide_window(_cmd: &mut Command) {}

pub fn clip(s: &str, max: usize) -> String {
    s.chars().take(max).collect()
}

/// Muestra solo las puntas de un identificador: alcanza para reconocerlo en
/// el log sin dejarlo entero.
pub fn mask(id: &str) -> String {
    let n = id.chars().count();
    if n <= 12 {
        return id.to_string();
    }
    let head: String = id.chars().take(8).collect();
    let tail: String = id.chars().skip(n - 4).collect();
    format!("{head}...{tail}")
}

pub fn unix_millis() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0)
}

const B64: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

pub fn base64_encode(input: &[u8]) -> String {
    let mut out = String::with_capacity(input.len().div_ceil(3) * 4);
    for chunk in input.chunks(3) {
        let b0 = chunk[0] as usize;
        let b1 = chunk.get(1).copied().unwrap_or(0) as usize;
        let b2 = chunk.get(2).copied().unwrap_or(0) as usize;
        out.push(B64[b0 >> 2] as char);
        out.push(B64[((b0 & 3) << 4) | (b1 >> 4)] as char);
        out.push(if chunk.len() > 1 { B64[((b1 & 0xf) << 2) | (b2 >> 6)] as char } else { '=' });
        out.push(if chunk.len() > 2 { B64[b2 & 0x3f] as char } else { '=' });
    }
    out
}

/// Acepta base64 estandar y la variante URL-safe, con o sin relleno.
pub fn base64_decode(input: &str) -> Option<Vec<u8>> {
    let mut out = Vec::with_capacity(input.len() * 3 / 4);
    let mut buf: u32 = 0;
    let mut bits: u32 = 0;
    for c in input.bytes() {
        let v = match c {
            b'A'..=b'Z' => c - b'A',
            b'a'..=b'z' => c - b'a' + 26,
            b'0'..=b'9' => c - b'0' + 52,
            b'+' | b'-' => 62,
            b'/' | b'_' => 63,
            b'=' => break,
            b' ' | b'\n' | b'\r' | b'\t' => continue,
            _ => return None,
        } as u32;
        buf = (buf << 6) | v;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((buf >> bits) as u8);
            buf &= (1 << bits) - 1;
        }
    }
    Some(out)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Level {
    Info,
    Warning,
}

impl Level {
    fn tag(self) -> &'static str {
        match self {
            Level::Info => "INFO",
            Level::Warning => "WARNING",
        }
    }
}

/// Diagnosticos con memoria por tema.
///
/// El agente consulta cada 5 s, y cada linea de log es un POST al bot. Antes
/// cada consulta repetia las mismas lineas y el agent.log del bot llego a casi
/// 3 MB de "Lockfile loaded". Ahora un tema se registra solo cuando su mensaje
/// cambia; los hechos puntuales (empezo una partida) van por `event`.
#[derive(Debug, Default)]
pub struct Diagnostics {
    last: HashMap<String, String>,
    pending: Vec<String>,
}

impl Diagnostics {
    pub fn new() -> Self {
        Self::default()
    }

    /// Estado actual de un tema. Se registra solo si cambio desde la vez anterior.
    pub fn status(&mut self, scope: &str, topic: &str, level: Level, msg: impl Into<String>) {
        let msg = msg.into();
        let key = format!("{scope}.{topic}");
        if self.last.get(&key) == Some(&msg) {
            return;
        }
        self.last.insert(key, msg.clone());
        self.push(scope, level, &msg);
    }

    /// Un hecho puntual: se registra siempre.
    pub fn event(&mut self, scope: &str, level: Level, msg: impl Into<String>) {
        self.push(scope, level, &msg.into());
    }

    /// Olvida los temas de un ambito, asi la proxima sesion del juego vuelve
    /// a registrar su estado desde el principio.
    pub fn forget(&mut self, scope: &str) {
        let prefix = format!("{scope}.");
        self.last.retain(|k, _| !k.starts_with(&prefix));
    }

    pub fn take(&mut self) -> Vec<String> {
        std::mem::take(&mut self.pending)
    }

    fn push(&mut self, scope: &str, level: Level, msg: &str) {
        self.pending.push(format!("[{}] [{scope}] {msg}", level.tag()));
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_lockfile_when_valid_returns_port_and_password() {
        let l = parse_lockfile("Riot Client:1234:58028:s3cr3t:https\n").unwrap();
        assert_eq!(l, Lockfile { port: 58028, password: "s3cr3t".into() });
    }

    #[test]
    fn parse_lockfile_when_too_few_fields_returns_err() {
        assert!(parse_lockfile("Riot Client:1234:58028").is_err());
    }

    #[test]
    fn parse_lockfile_when_port_is_not_a_number_returns_err() {
        assert!(parse_lockfile("LeagueClient:1:abc:pw:https").is_err());
    }

    #[test]
    fn lockfile_auth_is_basic_with_riot_user() {
        let l = Lockfile { port: 1, password: "pw".into() };
        // base64("riot:pw")
        assert_eq!(l.auth(), "Basic cmlvdDpwdw==");
    }

    #[test]
    fn parse_curl_output_when_404_returns_not_found() {
        let out = "{\"errorCode\":\"RESOURCE_NOT_FOUND\"}\n__HTTP_STATUS__:404";
        assert_eq!(parse_curl_output(out), Fetch::NotFound);
    }

    #[test]
    fn parse_curl_output_when_200_json_returns_value() {
        let out = "\"InProgress\"\n__HTTP_STATUS__:200";
        assert_eq!(parse_curl_output(out), Fetch::Ok(Value::String("InProgress".into())));
    }

    #[test]
    fn parse_curl_output_when_401_returns_failed_not_not_found() {
        let out = "unauthorized\n__HTTP_STATUS__:401";
        assert!(matches!(parse_curl_output(out), Fetch::Failed(m) if m.contains("401")));
    }

    #[test]
    fn parse_curl_output_when_no_marker_returns_failed() {
        assert!(matches!(parse_curl_output("{}"), Fetch::Failed(_)));
    }

    #[test]
    fn parse_curl_output_when_body_is_not_json_returns_failed() {
        assert!(matches!(parse_curl_output("<html>\n__HTTP_STATUS__:200"), Fetch::Failed(_)));
    }

    #[test]
    fn base64_round_trip_preserves_bytes() {
        for s in ["", "a", "ab", "abc", "riot:pw", "{\"sessionLoopState\":\"INGAME\"}"] {
            let encoded = base64_encode(s.as_bytes());
            assert_eq!(base64_decode(&encoded).unwrap(), s.as_bytes(), "input {s:?}");
        }
    }

    #[test]
    fn base64_decode_accepts_url_safe_and_missing_padding() {
        // "riot:pw" sin '=' de relleno
        assert_eq!(base64_decode("cmlvdDpwdw").unwrap(), b"riot:pw");
        // ">>>" -> "Pj4+" estandar, "Pj4-" url-safe
        assert_eq!(base64_decode("Pj4-").unwrap(), b">>>");
    }

    #[test]
    fn base64_decode_when_invalid_char_returns_none() {
        assert!(base64_decode("no*valido").is_none());
    }

    #[test]
    fn mask_keeps_only_the_ends() {
        assert_eq!(mask("65a61def-830c-5314-8981-c233c58c7363"), "65a61def...7363");
        assert_eq!(mask("corto"), "corto");
    }

    #[test]
    fn diagnostics_status_only_logs_when_message_changes() {
        let mut d = Diagnostics::new();
        d.status("valorant", "lockfile", Level::Info, "port 1");
        d.status("valorant", "lockfile", Level::Info, "port 1");
        d.status("valorant", "lockfile", Level::Info, "port 2");
        assert_eq!(d.take(), vec![
            "[INFO] [valorant] port 1".to_string(),
            "[INFO] [valorant] port 2".to_string(),
        ]);
    }

    #[test]
    fn diagnostics_event_always_logs() {
        let mut d = Diagnostics::new();
        d.event("lol", Level::Info, "game started");
        d.event("lol", Level::Info, "game started");
        assert_eq!(d.take().len(), 2);
    }

    #[test]
    fn diagnostics_forget_lets_the_same_status_log_again() {
        let mut d = Diagnostics::new();
        d.status("lol", "phase", Level::Info, "Lobby");
        d.forget("lol");
        d.status("lol", "phase", Level::Info, "Lobby");
        assert_eq!(d.take().len(), 2);
    }

    #[test]
    fn diagnostics_take_empties_the_queue() {
        let mut d = Diagnostics::new();
        d.event("lol", Level::Warning, "x");
        assert_eq!(d.take(), vec!["[WARNING] [lol] x".to_string()]);
        assert!(d.take().is_empty());
    }
}
