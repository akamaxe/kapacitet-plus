"""The other shapes a metering system writes its readings in, turned into the long format of
ingest/meters.py before it reads them (task 2.4 of the technical review of 14-11-2026, finding H7).

The long format has one row per meter and interval, its start in ISO 8601 with its UTC offset and
the energy in kWh. Three other shapes are common in the exports of Swedish metering systems:

- **Local time.** The same long rows with the start in Swedish wall-clock time and no offset. A
  file whose every time lacks its offset is read as Europe/Stockholm. On the night the clocks go
  back the hour from 02:00 to 03:00 comes twice: a meter's first reading of such a time is summer
  time, its second winter time, in the order of the file (`meter_repeated_hour`). A time given only
  once there cannot be placed and is left out, as is a time that does not exist (the hour the clocks
  skip in March). A file that mixes times with and without an offset is not local time: its naive
  times stay refused, as before.
- **One row per day.** customer_id (or meter_id) and date, with 24 hourly or 96 quarter-hourly
  columns in time order, and a 25th (a 97th to 100th) for the day the clocks go back; the day they
  go forward has one hour (four quarter-hours) less, and its last column stays blank. An optional
  column `quantity` says import or export (import when left out).
- **Wh.** wh_import and wh_export instead of kwh_import and kwh_export, or a column `unit` of Wh in
  the rows of a day: divided by 1000.

Readings by the hour (the rows of a day with 24 columns, or long rows an hour apart with `minutes`
of 60) keep their hour: `minutes` tells ingest/meters.py that the hour is complete in one reading.
What each conversion did, and what it left out, comes back as findings (`chk.msg.meter_*`).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .meters import ID_COLUMNS, TZ

DATE_COLUMNS = ("date", "datum", "day", "dag")
HOURLY, QUARTERLY = (24, 25), (96, 100)
OFFSET = re.compile(r"(?:[Zz]|[+-]\d{2}(?::?\d{2})?)$")


@dataclass
class Converted:
    """One file as the long format, and what its conversion found: (severity, code, count, params)."""

    frame: pd.DataFrame | None
    shape: str                      # "long", "local", "wide" or "wh"
    findings: list[tuple[str, str, int, dict]] = field(default_factory=list)


def _text(path: Path) -> str:
    raw = path.read_bytes()
    if path.name.lower().endswith(".gz"):
        import gzip

        raw = gzip.decompress(raw)
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def _frame(text: str) -> pd.DataFrame:
    head = text.split("\n", 1)[0]
    sep = ";" if head.count(";") > head.count(",") else ","
    df = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False, quoting=csv.QUOTE_MINIMAL)
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def _number(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.strip().str.replace(",", ".", regex=False), errors="coerce")


def _iso(ts: pd.Series) -> pd.Series:
    """Aware timestamps as ISO 8601 with their offset, as the long format writes them."""
    return ts.map(lambda t: t.isoformat())


def shape_of(df: pd.DataFrame) -> str:
    cols = set(df.columns)
    if "start" in cols:
        times = df["start"].astype(str).str.strip()
        times = times[times != ""]
        if len(times) and not times.str.contains(OFFSET).any():
            return "local"
        if "kwh_import" not in cols and "wh_import" in cols:
            return "wh"
        return "long"
    if cols & set(DATE_COLUMNS) and len(_interval_columns(df)) in HOURLY + QUARTERLY:
        return "wide"
    return "long"                   # whatever it is, ingest/meters.py says what it lacks


def _interval_columns(df: pd.DataFrame) -> list[str]:
    skip = set(ID_COLUMNS) | set(DATE_COLUMNS) | {"quantity", "unit", "meter", "name"}
    return [c for c in df.columns if c not in skip]


def convert(path: str | Path) -> Converted:
    """The file at `path` as long rows (customer_id, start, kwh_import, kwh_export, minutes), or the
    frame None when it already is the long format with offsets and kWh (read as it is)."""
    df = _frame(_text(Path(path)))
    shape = shape_of(df)
    if shape == "long":
        return Converted(None, shape)
    if shape == "wh":
        return _from_wh(df)
    if shape == "local":
        return _from_local(df)
    return _from_wide(df)


def _id(df: pd.DataFrame) -> str:
    return next((c for c in ID_COLUMNS if c in df.columns), ID_COLUMNS[0])


def _from_wh(df: pd.DataFrame) -> Converted:
    out = df.copy()
    for side in ("import", "export"):
        if f"wh_{side}" in out.columns:
            out[f"kwh_{side}"] = (_number(out.pop(f"wh_{side}")) / 1000.0).map(lambda v: "" if pd.isna(v) else repr(float(v)))
    return Converted(out, "wh", [("note", "meter_wh", int(len(out)), {})])


def _from_local(df: pd.DataFrame) -> Converted:
    """Long rows in Swedish wall-clock time: each placed at its instant, the repeated October hour
    in the order the meter gives it."""
    out = df.copy()
    found: list[tuple[str, str, int, dict]] = [("note", "meter_local_time", int(len(out)), {})]
    ident = _id(out)
    local = pd.to_datetime(out["start"].astype(str).str.strip(), errors="coerce")
    valid = local.notna()
    idx = pd.DatetimeIndex(local.where(valid, pd.Timestamp("2000-01-01")))
    every = np.ones(len(idx), dtype=bool)
    nonexistent = pd.Series(idx.tz_localize(TZ, ambiguous=every, nonexistent="NaT").isna(), index=out.index) & valid
    either = pd.Series(idx.tz_localize(TZ, ambiguous="NaT", nonexistent="NaT").isna(), index=out.index) & valid
    ambiguous = either & ~nonexistent
    keyed = out.assign(_t=local).groupby([ident, "_t"], sort=False)
    occurrence = keyed.cumcount()                      # 0: the meter's first reading of that time
    copies = keyed["_t"].transform("size")
    single = ambiguous & (copies == 1)
    repeated = ambiguous & (copies >= 2) & (occurrence >= 1)
    stamp = pd.Series(idx.tz_localize(TZ, ambiguous=(occurrence == 0).to_numpy(), nonexistent="NaT"), index=out.index)
    if repeated.any():
        found.append(("note", "meter_repeated_hour", int(repeated.sum()), {"ts": str(local[repeated].min())}))
    if single.any():
        found.append(("warning", "meter_repeated_once", int(single.sum()), {"ts": str(local[single].min())}))
    if nonexistent.any():
        found.append(("warning", "meter_nonexistent_time", int(nonexistent.sum()),
                      {"ts": str(local[nonexistent].min())}))
    keep = ~(nonexistent | single)                     # an unreadable time stays, for ingest/meters.py to name
    out = out[keep].copy()
    out["start"] = [t.isoformat() if ok else str(s)
                    for t, ok, s in zip(stamp[keep], valid[keep], df.loc[keep, "start"])]
    if "kwh_import" not in out.columns and "wh_import" in out.columns:
        conv = _from_wh(out)
        out, found = conv.frame, found + conv.findings
    return Converted(out, "local", found)


def _day_intervals(day: pd.Timestamp, n: int) -> pd.DatetimeIndex:
    """The starts of a Swedish local day's intervals: hours (n of 24 or 25) or quarter-hours."""
    start = pd.Timestamp(day.date()).tz_localize(TZ)
    end = (pd.Timestamp(day.date()) + pd.Timedelta(days=1)).tz_localize(TZ)
    step = "h" if n in HOURLY else "15min"
    return pd.date_range(start.tz_convert("UTC"), end.tz_convert("UTC"), freq=step, inclusive="left").tz_convert(TZ)


