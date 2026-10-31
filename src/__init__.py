"""Twitch moderation bot.

The Windows console starts in cp1252 and mangles accented characters in
messages (they show up as 'fall?'). UTF-8 is forced as soon as the package
is imported, before any entry point prints anything.
"""
from __future__ import annotations

import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        # stdout redirected to something that is not a TextIOWrapper: that is fine.
        pass
