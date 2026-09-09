"""Logging a consola + archivo rotativo."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import cfg

_FMT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_DATEFMT = "%H:%M:%S"


def setup(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(_FMT, _DATEFMT))
    root.addHandler(console)

    fileh = RotatingFileHandler(
        cfg.log_dir / "bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    # En el archivo si queremos la fecha completa, no solo la hora.
    fileh.setFormatter(logging.Formatter(_FMT, "%Y-%m-%d %H:%M:%S"))
    root.addHandler(fileh)

    # discord.py y aiohttp son muy ruidosos en INFO
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.client").setLevel(logging.WARNING)
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