def _from_wide(df: pd.DataFrame) -> Converted:
    """One row per meter and day: each column the next interval of that local day."""
    ident = _id(df)
    date_col = next(c for c in DATE_COLUMNS if c in df.columns)
    cols = _interval_columns(df)
    n = len(cols)
    minutes = 60 if n in HOURLY else 15
    found: list[tuple[str, str, int, dict]] = [("note", "meter_wide", int(len(df)), {"columns": str(n)})]
    rows = []
    misfit: list[str] = []
    wh = 0
    for r in df.itertuples(index=False):
        rec = r._asdict()
        day = pd.to_datetime(str(rec[date_col]).strip(), errors="coerce")
        if pd.isna(day):
            misfit.append(str(rec[date_col]))
            continue
        starts = _day_intervals(day, n)
        values = [str(rec[c]).strip() for c in cols]
        used = len(starts)
        # the columns past the day's length must be blank: a value there means another layout
        if used > n or any(v != "" for v in values[used:]):
            misfit.append(f"{rec[ident]} {day.date()}")
            continue
        side = "export" if str(rec.get("quantity", "")).strip().lower().startswith(("exp", "prod", "ut")) else "import"
        scale = 1000.0 if str(rec.get("unit", "")).strip().lower() == "wh" else 1.0
        wh += scale != 1.0
        for t, v in zip(starts, values[:used]):
            x = _number(pd.Series([v])).iloc[0]
            rows.append({ident: str(rec[ident]).strip(), "start": t.isoformat(), "minutes": str(minutes),
                         f"kwh_{side}": "" if v == "" else (repr(float(x) / scale) if pd.notna(x) else v)})
    if misfit:
        found.append(("error", "meter_wide_misfit", len(misfit), {"example": misfit[0], "columns": str(n)}))
    if wh:
        found.append(("note", "meter_wh", int(wh), {}))
    if minutes == 60:
        found.append(("note", "meter_hourly", int(len(df)), {}))
    out = pd.DataFrame(rows)
    if len(out):
        # an export row and an import row of one interval are one reading
        out = out.groupby([ident, "start", "minutes"], as_index=False, sort=False).agg(
            lambda s: next((v for v in s if isinstance(v, str) and v != ""), ""))
    return Converted(out, "wide", found)


def normalise(files, folder: Path) -> tuple[list[Path], list[tuple[str, str, int, dict]]]:
    """The files ingest/meters.py should read: each one already in the long format as it is, each
    other converted into `folder` as long rows; with every conversion's findings."""
    folder = Path(folder)
    out: list[Path] = []
    found: list[tuple[str, str, int, dict]] = []
    for i, path in enumerate(files):
        path = Path(path)
        conv = convert(path)
        if conv.frame is None:
            out.append(path)
            continue
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{i:04d}-{path.name.split('.')[0]}.csv"
        conv.frame.to_csv(target, index=False)
        out.append(target)
        found.extend(conv.findings)
    return out, found
