//! Extraccion de datos en vivo de League of Legends via Live Client Data API.
//!
//! Cuando el proceso "League of Legends.exe" esta en ejecucion, el juego
//! levanta una API local en el puerto 2999 sin autenticacion.
//!
//! Documentacion: https://developer.riotgames.com/docs/lol#game-client-api
//!
//! Al igual que en Valorant, usamos PowerShell para hacer la request y evadir
//! la validacion del certificado HTTPS autofirmado de Riot.

use std::process::Command;

use serde_json::Value;

/// Hace un GET HTTPS via PowerShell, aceptando certificados auto-firmados.
/// Devuelve el cuerpo JSON parseado, o None si fallo.
fn powershell_get(url: &str) -> Option<Value> {
    // Usamos powershell.exe -NoProfile -NonInteractive para que sea rapido.
    // OutputEncoding UTF8 evita problemas con caracteres no ASCII.
    let script = format!(
        r#"
$ErrorActionPreference = 'Stop'
try {{
    $r = Invoke-RestMethod -Uri '{url}' -SkipCertificateCheck -TimeoutSec 3 -Method Get
    $r | ConvertTo-Json -Depth 5 -Compress
}} catch {{
    if ($_.Exception.Response.StatusCode.value__ -eq 404) {{ '' }} else {{ exit 1 }}
}}
"#,
        url = url
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

/// Devuelve toda la informacion de la partida activa actual, o None si no se pudo conectar.
pub fn live_data() -> Option<Value> {
    powershell_get("https://127.0.0.1:2999/liveclientdata/allgamedata")
}
