"""Which desktop agent this bot expects on the streamer's PC.

The agent is updated by hand (copying the .exe to her PC), so nothing told
anyone it was outdated. Each report carries its version; the dashboard warns
when it is older than the one released with this bot.
"""
from __future__ import annotations

#: Version in agent/Cargo.toml when this bot was released (a test keeps them equal).
EXPECTED_AGENT_VERSION = "0.2.0"


def is_outdated(version: str, expected: str = EXPECTED_AGENT_VERSION) -> bool:
    """Older than expected, comparing numbers ("0.10.0" > "0.2.0").

    An unparseable version ("?" from a broken report) is not flagged: a false
    alarm would teach everyone to ignore the warning.
    """
    try:
        return _parse(version) < _parse(expected)
    except ValueError:
        return False


def _parse(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.strip().split("."))
