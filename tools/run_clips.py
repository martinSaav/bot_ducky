"""Runs the clips pipeline once, without waiting for the schedule.

    python tools/run_clips.py
    python tools/run_clips.py --hours 72 --max 5
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import logging_setup  # noqa: E402
from src.category import CategoryResolver  # noqa: E402
from src.chat_history import ChatHistory  # noqa: E402
from src.clips.pipeline import ClipPipeline  # noqa: E402
from src.config import cfg  # noqa: E402
from src.drive import DriveUploader  # noqa: E402
from src.gamesource import GameArbiter  # noqa: E402
from src.riot.lol import LolClient  # noqa: E402
from src.riot.valorant import ValorantClient  # noqa: E402
from src.services import Services  # noqa: E402
from src.storage import JsonStore  # noqa: E402
from src.twitch.auth import TwitchAuth  # noqa: E402
from src.twitch.helix import Helix  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    logging_setup.setup()
    if args.hours:
        cfg.clips_lookback_hours = args.hours
    if args.max:
        cfg.clips_max = args.max

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as session:
        tokens = JsonStore(cfg.token_file)
        state = JsonStore(cfg.state_file)
        auth = TwitchAuth(session, tokens)
        helix = Helix(session, auth)

        entry = auth.info("broadcaster")
        broadcaster_id = entry.get("user_id")
        if not broadcaster_id:
            user = await helix.get_user(cfg.twitch_channel)
            if not user:
                print(f"[X] Channel '{cfg.twitch_channel}' does not exist")
                return 1
            broadcaster_id = user["id"]

        resolver = CategoryResolver(helix, state)
        # The pipeline uses neither the arbiter nor the chat history, but
        # Services requires them.
        svc = Services(
            session=session, tokens=tokens, state=state, auth=auth, helix=helix,
            resolver=resolver,
            arbiter=GameArbiter(helix, resolver, str(broadcaster_id), state=state),
            lol=LolClient(session), valorant=ValorantClient(session),
            broadcaster_id=str(broadcaster_id),
            history=ChatHistory(),
        )
        pipeline = ClipPipeline(svc, DriveUploader(session))

        if args.list:
            clips = await pipeline.select_clips()
            if not clips:
                print("No new clips in the requested window.")
                return 0
            print(f"\n{len(clips)} candidate clips:\n")
            for c in clips:
                print(f"  {c.get('view_count'):>5} views  {c.get('created_at')}  {c.get('title')}")
            return 0

        summary = await pipeline.run_once()
        print(f"\nResult: {summary}")
        return 0 if summary.get("failed", 0) == 0 else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual run of the clips pipeline")
    parser.add_argument("--hours", type=int, help="look-back window in hours (default: .env)")
    parser.add_argument("--max", type=int, help="maximum number of clips to upload")
    parser.add_argument("--list", action="store_true",
                        help="only list the candidates, without downloading or uploading")
    try:
        return asyncio.run(run(parser.parse_args()))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
