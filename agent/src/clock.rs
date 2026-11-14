//! Date and time without external dependencies.
//!
//! kernel32 is used directly instead of pulling in `chrono`: that dependency
//! chain ends in `windows-core`, which on the GNU target links through
//! raw-dylib and needs a full C toolchain (dlltool + as) that rustup does not
//! ship. Two system calls are enough, and the binary stays smaller.

/// Mirror of the Windows API SYSTEMTIME struct.
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
    // Safe: we pass a valid pointer to memory we own, of the exact size
    // the API expects to fill.
    unsafe { GetLocalTime(&mut t) };
    t
}

fn utc() -> SystemTime {
    let mut t = SystemTime::default();
    unsafe { GetSystemTime(&mut t) };
    t
}

/// Readable local time for the log file: `2026-09-09 14:32:07`.
pub fn log_stamp() -> String {
    let t = local();
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}:{:02}",
        t.year, t.month, t.day, t.hour, t.minute, t.second
    )
}

/// UTC in ISO 8601 to send over the network: `2026-09-09T17:32:07Z`.
///
/// UTC on purpose: it does not depend on anyone's PC time zone, and Python
/// parses it with `datetime.fromisoformat` without ambiguity.
pub fn iso_utc() -> String {
    let t = utc();
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        t.year, t.month, t.day, t.hour, t.minute, t.second
    )
}
