"""Logging a consola (con colores ANSI) + archivo rotativo."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import cfg

_FMT      = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_DATEFMT  = "%H:%M:%S"

# ─── Códigos ANSI ────────────────────────────────────────────────────────────
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"

_LEVEL_COLORS: dict[int, str] = {
    logging.DEBUG:    "\033[38;5;244m",   # gris
    logging.INFO:     "\033[38;5;83m",    # verde brillante
    logging.WARNING:  "\033[38;5;220m",   # amarillo
    logging.ERROR:    "\033[38;5;203m",   # rojo
    logging.CRITICAL: "\033[38;5;198m",   # rosa/magenta
}

_NAME_COLOR  = "\033[38;5;111m"   # azul celeste para [nombre]
_TIME_COLOR  = "\033[38;5;240m"   # gris oscuro para la hora
_RESET       = "\033[0m"


class _ColorFormatter(logging.Formatter):
    """Formatter que pinta cada línea según su nivel."""

    def format(self, record: logging.LogRecord) -> str:
        level_color = _LEVEL_COLORS.get(record.levelno, "")
        time_str    = self.formatTime(record, self.datefmt)
        level_str   = f"{record.levelname:<7}"
        name_str    = record.name

        # Formatear el mensaje base (incluye exc_info si hay)
        record.message = record.getMessage()
        msg = record.message
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            msg = f"{msg}\n{record.exc_text}"
        if record.stack_info:
            msg = f"{msg}\n{self.formatStack(record.stack_info)}"

        return (
            f"{_TIME_COLOR}{time_str}{_RESET} "
            f"{_BOLD}{level_color}{level_str}{_RESET} "
            f"{_NAME_COLOR}[{name_str}]{_RESET} "
            f"{level_color}{msg}{_RESET}"
        )


def setup(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)

    # Consola — con colores (solo si el terminal los soporta)
    console = logging.StreamHandler(sys.stdout)
    use_colors = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    if use_colors:
        console.setFormatter(_ColorFormatter(_FMT, _DATEFMT))
    else:
        console.setFormatter(logging.Formatter(_FMT, _DATEFMT))
    root.addHandler(console)

    # Archivo — sin colores ANSI, con fecha completa
    fileh = RotatingFileHandler(
        cfg.log_dir / "bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    fileh.setFormatter(logging.Formatter(_FMT, "%Y-%m-%d %H:%M:%S"))
    root.addHandler(fileh)

    # discord.py y aiohttp son muy ruidosos en INFO
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.client").setLevel(logging.WARNING)
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
