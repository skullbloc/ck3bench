"""The game's calendar: 365 days, no leap years, dates spelled Y.M.D as the
bridge spells them (`%04d.%02d.%02d`).

A transcription of the toolkit's `cycle_cadence.ordinal` / `date_from_ordinal`,
so the environment needs no toolkit internals for dates; `test_mirrors.py`
checks the mirror against the toolkit's own functions where its source is present.
"""
from __future__ import annotations

from typing import Any, Optional

MONTH_CUM_DAYS = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)
MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
DAYS_PER_YEAR = 365


def ordinal(date: Any) -> Optional[int]:
    """Game-days since year 0, or None when `date` is not a valid Y.M.D."""
    if not isinstance(date, str):
        return None
    parts = date.strip().split(".")
    if len(parts) != 3:
        return None
    try:
        y, m, d = (int(p) for p in parts)
    except ValueError:
        return None
    if y < 0 or not 1 <= m <= 12 or not 1 <= d <= MONTH_DAYS[m - 1]:
        return None
    return y * DAYS_PER_YEAR + MONTH_CUM_DAYS[m - 1] + (d - 1)


def date_from_ordinal(o: int) -> str:
    y, rem = divmod(int(o), DAYS_PER_YEAR)
    m = max(i for i in range(12) if MONTH_CUM_DAYS[i] <= rem)
    return f"{y:04d}.{m + 1:02d}.{rem - MONTH_CUM_DAYS[m] + 1:02d}"


def add_days(date: str, days: int) -> str:
    o = ordinal(date)
    if o is None:
        raise ValueError(f"not a game date: {date!r}")
    return date_from_ordinal(o + int(days))


def days_between(a: str, b: str) -> int:
    oa, ob = ordinal(a), ordinal(b)
    if oa is None or ob is None:
        raise ValueError(f"not game dates: {a!r}, {b!r}")
    return ob - oa
