//! Deteccion de partida activa de Valorant via API local del cliente.
//!
//! El cliente de Valorant escribe un lockfile cuando esta abierto. Ese
//! lockfile contiene el puerto y la contrasena para conectarse a la API
//! HTTPS local en https://127.0.0.1:{port}.
//!
//! Para evitar dependencias de TLS con el toolchain windows-gnu, las llamadas
//! HTTPS se hacen via PowerShell (Invoke-RestMethod), que ya tiene TLS
//! integrado en Windows y acepta certificados auto-firmados con -SkipCertificateCheck.

use std::path::PathBuf;
use std::process::Command;
use std::sync::Mutex;

use serde_json::Value;

/// Cache del PUUID: no cambia entre sesiones de la misma cuenta.
static CACHED_PUUID: Mutex<Option<String>> = Mutex::new(None);

fn lockfile_path() -> PathBuf {
    let local = std::env::var("LOCALAPPDATA").unwrap_or_else(|_| {
        let profile = std::env::var("USERPROFILE").unwrap_or_else(|_| "C:\\Users\\Default".into());
        format!("{profile}\\AppData\\Local")
    });
    PathBuf::from(local)
        .join("Riot Games")
        .join("Riot Client")
        .join("Config")
        .join("lockfile")
}

/// Devuelve (puerto, contrasena) del lockfile, o None si Valorant no esta abierto.
fn read_lockfile() -> Option<(u16, String)> {
    let content = std::fs::read_to_string(lockfile_path()).ok()?;
    // formato: name:pid:port:password:protocol
    let parts: Vec<&str> = content.trim().splitn(6, ':').collect();
    if parts.len() < 5 {
        return None;
    }
    let port: u16 = parts[2].parse().ok()?;
    let password = parts[3].to_string();
    Some((port, password))
}

/// Base64 sin dependencias externas.
fn base64_encode(input: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity((input.len() + 2) / 3 * 4);
    for chunk in input.chunks(3) {
        let b0 = chunk[0] as usize;
        let b1 = chunk.get(1).copied().unwrap_or(0) as usize;
        let b2 = chunk.get(2).copied().unwrap_or(0) as usize;
        out.push(TABLE[b0 >> 2] as char);
        out.push(TABLE[((b0 & 3) << 4) | (b1 >> 4)] as char);
        out.push(if chunk.len() > 1 { TABLE[((b1 & 0xf) << 2) | (b2 >> 6)] as char } else { '=' });
        out.push(if chunk.len() > 2 { TABLE[b2 & 0x3f] as char } else { '=' });
    }
    out
}

/// Hace un GET HTTPS via PowerShell, aceptando certificados auto-firmados.
/// Devuelve el cuerpo JSON parseado, o None si fallo.
fn powershell_get(url: &str, auth_header: &str) -> Option<Value> {
    // Usamos powershell.exe -NoProfile -NonInteractive para que sea rapido.
    // OutputEncoding UTF8 evita problemas con caracteres no ASCII.
    let script = format!(
        r#"
$ErrorActionPreference = 'Stop'
try {{
    $r = Invoke-RestMethod -Uri '{url}' -Headers @{{Authorization='{auth}'}} -SkipCertificateCheck -TimeoutSec 5 -Method Get
    $r | ConvertTo-Json -Depth 5 -Compress
}} catch {{
    if ($_.Exception.Response.StatusCode.value__ -eq 404) {{ '' }} else {{ exit 1 }}
}}
"#,
        url = url,
        auth = auth_header,
    );

    let output = Command::new("powershell.exe")
        .args(["-NoProfile", "-NonInteractive", "-Command", &script])
        .output()
        .ok()?;

    if !output.status.success() {
        return None;
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    let trimmed = stdout.trim();
    if trimmed.is_empty() {
        return None;
    }
    serde_json::from_str(trimmed).ok()
}

fn fetch_puuid(port: u16, auth: &str) -> Option<String> {
    let url = format!("https://127.0.0.1:{port}/chat/v1/session");
    let body = powershell_get(&url, auth)?;
    body.get("subject")?.as_str().map(|s| s.to_string())
}

fn fetch_core_match_id(port: u16, auth: &str, puuid: &str) -> Option<String> {
    let url = format!("https://127.0.0.1:{port}/core-game/v1/player/{puuid}");
    let body = powershell_get(&url, auth)?;
    let id = body.get("matchID")?.as_str()?;
    if id.is_empty() { None } else { Some(id.to_string()) }
}

/// Devuelve el matchID de la partida activa de Valorant, o None si:
///  - El cliente no esta abierto (sin lockfile).
///  - El jugador esta en menu / seleccion de agentes / no en partida.
///  - No se pudo conectar a la API local.
pub fn active_match_id() -> Option<String> {
    let (port, password) = read_lockfile()?;
    let auth = format!("Basic {}", base64_encode(format!("riot:{password}").as_bytes()));

    // PUUID cacheado.
    let puuid = {
        CACHED_PUUID.lock().unwrap_or_else(|e| e.into_inner()).clone()
    };
    let puuid = match puuid {
        Some(p) => p,
        None => {
            let p = fetch_puuid(port, &auth)?;
            if let Ok(mut guard) = CACHED_PUUID.lock() {
                *guard = Some(p.clone());
            }
            p
        }
    };

    fetch_core_match_id(port, &auth, &puuid)
}
