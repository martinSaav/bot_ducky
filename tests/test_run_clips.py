"""Tests for tools/run_clips.py — the manual run of the clips pipeline.

Naming: [MethodName]_[Scenario/State]_[ExpectedResult]
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_PATH = Path(__file__).resolve().parent.parent / "tools" / "run_clips.py"
_spec = importlib.util.spec_from_file_location("run_clips", _PATH)
run_clips = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_clips)


class TestRunClips:

    async def test_run_WhenListing_BuildsServicesAndListsCandidates(self, tmp_path):
        # Services gained required fields (arbiter, history); the tool must
        # still build it instead of crashing with a TypeError.
        auth = MagicMock()
        auth.info.return_value = {"user_id": "467734820"}
        pipeline = MagicMock()
        pipeline.select_clips = AsyncMock(return_value=[])
        args = argparse.Namespace(hours=None, max=None, list=True)

        with patch.object(run_clips, "logging_setup"), \
             patch.object(run_clips, "TwitchAuth", return_value=auth), \
             patch.object(run_clips, "JsonStore"), \
             patch.object(run_clips, "Helix"), \
             patch.object(run_clips, "DriveUploader"), \
             patch.object(run_clips, "ClipPipeline", return_value=pipeline):
            code = await run_clips.run(args)

        assert code == 0
        pipeline.select_clips.assert_awaited_once()
