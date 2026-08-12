"""Small terminal helpers: colour, units, bars, tables.

Colour is disabled automatically when stdout is not a TTY, when ``NO_COLOR``
is set, or when ``TERM=dumb`` -- so piping ``vein show`` into a file gives
clean text.
"""

from __future__ import annotations

import os
import shutil
import sys

_ENABLED = (
    sys.stdout.isatty()
    and os.environ.get("NO_COLOR") is None
    and os.environ.get("TERM") != "dumb"
)

_CODES = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "grey": "\033[90m",
}


def supports_color() -> bool:
    return _ENABLED


def set_color(enabled: bool) -> None:
    global _ENABLED
    _ENABLED = enabled


def paint(text: str, *styles: str) -> str:
    if not _ENABLED or not styles:
        return text
    prefix = "".join(_CODES.get(s, "") for s in styles)
    return f"{prefix}{text}{_CODES['reset']}"


def width(default: int = 100) -> int:
    try:
        return min(shutil.get_terminal_size().columns, 120)
    except OSError:  # pragma: no cover
        return default


def duration(ns: float) -> str:
    """Human-readable duration from nanoseconds."""
    if ns is None:
        return "-"
    if ns < 1_000:
        return f"{ns:.0f}ns"
    if ns < 1_000_000:
        return f"{ns / 1_000:.1f}µs"
    if ns < 1_000_000_000:
        return f"{ns / 1_000_000:.1f}ms"
    return f"{ns / 1_000_000_000:.2f}s"


def count(n: int) -> str:
    """Compact call counts: 1234 -> 1.2k."""
    if n < 1_000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1_000:.1f}k"
    return f"{n / 1_000_000:.1f}M"


_BLOCKS = " ▏▎▍▌▋▊▉█"


def bar(fraction: float, size: int = 12) -> str:
    """A sub-character-precision horizontal bar."""
    fraction = max(0.0, min(1.0, fraction))
    filled = fraction * size
    whole = int(filled)
    out = "█" * whole
    if whole < size:
        out += _BLOCKS[int((filled - whole) * 8)]
    return out.ljust(size)


def truncate(text: str, limit: int) -> str:
    if limit <= 1 or len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def rule(title: str = "", char: str = "─") -> str:
    total = width()
    if not title:
        return paint(char * total, "grey")
    head = f"{char}{char} {title} "
    return paint(head + char * max(0, total - len(head)), "grey")


def _plain(text: str) -> str:
    """The string with ANSI escapes stripped, for width maths."""
    out, skip = [], False
    for ch in text:
        if ch == "\033":
            skip = True
        elif skip:
            if ch == "m":
                skip = False
        else:
            out.append(ch)
    return "".join(out)


def _pad(text: str, size: int, align: str) -> str:
    pad = size - len(_plain(text))
    if pad <= 0:
        return text
    return " " * pad + text if align == "r" else text + " " * pad


def table(rows: list[list[str]], headers: list[str], aligns: str = "") -> str:
    """Render a plain aligned table. ``aligns`` is one char per column: l/r."""
    if not rows:
        return ""
    cols = len(headers)
    aligns = (aligns + "l" * cols)[:cols]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row[:cols]):
            widths[i] = max(widths[i], len(_plain(cell)))
    out = [
        "  ".join(
            paint(_pad(h, widths[i], aligns[i]), "bold", "grey")
            for i, h in enumerate(headers)
        )
    ]
    for row in rows:
        out.append(
            "  ".join(
                _pad(cell, widths[i], aligns[i]) for i, cell in enumerate(row[:cols])
            )
        )
    return "\n".join(out)
