"""Metered consumption: a DSO's quarter-hour readings, read with DuckDB, checked, and summed to hours.

The readings are CSV files (optionally gzipped) in one folder, any number of them, each with a
header row:

    customer_id or meter_id   the metering point: the customer_id of the network's customers.csv
    start                     the quarter-hour's start in ISO 8601 with its UTC offset, for example
                              2025-03-30T03:00:00+02:00 (Z for UTC)
    kwh_import                energy taken from the grid in the quarter-hour, kWh
    kwh_export                optional: energy fed into the grid, kWh
    v_l1, v_l2, v_l3          optional: the phase voltages, V

The separator is sniffed and a decimal comma is read as a point, so the Swedish Excel dialect
(`;`, decimal comma) reads as well as RFC 4180. Everything is read as text first, so a value the
engine cannot read is a finding and never a crash.

`check` returns the findings as a validate.Report (`chk.msg.meter_*`) and leaves the readings it
can use in a table of its own: a time without its UTC offset is refused outright, since it names
two instants on the night the clocks go back; a time with an offset that is not Swedish time at
that instant, in a file that changes offset (daylight saving time written wrong), a time off the
15-minute grid, a negative or unreadable value and conflicting copies of a reading are left out
and counted. `to_hourly` sums the quarter-hours to hours on timeindex.year_index (the hour's start
in UTC, for a calendar year in UTC+1): an hour counts only with all four of its quarter-hours,
so on the days the clocks change every day still has 24 hours. The voltages stay at 15 minutes,
for identifying each meter's phase later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Collection

import numpy as np
import pandas as pd

from ..importer.validate import ERROR, NOTE, WARNING, Report

ID_COLUMNS = ("customer_id", "meter_id")
REQUIRED = ("start", "kwh_import")
VOLTAGES = ("v_l1", "v_l2", "v_l3")
TZ = "Europe/Stockholm"
COMPLETE_SHARE = 0.9          # a meter with fewer of its hours complete is reported
SUFFIXES = (".csv", ".csv.gz")


@dataclass
class Readings:
    """The readings of one or more files, in a DuckDB connection of their own."""

    con: object
    files: list[Path]
    columns: list[str]
    id_column: str | None
    voltages: list[str] = field(default_factory=list)
    has_export: bool = False
    checked: bool = False


def meter_files(folder: str | Path) -> list[Path]:
    """The reading files in `folder` and below it, in name order (hidden ones left out)."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.name.lower().endswith(SUFFIXES)
                  and not any(part.startswith(".") for part in p.relative_to(folder).parts))


def _quote(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def read(files: Collection[str | Path]) -> Readings:
    """Every row of `files` as text, in a table `raw` with the column names in lower case."""
    import duckdb

    files = [Path(f) for f in files]
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    if not files:
        con.execute("CREATE TABLE raw (file VARCHAR)")
        return Readings(con, files, [], None)
    listed = "[" + ", ".join(_quote(f.as_posix()) for f in files) + "]"
    source = (f"read_csv({listed}, all_varchar = true, header = true, union_by_name = true, "
              "filename = true)")
    names = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()]
    picked: dict[str, str] = {}
    for name in names:
        key = name.strip().lower()
        if name != "filename" and key not in picked:
            picked[key] = name
    cols = ", ".join(f'"{orig}" AS "{key}"' for key, orig in picked.items())
    con.execute(f'CREATE TABLE raw AS SELECT row_number() OVER () AS "row", filename AS file, {cols} FROM {source}')
    id_column = next((c for c in ID_COLUMNS if c in picked), None)
    return Readings(con, files, list(picked), id_column, voltages=[v for v in VOLTAGES if v in picked],
                    has_export="kwh_export" in picked)


def _number(column: str) -> str:
    return f"TRY_CAST(replace(trim(\"{column}\"), ',', '.') AS DOUBLE)"


def _blank(column: str) -> str:
    return f"(\"{column}\" IS NULL OR trim(\"{column}\") = '')"


