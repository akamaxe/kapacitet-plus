"""The CSV files a study writes for people and for programs, and the ones it reads back from them.

Every table is written twice, from the same rows:

    <name>.csv           in the study's [output] csv_dialect: "sv-excel" by default (`;` between
                         fields, a decimal comma, UTF-8 with a byte-order mark, CRLF), which Excel
                         opens as it is on a Swedish computer; or "rfc4180"
    <name>.machine.csv   always RFC 4180 (`,`, a decimal point, UTF-8 without BOM, CRLF), for programs

A blank field is a value that is not there (None), never a zero. The column names are the same
in both. A file a person hands back (corrections.csv) may be in either dialect: `read` takes `;`
or `,` and a decimal comma or point.
"""

from __future__ import annotations

import csv
import io
import math
from pathlib import Path
from typing import Iterable

from ..atomic import write_bytes_atomic

DIALECTS = ("sv-excel", "rfc4180")
MACHINE = ".machine.csv"


def _cell(value, decimal: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        text = repr(round(value, 6))
        text = text[:-2] if text.endswith(".0") else text
        return text.replace(".", decimal)
    return str(value)


def render(rows: Iterable[dict], columns: list[str], dialect: str) -> bytes:
    """The table as bytes in one dialect."""
    if dialect not in DIALECTS:
        raise ValueError(f"csv dialect must be one of {DIALECTS}, not {dialect!r}")
    sep, dec, bom = (";", ",", "﻿") if dialect == "sv-excel" else (",", ".", "")
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=sep, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(columns)
    for r in rows:
        w.writerow([_cell(r.get(c), dec) for c in columns])
    return (bom + buf.getvalue()).encode("utf-8")


def write(folder: Path, name: str, rows: list[dict], columns: list[str], dialect: str) -> list[Path]:
    """<name>.csv in `dialect` and <name>.machine.csv in RFC 4180, written atomically."""
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for path, d in ((folder / f"{name}.csv", dialect), (folder / f"{name}{MACHINE}", "rfc4180")):
        write_bytes_atomic(path, render(rows, columns, d))
        out.append(path)
    return out


def read(path: Path) -> list[dict]:
    """The rows of a CSV in either dialect, keys stripped and lower-cased, values stripped."""
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        first = sample.splitlines()[0] if sample.strip() else ""
        # the header decides: a decimal comma in the rows must not make `,` look like the separator
        sep = ";" if first.count(";") > first.count(",") else ","
        return [{str(k).strip().lower(): (v or "").strip() for k, v in r.items() if k is not None}
                for r in csv.DictReader(fh, delimiter=sep)]


def number(text) -> float | None:
    """A number written with a decimal comma or point (and spaces between thousands); None if blank.
    An unreadable one raises ValueError."""
    if text is None or not str(text).strip():
        return None
    return float(str(text).strip().replace(" ", "").replace(" ", "").replace(",", "."))


__all__ = ["DIALECTS", "MACHINE", "number", "read", "render", "write"]
