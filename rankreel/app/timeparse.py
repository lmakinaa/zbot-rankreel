"""Timestamp parsing: ``ss``, ``ss.ms``, ``mm:ss``, ``mm:ss.ms``, ``hh:mm:ss[.ms]``."""

from __future__ import annotations

import re

_NUM = r"\d+(?:\.\d+)?"
_RE = re.compile(rf"^(?:(\d+):)?(?:(\d+):)?({_NUM})$")


def parse_time(value: str | int | float | None) -> float | None:
    """Parse a timestamp into seconds. ``None`` / empty string -> ``None``.

    Raises ``ValueError`` for anything malformed or negative.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"Invalid timestamp: {value!r}")
    if isinstance(value, (int, float)):
        if value < 0:
            raise ValueError(f"Timestamp can't be negative: {value}")
        return float(value)
    s = str(value).strip().replace(",", ".")
    if s == "":
        return None
    m = _RE.match(s)
    if not m:
        raise ValueError(f"Invalid timestamp: {value!r} (use ss, mm:ss, mm:ss.ms or hh:mm:ss)")
    a, b, sec = m.groups()
    sec_f = float(sec)
    if a is None:  # plain seconds
        return sec_f
    if b is None:  # mm:ss
        minutes, hours = int(a), 0
    else:  # hh:mm:ss
        hours, minutes = int(a), int(b)
        if minutes >= 60:
            raise ValueError(f"Invalid minutes in {value!r}")
    if sec_f >= 60:
        raise ValueError(f"Invalid seconds in {value!r}")
    return hours * 3600 + minutes * 60 + sec_f


def format_time(seconds: float | None) -> str:
    """Seconds -> ``m:ss.s`` (or ``h:mm:ss.s``)."""
    if seconds is None:
        return ""
    seconds = max(0.0, float(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{int(h)}:{int(m):02d}:{s:04.1f}"
    return f"{int(m)}:{s:04.1f}"
