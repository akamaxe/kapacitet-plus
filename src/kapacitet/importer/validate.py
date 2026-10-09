"""Data-quality report on an operator's network export.

This is the first thing a DSO gets from us, before any capacity number: a list of what
their low-voltage records can and cannot support, with a count on every line. It runs on
the four CSV files of `contract.py` and needs nothing else.

Severities. An `error` stops the import, because the engine would otherwise be answering a
question about a network that does not exist. A `warning` means the import proceeds on a
documented fallback and the result carries that caveat. A `note` records something worth
knowing that costs nothing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .contract import BY_NAME, TABLES

ERROR, WARNING, NOTE = "error", "warning", "note"
LV_MAX_SECTION_M = 2000.0
PLAUSIBLE_FUSES = {16, 20, 25, 35, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400}


@dataclass
class Finding:
    severity: str
    check: str
    message: str
    count: int = 0


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    tables: dict[str, int] = field(default_factory=dict)

    def add(self, severity: str, check: str, message: str, count: int = 0) -> None:
        self.findings.append(Finding(severity, check, message, count))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == WARNING]

    @property
    def usable(self) -> bool:
        return not self.errors

    @property
    def score(self) -> int:
        """A blunt 0-100 completeness figure, so a DSO can see progress between exports."""
        if self.errors:
            return 0
        return max(0, 100 - 10 * len(self.warnings))

    def to_dict(self) -> dict:
        """Serialisable form, for the API and for storing next to a result."""
        return {
            "usable": self.usable,
            "score": self.score,
            "tables": self.tables,
            "findings": [f.__dict__ for f in self.findings],
            "text": self.text(),
        }

    def text(self) -> str:
        head = [f"{name}: {n} rows" for name, n in self.tables.items()]
        lines = ["Network export check", "=" * 20, "  ".join(head), ""]
        if not self.findings:
            lines.append("Nothing to report. The export is complete.")
        for f in sorted(self.findings, key=lambda f: (f.severity != ERROR, f.severity != WARNING)):
            tail = f" ({f.count})" if f.count else ""
            lines.append(f"[{f.severity:<7}] {f.check}: {f.message}{tail}")
        lines += ["", f"usable: {'yes' if self.usable else 'no'}    completeness: {self.score}/100"]
        return "\n".join(lines)


def read_export(directory: str | Path) -> dict[str, pd.DataFrame]:
    """Read whichever contract files are present. Missing ones come back absent."""
    directory = Path(directory)
    out = {}
    for table in TABLES:
        path = directory / f"{table.name}.csv"
        if path.exists():
            out[table.name] = pd.read_csv(path)
    return out


def validate(data: dict[str, pd.DataFrame]) -> Report:
    report = Report(tables={k: len(v) for k, v in data.items()})
    if not _check_tables_and_columns(data, report):
        return report
    _check_identifiers(data, report)
    _check_topology(data, report)
    _check_electrical_data(data, report)
    _check_customers(data, report)
    return report


def _check_tables_and_columns(data: dict[str, pd.DataFrame], report: Report) -> bool:
    ok = True
    for table in TABLES:
        if table.name not in data:
            report.add(ERROR, "missing file", f"{table.name}.csv is not in the export")
            ok = False
            continue
        missing = [c for c in table.required_columns if c not in data[table.name].columns]
        if missing:
            report.add(ERROR, "missing column",
                       f"{table.name}.csv has no {', '.join(missing)}")
            ok = False
    return ok


def _check_identifiers(data: dict[str, pd.DataFrame], report: Report) -> None:
    for table in TABLES:
        df = data[table.name]
        key = table.key
        if df[key].isna().any():
            report.add(ERROR, "empty identifier", f"{table.name}.csv has rows without {key}",
                       int(df[key].isna().sum()))
        duplicated = int(df[key].duplicated().sum())
        if duplicated:
            report.add(ERROR, "duplicate identifier",
                       f"{table.name}.csv repeats {key}", duplicated)
    if len(data["transformer"]) != 1:
        report.add(ERROR, "transformer count",
                   "exactly one transformer is supported, the export has",
                   len(data["transformer"]))


def _check_topology(data: dict[str, pd.DataFrame], report: Report) -> None:
    buses, lines, trafo = data["buses"], data["lines"], data["transformer"]
    known = set(buses["bus_id"].astype(str))
    for col in ("from_bus", "to_bus"):
        unknown = set(lines[col].astype(str)) - known
        if unknown:
            report.add(ERROR, "dangling cable",
                       f"lines.csv refers to {col} values that are not in buses.csv",
                       len(unknown))
    if len(trafo) == 1 and str(trafo["lv_bus"].iloc[0]) not in known:
        report.add(ERROR, "transformer bus", "the transformer's lv_bus is not in buses.csv")

    # Radial and connected, checked without pulling in a graph library.
    adjacency: dict[str, list[str]] = {b: [] for b in known}
    for a, b in zip(lines["from_bus"].astype(str), lines["to_bus"].astype(str)):
        if a in adjacency and b in adjacency:
            adjacency[a].append(b)
            adjacency[b].append(a)
    root = str(trafo["lv_bus"].iloc[0]) if len(trafo) == 1 else next(iter(known), None)
    if root is None:
        return
    seen, stack, loops = {root}, [(root, None)], 0
    while stack:
        node, came_from = stack.pop()
        for nxt in adjacency.get(node, []):
            if nxt == came_from:
                continue
            if nxt in seen:
                loops += 1
                continue
            seen.add(nxt)
            stack.append((nxt, node))
    unreached = len(known) - len(seen)
    if unreached:
        report.add(ERROR, "island",
                   "buses cannot be reached from the transformer", unreached)
    if loops:
        report.add(WARNING, "meshed network",
                   "the export is not radial; the engine assumes a radial feeder", loops // 2)


def _check_electrical_data(data: dict[str, pd.DataFrame], report: Report) -> None:
    lines = data["lines"]
    electrical = ["r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km", "max_i_ka"]
    has_type = lines["std_type"].notna() if "std_type" in lines else pd.Series(False, index=lines.index)
    has_values = pd.Series(True, index=lines.index)
    for col in electrical:
        has_values &= lines[col].notna() if col in lines else pd.Series(False, index=lines.index)
    undefined = int((~has_type & ~has_values).sum())
    if undefined:
        report.add(WARNING, "cable type",
                   "sections with neither a type name nor full r/x/c data, the site default "
                   "cable will be assumed", undefined)

    bad_length = int(((lines["length_m"] <= 0) | lines["length_m"].isna()).sum())
    if bad_length:
        report.add(ERROR, "cable length", "sections with a missing or non-positive length",
                   bad_length)
    long_sections = int((lines["length_m"] > LV_MAX_SECTION_M).sum())
    if long_sections:
        report.add(WARNING, "cable length",
                   f"sections longer than {LV_MAX_SECTION_M:.0f} m, which is unusual at low "
                   "voltage and often means metres and feet are mixed", long_sections)

    trafo = data["transformer"]
    if len(trafo) == 1:
        row = trafo.iloc[0]
        if not (0.0 < float(row["sn_mva"]) < 5.0):
            report.add(ERROR, "transformer rating",
                       f"sn_mva is {row['sn_mva']}, which is not a distribution transformer")
        if "vector_group" not in trafo or pd.isna(row.get("vector_group")):
            report.add(NOTE, "vector group", "not given, Dyn assumed")


def _check_customers(data: dict[str, pd.DataFrame], report: Report) -> None:
    cust, buses = data["customers"], data["buses"]
    known = set(buses["bus_id"].astype(str))
    orphan = int((~cust["bus"].astype(str).isin(known)).sum())
    if orphan:
        report.add(ERROR, "orphan customer",
                   "customers whose bus is not in buses.csv", orphan)

    odd = cust.loc[~cust["fuse_a"].isin(PLAUSIBLE_FUSES), "fuse_a"]
    if len(odd):
        report.add(WARNING, "fuse rating",
                   f"customers with a fuse that is not a standard size, for example "
                   f"{odd.iloc[0]} A", len(odd))

    if "phase" not in cust.columns:
        report.add(WARNING, "phase not recorded",
                   "no phase column, so every service is taken as three-phase and voltage "
                   "unbalance is understated; on our reference grid that assumption is worth "
                   "up to a third of the hosting capacity", len(cust))
    else:
        blank = int(cust["phase"].isna().sum())
        if blank == len(cust):
            report.add(NOTE, "phase", "every service is three-phase")
        elif blank:
            report.add(NOTE, "phase",
                       "services recorded as three-phase or with no phase given", blank)
        bad = cust.loc[cust["phase"].notna() & ~cust["phase"].astype(str).str.lower()
                       .isin(["a", "b", "c", "l1", "l2", "l3"]), "phase"]
        if len(bad):
            report.add(ERROR, "phase value",
                       f"unrecognised phase labels, for example {bad.iloc[0]!r}", len(bad))

    for col, what in (("footprint_m2", "no rooftop PV potential can be estimated"),
                      ("annual_kwh", "consumption will be estimated from the fuse rating")):
        if col not in cust.columns:
            report.add(WARNING, f"{col} missing", f"no {col} column, {what}", len(cust))
        else:
            n = int(cust[col].isna().sum())
            if n:
                report.add(WARNING, f"{col} missing", f"customers without {col}, {what}", n)

    if "lon" not in buses.columns or buses["lon"].isna().all():
        report.add(WARNING, "no coordinates",
                   "buses.csv has no usable lon/lat, so the map cannot be drawn")


def check_directory(directory: str | Path) -> Report:
    data = read_export(directory)
    if not data:
        report = Report()
        report.add(ERROR, "empty export", f"no contract files found in {directory}")
        return report
    return validate(data)


def _fmt(value: float) -> str:
    return "-" if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)
