//! Discord alert when the bot cannot be reached while she is playing.
//!
//! The bot cannot warn anyone when it is down, but the agent notices: every
//! send fails. After `alert_after_minutes` of failures with a game running,
//! one message goes to a Discord webhook; another one when the bot is back.

use std::time::{Duration, Instant};

#[derive(Debug, PartialEq)]
pub enum Alert {
    /// The bot has been unreachable for `minutes` while `game` is running.
    Down { minutes: u64, game: String },
    /// The bot answers again after an outage that was alerted.
    Recovered { minutes: u64 },
}

pub struct Alerter {
    after: Duration,
    /// When the current run of failed sends started.
    failing_since: Option<Instant>,
    /// Whether this outage was already alerted (one message per outage).
    alerted: bool,
}

impl Alerter {
    pub fn new(after: Duration) -> Self {
        Self { after, failing_since: None, alerted: false }
    }

    /// Called after every send attempt. Returns the alert to post, if any.
    ///
    /// The outage counts from the first failed send even with no game
    /// running, so a game started during a long outage alerts right away.
    pub fn observe(&mut self, ok: bool, game: Option<&str>, now: Instant) -> Option<Alert> {
        if ok {
            let since = self.failing_since.take();
            if std::mem::take(&mut self.alerted) {
                let down = since.map_or(Duration::ZERO, |since| now.duration_since(since));
                return Some(Alert::Recovered { minutes: minutes(down) });
            }
            return None;
        }
        let since = *self.failing_since.get_or_insert(now);
        if self.alerted {
            return None;
        }
        let game = game?;
        let down = now.duration_since(since);
        if down < self.after {
            return None;
        }
        self.alerted = true;
        Some(Alert::Down { minutes: minutes(down), game: game.to_string() })
    }
}

fn minutes(duration: Duration) -> u64 {
    duration.as_secs() / 60
}

/// Text of the Discord message.
pub fn message(alert: &Alert, address: &str) -> String {
    match alert {
        Alert::Down { minutes, game } => format!(
            "⚠️ The bot at {address} has been unreachable for {minutes} min while {game} is running \
             on the streamer's PC. The category and predictions are not updating."
        ),
        Alert::Recovered { minutes } => {
            format!("✅ The bot at {address} is back after {minutes} min down.")
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const AFTER: Duration = Duration::from_secs(180);

    fn at(start: Instant, seconds: u64) -> Instant {
        start + Duration::from_secs(seconds)
    }

    #[test]
    fn observe_when_failing_shorter_than_threshold_does_not_alert() {
        let start = Instant::now();
        let mut alerter = Alerter::new(AFTER);
        assert_eq!(alerter.observe(false, Some("VALORANT"), start), None);
        assert_eq!(alerter.observe(false, Some("VALORANT"), at(start, 120)), None);
    }

    #[test]
    fn observe_when_failing_past_threshold_with_a_game_alerts_once() {
        let start = Instant::now();
        let mut alerter = Alerter::new(AFTER);
        alerter.observe(false, Some("VALORANT"), start);

        assert_eq!(
            alerter.observe(false, Some("VALORANT"), at(start, 185)),
            Some(Alert::Down { minutes: 3, game: "VALORANT".to_string() })
        );
        assert_eq!(alerter.observe(false, Some("VALORANT"), at(start, 600)), None);
    }

    #[test]
    fn observe_when_no_game_is_running_does_not_alert() {
        // Her PC on but not playing: the bot being down does not matter yet.
        let start = Instant::now();
        let mut alerter = Alerter::new(AFTER);
        alerter.observe(false, None, start);
        assert_eq!(alerter.observe(false, None, at(start, 3600)), None);
    }

    #[test]
    fn observe_when_a_game_starts_during_a_long_outage_alerts_right_away() {
        let start = Instant::now();
        let mut alerter = Alerter::new(AFTER);
        alerter.observe(false, None, start);
        assert_eq!(
            alerter.observe(false, Some("League of Legends"), at(start, 1800)),
            Some(Alert::Down { minutes: 30, game: "League of Legends".to_string() })
        );
    }

    #[test]
    fn observe_when_bot_is_back_after_an_alert_reports_recovery() {
        let start = Instant::now();
        let mut alerter = Alerter::new(AFTER);
        alerter.observe(false, Some("VALORANT"), start);
        alerter.observe(false, Some("VALORANT"), at(start, 200));

        assert_eq!(
            alerter.observe(true, Some("VALORANT"), at(start, 720)),
            Some(Alert::Recovered { minutes: 12 })
        );
        // A new outage starts counting from zero.
        assert_eq!(alerter.observe(false, Some("VALORANT"), at(start, 730)), None);
    }

    #[test]
    fn observe_when_bot_is_back_without_an_alert_stays_quiet() {
        let start = Instant::now();
        let mut alerter = Alerter::new(AFTER);
        alerter.observe(false, Some("VALORANT"), start);
        assert_eq!(alerter.observe(true, Some("VALORANT"), at(start, 60)), None);
    }

    #[test]
    fn message_when_down_names_the_game_the_address_and_the_time() {
        let text = message(&Alert::Down { minutes: 5, game: "VALORANT".to_string() }, "192.168.0.188:8787");
        assert!(text.contains("192.168.0.188:8787"), "{text}");
        assert!(text.contains("VALORANT"), "{text}");
        assert!(text.contains("5 min"), "{text}");
    }

    #[test]
    fn message_when_recovered_says_how_long_it_was_down() {
        let text = message(&Alert::Recovered { minutes: 12 }, "192.168.0.188:8787");
        assert!(text.to_lowercase().contains("back"), "{text}");
        assert!(text.contains("12 min"), "{text}");
    }
}
