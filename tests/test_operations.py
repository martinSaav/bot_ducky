"""Tests for running the bot reliably: backups, pinned dependencies, the
event-loop watchdog and agent.log rotation.

Naming: [MethodName]_[Scenario/State]_[ExpectedResult]
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ===========================================================================
# Nightly database backup (tools/db_backup.sh, run by the db-backup service)
# ===========================================================================

SH = shutil.which("sh")


@pytest.mark.skipif(SH is None, reason="needs a POSIX sh")
class TestBackupScript:

    def _run(self, tmp_path: Path, keep: int) -> subprocess.CompletedProcess:
        # A fake pg_dump writes a small file where -f says, like the real one.
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "pg_dump"
        fake.write_text(
            '#!/bin/sh\n'
            'while [ $# -gt 0 ]; do\n'
            '  if [ "$1" = "-f" ]; then shift; echo dump > "$1"; fi\n'
            '  shift\n'
            'done\n',
            encoding="utf-8", newline="\n",
        )
        fake.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "BACKUP_DIR": str(tmp_path / "backups"),
            "BACKUP_KEEP": str(keep),
            "POSTGRES_USER": "ducky",
            "POSTGRES_DB": "duckydb",
        }
        return subprocess.run(
            [SH, str(ROOT / "tools" / "db_backup.sh"), "--once"],
            env=env, capture_output=True, text=True, timeout=30,
        )

    def test_backup_WhenRunOnce_WritesTodaysDump(self, tmp_path):
        result = self._run(tmp_path, keep=7)

        assert result.returncode == 0, result.stderr
        dumps = sorted(p.name for p in (tmp_path / "backups").glob("duckydb-*.dump"))
        assert len(dumps) == 1
        assert (tmp_path / "backups" / dumps[0]).read_text().strip() == "dump"

    def test_backup_WhenOverTheLimit_KeepsOnlyTheNewest(self, tmp_path):
        backups = tmp_path / "backups"
        backups.mkdir()
        for day in ["2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04"]:
            (backups / f"duckydb-{day}.dump").write_text("old")

        result = self._run(tmp_path, keep=3)

        assert result.returncode == 0, result.stderr
        names = sorted(p.name for p in backups.glob("duckydb-*.dump"))
        assert len(names) == 3
        assert names[:2] == ["duckydb-2026-11-03.dump", "duckydb-2026-11-04.dump"]


# ===========================================================================
# Reproducible builds
# ===========================================================================

class TestPinnedDependencies:

    @pytest.mark.parametrize("name", ["requirements.txt", "requirements-dev.txt"])
    def test_requirements_EveryPackageHasAnExactVersion(self, name):
        # ">=" installs whatever is newest on each Docker build: a major
        # release of a dependency could break a deploy with no code change.
        loose = []
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
            spec = line.split("#", 1)[0].split(";", 1)[0].strip()
            if spec and not spec.startswith("-") and "==" not in spec:
                loose.append(spec)
        assert loose == []


# ===========================================================================
# Event-loop watchdog
# ===========================================================================

class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class TestLoopWatchdog:
    """If the asyncio loop freezes, nothing inside it can react, and Docker
    only restarts a container that exits. A thread watches a heartbeat the
    loop updates and ends the process, so Docker restarts it."""

    def test_check_WhenHeartbeatIsRecent_DoesNotExit(self):
        from src.watchdog import LoopWatchdog

        clock, exits = FakeClock(), []
        dog = LoopWatchdog(timeout=60, exit=lambda: exits.append(1), clock=clock)
        dog.beat()
        clock.now += 30

        assert dog.check() is False
        assert exits == []

    def test_check_WhenLoopStopsBeatingPastTimeout_ExitsOnce(self):
        from src.watchdog import LoopWatchdog

        clock, exits = FakeClock(), []
        dog = LoopWatchdog(timeout=60, exit=lambda: exits.append(1), clock=clock)
        dog.beat()
        clock.now += 61

        assert dog.check() is True
        dog.check()
        assert exits == [1]

    def test_beat_RestartsTheCountdown(self):
        from src.watchdog import LoopWatchdog

        clock, exits = FakeClock(), []
        dog = LoopWatchdog(timeout=60, exit=lambda: exits.append(1), clock=clock)
        clock.now += 50
        dog.beat()
        clock.now += 50

        assert dog.check() is False


# ===========================================================================
# agent.log rotation
# ===========================================================================

class TestAgentLogRotation:
    """bot.log rotates (5 files x 5 MB); agent.log grew forever (3.6 MB in Nov)."""

    def test_writeAgentLogs_WhenFileReachesTheLimit_RotatesIt(self, tmp_path, monkeypatch):
        from src import agent_server

        monkeypatch.setattr(agent_server.cfg, "log_dir", tmp_path, raising=False)
        monkeypatch.setattr(agent_server, "AGENT_LOG_MAX_BYTES", 200, raising=False)
        line = "2026-11-14 01:00:00 INFO    [agent] " + "x" * 40

        for _ in range(20):
            agent_server.write_agent_logs([line])

        assert (tmp_path / "agent.log.1").exists()
        assert (tmp_path / "agent.log").stat().st_size <= 200 + len(line) + 2

    def test_writeAgentLogs_KeepsAtMostFiveOldFiles(self, tmp_path, monkeypatch):
        from src import agent_server

        monkeypatch.setattr(agent_server.cfg, "log_dir", tmp_path, raising=False)
        monkeypatch.setattr(agent_server, "AGENT_LOG_MAX_BYTES", 100, raising=False)

        for i in range(100):
            agent_server.write_agent_logs([f"line {i} " + "y" * 60])

        rotated = sorted(p.name for p in tmp_path.glob("agent.log.*"))
        assert rotated == [f"agent.log.{n}" for n in range(1, 6)]
