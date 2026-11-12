"""A study's run ids: a quarter (`2026Q4`) or a month (`2026-11`).

A DSO publishes every quarter, or every month if it follows the European recommendation; both kinds
may sit side by side in runs/ (a DSO that changes its cadence keeps its earlier runs). The runs are
ordered in time by the start of their period, then by its end, so `previous` finds the run before
another whatever its kind: the month 2026-11 follows the quarter 2026Q3, and 2026Q4 follows the month
2026-10, which starts on the same day and ends sooner. `[publication] cadence` in study.toml only
sets the run id the commands take when none is given (`current`).
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from pathlib import Path

# \Z, not $: a run id names a folder, and `$` would let a final newline through
QUARTER = re.compile(r"^(\d{4})Q([1-4])\Z")
MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])\Z")
EXAMPLE = "a quarter such as 2026Q4 or a month such as 2026-11"
# what a run is called in the Swedish texts, by kind: the word in a sentence and in a table's label
WORD_SV = {"quarter": "kvartal", "month": "månad"}


def kind(run: str) -> str | None:
    """"quarter", "month", or None when `run` is not a run id."""
    if QUARTER.match(run or ""):
        return "quarter"
    if MONTH.match(run or ""):
        return "month"
    return None


def valid(run: str | None) -> bool:
    return kind(run or "") is not None


def period(run: str) -> tuple[dt.date, dt.date]:
    """The first and the last day of the run's period."""
    if m := QUARTER.match(run):
        year, q = int(m.group(1)), int(m.group(2))
        first, last_month = 3 * q - 2, 3 * q
    elif m := MONTH.match(run):
        year, first = int(m.group(1)), int(m.group(2))
        last_month = first
    else:
        raise ValueError(f"{run!r} is not a run id: {EXAMPLE}")
    return dt.date(year, first, 1), dt.date(year, last_month, calendar.monthrange(year, last_month)[1])


def year(run: str) -> int:
    return period(run)[0].year


def key(run: str) -> tuple[dt.date, dt.date]:
    """The order of runs in time: by the start of the period, then by its end."""
    return period(run)


def current(cadence: str = "quarterly", today: dt.date | None = None) -> str:
    """This quarter (2026Q4) or this month (2026-11), as the cadence says."""
    today = today or dt.date.today()
    if cadence == "monthly":
        return f"{today.year}-{today.month:02d}"
    return f"{today.year}Q{(today.month - 1) // 3 + 1}"


def word_sv(run: str) -> str:
    """"kvartal" or "månad"."""
    return WORD_SV[kind(run) or "quarter"]


def label_sv(run: str) -> str:
    """The word with a capital, for a table's label: "Kvartal" or "Månad"."""
    return word_sv(run).capitalize()


def listed(runs_dir: Path, needs: str = "stations.parquet") -> list[str]:
    """The run ids under runs/ that hold `needs` (a file of the run, or a path below it), in time order."""
    if not runs_dir.is_dir():
        return []
    found = [p.name for p in runs_dir.iterdir() if p.is_dir() and valid(p.name) and (p / needs).exists()]
    return sorted(found, key=key)


def previous(runs_dir: Path, run: str, needs: str = "stations.parquet") -> str | None:
    """The latest run before `run` in time that holds `needs`, whatever its kind."""
    before = [r for r in listed(runs_dir, needs) if key(r) < key(run)]
    return before[-1] if before else None


__all__ = ["EXAMPLE", "MONTH", "QUARTER", "WORD_SV", "current", "key", "kind", "label_sv", "listed", "period",
           "previous", "valid", "word_sv", "year"]
