"""Logging setup."""

from __future__ import annotations

import logging
import sys

_LOG_FMT = "%(asctime)s │ %(levelname)-7s │ %(name)-22s │ %(message)s"
_DATE_FMT = "%H:%M:%S"


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FMT, datefmt=_DATE_FMT))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # mute noisy libs
    for noisy in ("aiogram.event", "aiohttp.access", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
