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
    let path = lockfile_path();
    let content = match std::fs::read_to_string(&path) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[valorant] lockfile no encontrado en {}: {}", path.display(), e);
            return None;
        }
    };
    // formato: name:pid:port:password:protocol
    let parts: Vec<&str> = content.trim().splitn(6, ':').collect();
    if parts.len() < 5 {
        eprintln!("[valorant] lockfile con formato inesperado ({} partes): {:?}", parts.len(), content.trim());
        return None;
    }
    let port: u16 = match parts[2].parse() {
        Ok(p) => p,
        Err(e) => {
            eprintln!("[valorant] no se pudo parsear el puerto del lockfile '{}': {}", parts[2], e);
            return None;
        }
    };
    let password = parts[3].to_string();
    eprintln!("[valorant] lockfile ok -> puerto {}", port);
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

    let output = match Command::new("powershell.exe")
        .args(["-NoProfile", "-NonInteractive", "-Command", &script])
        .output()
    {
        Ok(o) => o,
        Err(e) => {
            eprintln!("[valorant] no se pudo lanzar powershell.exe: {}", e);
            return None;
        }
    };

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        eprintln!("[valorant] powershell fallo para {} | stderr: {}", url, stderr.trim());
        return None;
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    let trimmed = stdout.trim();
    if trimmed.is_empty() {
        eprintln!("[valorant] {} -> 404 (no en partida/pregame en este endpoint)", url);
        return None;
    }
    match serde_json::from_str(trimmed) {
        Ok(v) => Some(v),
        Err(e) => {
            eprintln!("[valorant] respuesta de {} no es JSON valido: {} | raw: {}", url, e, &trimmed[..trimmed.len().min(120)]);
            None
        }
    }
}

fn fetch_puuid(port: u16, auth: &str) -> Option<String> {
    let url = format!("https://127.0.0.1:{port}/chat/v1/session");
    let body = powershell_get(&url, auth)?;
    match body.get("subject").and_then(|v| v.as_str()) {
        Some(s) => {
            eprintln!("[valorant] puuid obtenido: {}...{}", &s[..s.len().min(8)], &s[s.len().saturating_sub(4)..]);
            Some(s.to_string())
        }
        None => {
            eprintln!("[valorant] /chat/v1/session no devolvio 'subject': {:?}", body);
            None
        }
    }
}

fn fetch_core_match_id(port: u16, auth: &str, puuid: &str) -> Option<String> {
    let url = format!("https://127.0.0.1:{port}/core-game/v1/player/{puuid}");
    let body = powershell_get(&url, auth)?;
    let id = body.get("matchID")?.as_str()?;
    if id.is_empty() { None } else { Some(id.to_string()) }
}

/// Fallback para cuando el jugador esta en seleccion de agentes (pregame).
/// El endpoint de core-game devuelve 404 en ese estado; pregame tiene un
/// matchID propio que sirve igualmente como clave unica de partida.
fn fetch_pregame_match_id(port: u16, auth: &str, puuid: &str) -> Option<String> {
    let url = format!("https://127.0.0.1:{port}/pregame/v1/players/{puuid}");
    let body = powershell_get(&url, auth)?;
    let id = body.get("MatchID")?.as_str()?;
    if id.is_empty() { None } else { Some(id.to_string()) }
}

/// Devuelve el matchID de la partida activa de Valorant, o None si:
///  - El cliente no esta abierto (sin lockfile).
///  - El jugador no esta en partida ni en seleccion de agentes.
///  - No se pudo conectar a la API local.
///
/// Orden de consulta:
///   1. core-game  -> partida en curso
///   2. pregame    -> seleccion de agentes (core-game devuelve 404 en este estado)
pub fn active_match_id() -> Option<String> {
    let (port, password) = read_lockfile()?;
    let auth = format!("Basic {}", base64_encode(format!("riot:{password}").as_bytes()));

    // PUUID cacheado. Lo refrescamos si el lockfile cambio de sesion:
    // señal de que la cuenta o el cliente se reinicio.
    let puuid = {
        CACHED_PUUID.lock().unwrap_or_else(|e| e.into_inner()).clone()
    };
    let puuid = match puuid {
        Some(p) => {
            eprintln!("[valorant] usando puuid cacheado");
            p
        }
        None => {
            let p = fetch_puuid(port, &auth)?;
            if let Ok(mut guard) = CACHED_PUUID.lock() {
                *guard = Some(p.clone());
            }
            p
        }
    };

    // 1. Intentar core-game (partida activa).
    if let Some(id) = fetch_core_match_id(port, &auth, &puuid) {
        eprintln!("[valorant] match_id via core-game: {}...{}", &id[..id.len().min(8)], &id[id.len().saturating_sub(4)..]);
        return Some(id);
    }

    // 2. Fallback: seleccion de agentes (pregame).
    match fetch_pregame_match_id(port, &auth, &puuid) {
        Some(id) => {
            eprintln!("[valorant] match_id via pregame: {}...{}", &id[..id.len().min(8)], &id[id.len().saturating_sub(4)..]);
            Some(id)
        }
        None => {
            eprintln!("[valorant] sin match_id: no esta en core-game ni pregame (menu o lobby)");
            None
        }
    }
}