def check(rd: Readings, keep: Collection[str] | None = None, known: Collection[str] | None = None) -> Report:
    """The findings on the readings, and a table `quarters` of the readings that can be used
    (customer_id, t, kwh_import, kwh_export, v_*). `keep`: the customers whose readings are wanted
    (a station's), others are left out without a finding; `known`: every customer a reading may name
    (a study's), defaulting to `keep`, and a meter outside it is an unmapped one."""
    con = rd.con
    report = Report(title="Meter readings check")
    report.tables = {"readings": int(con.execute("SELECT count(*) FROM raw").fetchone()[0])}
    if not rd.files or report.tables["readings"] == 0:
        report.add(ERROR, "no readings", "meter_empty")
        return report
    missing = [c for c in REQUIRED if c not in rd.columns]
    if rd.id_column is None:
        missing.insert(0, " or ".join(ID_COLUMNS))
    if missing:
        report.add(ERROR, "missing column", "meter_missing_column", columns=", ".join(missing))
        return report

    exp = _number("kwh_export") if rd.has_export else "NULL::DOUBLE"
    exp_blank = _blank("kwh_export") if rd.has_export else "true"
    volts = "".join(f', {_number(v)} AS "{v}"' for v in rd.voltages)
    con.execute(f"""
        CREATE OR REPLACE TABLE parsed AS
        WITH s AS (
            SELECT "row", file, trim(CAST("{rd.id_column}" AS VARCHAR)) AS customer_id,
                   regexp_replace(trim(start), '^([0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}[T ][0-9]{{2}}:[0-9]{{2}})([+-]|[Zz]$)',
                                  '\\1:00\\2') AS start_text,
                   {_number("kwh_import")} AS kwh_import, {_blank("kwh_import")} AS imp_blank,
                   {exp} AS kwh_export, {exp_blank} AS exp_blank,
                   kwh_import AS imp_text {volts}
            FROM raw)
        SELECT *, regexp_extract(start_text, '([Zz]|[+-][0-9]{{2}}(:?[0-9]{{2}})?)$') AS off_text,
               TRY_CAST(start_text AS TIMESTAMPTZ) AS t
        FROM s""")
    con.execute(f"""
        CREATE OR REPLACE TABLE parsed AS
        SELECT *,
               CASE WHEN off_text IN ('Z', 'z') THEN 0 WHEN off_text = '' THEN NULL ELSE
                   (CASE WHEN substr(off_text, 1, 1) = '-' THEN -1 ELSE 1 END)
                   * (CAST(substr(off_text, 2, 2) AS INTEGER) * 60
                      + CASE WHEN length(replace(off_text, ':', '')) = 5
                             THEN CAST(substr(replace(off_text, ':', ''), 4, 2) AS INTEGER) ELSE 0 END)
               END AS off_min,
               CAST((epoch(timezone('{TZ}', t)) - epoch(t)) / 60 AS INTEGER) AS sweden_min
        FROM parsed""")

    def example(where: str, what: str = "start_text") -> tuple[int, str]:
        n, ex = con.execute(f"SELECT count(*), min({what}) FROM parsed WHERE {where}").fetchone()
        return int(n), str(ex)

    n, ex = example("customer_id IS NULL OR customer_id = ''", "CAST(\"row\" AS VARCHAR)")
    if n:
        report.add(ERROR, "empty identifier", "meter_no_id", n, row=ex)
    n, ex = example("start_text IS NOT NULL AND start_text <> '' AND off_text = ''")
    if n:
        report.add(ERROR, "naive time", "meter_naive_time", n, ts=ex)
    n, ex = example("start_text IS NULL OR start_text = '' OR (off_text <> '' AND t IS NULL)",
                    "coalesce(start_text, '')")
    if n:
        report.add(ERROR, "unreadable time", "meter_bad_time", n, ts=ex)
    if report.errors:
        return report

    # which rows are used, and why the others are not
    con.execute("""
        CREATE OR REPLACE TABLE parsed AS
        SELECT p.*, f.offsets, m.meter_offsets FROM parsed p
        JOIN (SELECT file, count(DISTINCT off_min) AS offsets FROM parsed GROUP BY file) f USING (file)
        JOIN (SELECT customer_id, count(DISTINCT off_min) AS meter_offsets FROM parsed GROUP BY customer_id) m
             USING (customer_id)""")
    con.execute("CREATE OR REPLACE TABLE flagged AS SELECT *, "
                "(offsets > 1 OR meter_offsets > 1) AND off_min <> sweden_min AS dst, "
                "(epoch_us(t) % 900000000) <> 0 AS off_grid, "
                "(NOT imp_blank AND kwh_import IS NULL) OR (NOT exp_blank AND kwh_export IS NULL) AS unreadable, "
                "imp_blank AS empty, "
                "coalesce(kwh_import < 0, false) OR coalesce(kwh_export < 0, false) AS negative "
                "FROM parsed")
    n, ex = con.execute("SELECT count(*), min(start_text) FROM flagged WHERE dst").fetchone()
    if n:
        report.add(WARNING, "daylight saving time", "meter_dst", int(n), ts=str(ex))
    n, ex = con.execute("SELECT count(*), min(start_text) FROM flagged WHERE off_grid AND NOT dst").fetchone()
    if n:
        report.add(WARNING, "off the 15-minute grid", "meter_off_grid", int(n), ts=str(ex))
    n, ex = con.execute("SELECT count(*), min(coalesce(imp_text, '')) FROM flagged WHERE unreadable OR empty").fetchone()
    if n:
        report.add(WARNING, "unreadable reading", "meter_unreadable", int(n), value=repr(str(ex)))
    n = con.execute("SELECT count(*) FROM flagged WHERE negative").fetchone()[0]
    if n:
        report.add(WARNING, "negative reading", "meter_negative", int(n))

    known_ids = set(known if known is not None else (keep if keep is not None else []))
    con.execute("CREATE OR REPLACE TABLE ids (customer_id VARCHAR)")
    if known_ids:
        con.executemany("INSERT INTO ids VALUES (?)", [(str(i),) for i in sorted(known_ids)])
    if known is not None or keep is not None:
        n, ex = con.execute("SELECT count(DISTINCT customer_id), min(customer_id) FROM flagged "
                            "WHERE customer_id NOT IN (SELECT customer_id FROM ids)").fetchone()
        if n:
            report.add(WARNING, "unmapped meter", "meter_unmapped", int(n), meter=str(ex))
    con.execute("CREATE OR REPLACE TABLE kept (customer_id VARCHAR)")
    if keep is not None:
        con.executemany("INSERT INTO kept VALUES (?)", [(str(i),) for i in sorted(set(map(str, keep)))])
    mine = "customer_id IN (SELECT customer_id FROM kept)" if keep is not None else "true"
    vsel = "".join(f', "{v}"' for v in rd.voltages)
    con.execute(f"""
        CREATE OR REPLACE TABLE good AS
        SELECT customer_id, t, kwh_import, kwh_export {vsel} FROM flagged
        WHERE NOT (dst OR off_grid OR unreadable OR empty OR negative) AND {mine}""")

    # copies of one reading: the same values once, different values not at all
    dup = con.execute("""
        SELECT count(*) FILTER (WHERE variants = 1), coalesce(sum(n - 1) FILTER (WHERE variants = 1), 0),
               count(*) FILTER (WHERE variants > 1), min(customer_id || ' ' || CAST(t AS VARCHAR)) FILTER (WHERE variants > 1)
        FROM (SELECT customer_id, t, count(*) AS n,
                     count(DISTINCT (coalesce(kwh_import, -1), coalesce(kwh_export, -1))) AS variants
              FROM good GROUP BY customer_id, t HAVING count(*) > 1)""").fetchone()
    if dup[1]:
        report.add(NOTE, "duplicate reading", "meter_duplicate_same", int(dup[1]))
    if dup[2]:
        report.add(WARNING, "duplicate reading", "meter_duplicate", int(dup[2]), example=str(dup[3]))
    con.execute(f"""
        CREATE OR REPLACE TABLE quarters AS
        SELECT customer_id, t, any_value(kwh_import) AS kwh_import, any_value(kwh_export) AS kwh_export
               {''.join(f', any_value("{v}") AS "{v}"' for v in rd.voltages)}
        FROM good GROUP BY customer_id, t
        HAVING count(DISTINCT (coalesce(kwh_import, -1), coalesce(kwh_export, -1))) = 1
        ORDER BY customer_id, t""")
    rd.checked = True

    # gaps, and how many hours are complete
    per = con.execute("""
        WITH q AS (SELECT customer_id, min(t) AS first, max(t) AS last, count(*) AS present
                   FROM quarters GROUP BY customer_id),
             h AS (SELECT customer_id, count(*) AS complete FROM
                     (SELECT customer_id, date_trunc('hour', t) AS hr FROM quarters GROUP BY 1, 2 HAVING count(*) = 4)
                   GROUP BY customer_id)
        SELECT q.customer_id, present,
               CAST((epoch(last) - epoch(first)) / 900 AS BIGINT) + 1 AS span,
               coalesce(h.complete, 0) AS complete,
               CAST((epoch(date_trunc('hour', last)) - epoch(date_trunc('hour', first))) / 3600 AS BIGINT) + 1 AS hours
        FROM q LEFT JOIN h USING (customer_id) ORDER BY q.customer_id""").fetchdf()
    report.tables["meters"] = int(len(per))
    gaps = per["span"] - per["present"]
    if int(gaps.sum()):
        report.add(WARNING, "gaps", "meter_gaps", int(gaps.sum()), meters=str(int((gaps > 0).sum())))
    if len(per):
        share = per["complete"] / per["hours"]
        low = per.loc[share < COMPLETE_SHARE, "customer_id"]
        if len(low):
            report.add(WARNING, "completeness", "meter_incomplete", len(low), pct=f"{COMPLETE_SHARE * 100:.0f}",
                       meter=str(low.iloc[0]))
        report.add(NOTE, "completeness", "meter_complete", int(per["complete"].sum()), meters=str(len(per)),
                   pct=f"{100.0 * per['complete'].sum() / per['hours'].sum():.1f}")
    if keep is not None:
        without = len(set(map(str, keep)) - set(per["customer_id"]))
        if without:
            report.add(NOTE, "customers without readings", "meter_no_readings", without)
    return report


