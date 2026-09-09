//! Fecha y hora sin dependencias externas.
//!
//! Se usa kernel32 directamente en vez de traer `chrono`: esa cadena de
//! dependencias termina en `windows-core`, que en el target GNU enlaza por
//! raw-dylib y exige un toolchain de C completo (dlltool + as) que rustup no
//! trae. Con dos llamadas del sistema alcanza y el binario queda mas chico.

/// Espejo de SYSTEMTIME de la API de Windows.
#[repr(C)]
#[derive(Default, Clone, Copy)]
struct SystemTime {
    year: u16,
    month: u16,
    day_of_week: u16,
    day: u16,
    hour: u16,
    minute: u16,
    second: u16,
    milliseconds: u16,
}

#[link(name = "kernel32")]
extern "system" {
    fn GetLocalTime(out: *mut SystemTime);
    fn GetSystemTime(out: *mut SystemTime);
}

fn local() -> SystemTime {
    let mut t = SystemTime::default();
    // Seguro: le pasamos un puntero valido a memoria que es nuestra y del
    // tamano exacto que la API espera rellenar.
    unsafe { GetLocalTime(&mut t) };
    t
}

fn utc() -> SystemTime {
    let mut t = SystemTime::default();
    unsafe { GetSystemTime(&mut t) };
    t
}

/// Hora local, legible, para el archivo de log: `2026-09-09 14:32:07`.
pub fn log_stamp() -> String {
    let t = local();
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}:{:02}",
        t.year, t.month, t.day, t.hour, t.minute, t.second
    )
}

/// UTC en ISO 8601 para mandar por la red: `2026-09-09T17:32:07Z`.
///
/// Va en UTC a proposito: no depende de la zona horaria de la PC de nadie y
/// Python lo parsea con `datetime.fromisoformat` sin ambiguedad.
pub fn iso_utc() -> String {
    let t = utc();
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        t.year, t.month, t.day, t.hour, t.minute, t.second
    )
}
