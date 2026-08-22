"""Compact ANSI terminal output for the Raspberry Pi control server."""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime

GREEN = "\033[38;5;48m"
CYAN = "\033[38;5;51m"
YELLOW = "\033[38;5;220m"
RED = "\033[38;5;203m"
DIM = "\033[38;5;244m"
WHITE = "\033[97m"
RESET = "\033[0m"
BOLD = "\033[1m"
_lock = threading.Lock()
_color = sys.stdout.isatty() and os.getenv("NO_COLOR") is None


def _paint(text: str, color: str) -> str:
    return f"{color}{text}{RESET}" if _color else text


def banner(host: str, port: int) -> None:
    logo = (
        "╔══════════════════════════════════════════════════╗\n"
        "║          ROOMBA // MOBILE CONTROL DECK          ║\n"
        "╠══════════════════════════════════════════════════╣\n"
        f"║  LISTEN  {host}:{port:<34}║\n"
        "║  SAFETY  PASSIVE · 20% BATTERY LOCK · WATCHDOG ║\n"
        "╚══════════════════════════════════════════════════╝"
    )
    with _lock:
        print(_paint(logo, GREEN), flush=True)


def event(channel: str, message: str, level: str = "info") -> None:
    palette = {"info": CYAN, "ok": GREEN, "warn": YELLOW, "danger": RED}
    color = palette.get(level, WHITE)
    stamp = datetime.now().strftime("%H:%M:%S")
    prefix = f"[{channel.upper():<8}]"
    with _lock:
        print(
            f"{_paint(stamp, DIM)}  {_paint(prefix, color)}  "
            f"{_paint(message, BOLD if level in ('warn', 'danger') else WHITE)}",
            flush=True,
        )