def to_hourly(rd: Readings, folder: str | Path | None = None) -> pd.DataFrame:
    """The complete hours of every meter: customer_id, start (UTC, the hour's start), year and hour
    (its position on timeindex.year_index), kwh_import and kwh_export (NaN unless all four quarters
    have one). With `folder`, writes hourly.parquet there, and voltage_15min.parquet with the
    voltages at 15 minutes when the readings have any."""
    from ..timeindex import local_standard, year_index

    if not rd.checked:
        raise ValueError("check the readings before summing them")
    con = rd.con
    df = con.execute("""
        SELECT customer_id, date_trunc('hour', t) AS start, sum(kwh_import) AS kwh_import,
               CASE WHEN count(kwh_export) = 4 THEN sum(kwh_export) END AS kwh_export
        FROM quarters GROUP BY 1, 2 HAVING count(*) = 4 ORDER BY 1, 2""").fetchdf()
    start = pd.DatetimeIndex(pd.to_datetime(df["start"], utc=True)).as_unit("ns")
    df["start"] = start
    # an hour's year is its calendar year in UTC+1, its position the hours since that year's
    # first hour on year_index
    year = local_standard(start).year.to_numpy() if len(df) else np.zeros(0, dtype=int)
    first = {int(y): year_index(int(y))[0].value for y in np.unique(year)}
    df["year"] = year.astype(np.int16)
    df["hour"] = ((start.asi8 - np.array([first[int(y)] for y in year], dtype=np.int64))
                  // 3_600_000_000_000).astype(np.int32)
    df["kwh_export"] = df["kwh_export"].astype(float)
    df = df[["customer_id", "start", "year", "hour", "kwh_import", "kwh_export"]]
    if folder is not None:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        df.to_parquet(folder / "hourly.parquet", index=False)
        if rd.voltages:
            cols = ", ".join(f'"{v}"' for v in rd.voltages)
            target = (folder / "voltage_15min.parquet").as_posix()
            con.execute(f"COPY (SELECT customer_id, t AS start, {cols} FROM quarters ORDER BY customer_id, t) "
                        f"TO {_quote(target)} (FORMAT PARQUET)")
    return df


def ingest(files: Collection[str | Path], folder: str | Path | None = None, keep: Collection[str] | None = None,
           known: Collection[str] | None = None) -> tuple[Report, pd.DataFrame | None]:
    """read, check and, when nothing stops it, to_hourly: (the report, the hourly readings or None)."""
    rd = read(files)
    try:
        report = check(rd, keep=keep, known=known)
        if not report.usable:
            return report, None
        return report, to_hourly(rd, folder)
    finally:
        rd.con.close()
