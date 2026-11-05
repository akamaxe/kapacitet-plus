"""Data-quality report on an operator's network export.

This is the first thing a DSO gets from us, before any capacity number: a list of what
their low-voltage records can and cannot support, with a count on every line. It runs on
the four CSV files of `contract.py` and needs nothing else.

Severities. An `error` stops the import, because the engine would otherwise be answering a
question about a network that does not exist. A `warning` means the import proceeds on a
documented fallback and the result carries that caveat. A `note` records something worth
knowing that costs nothing.

Every finding carries a `code` and its `params` next to the English `message`, so the interface
can say it in any of its languages (`chk.msg.<code>` in web/src/lib/i18n); `MESSAGES` is the one
place the English wording lives, and tests/test_messages.py holds both languages to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .contract import BY_NAME, TABLES

ERROR, WARNING, NOTE = "error", "warning", "note"
LV_MAX_SECTION_M = 2000.0
PLAUSIBLE_FUSES = {16, 20, 25, 35, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400}


MESSAGES = {
    "missing_file": "{table}.csv is not in the export",
    "missing_column": "{table}.csv has no {columns}",
    "empty_identifier": "{table}.csv has rows without {key}",
    "duplicate_identifier": "{table}.csv repeats {key}",
    "transformer_count": "exactly one transformer is supported; the export has {n}",
    "dangling_cable": "lines.csv refers to {column} values that are not in buses.csv",
    "transformer_bus": "the transformer's lv_bus is not in buses.csv",
    "island": "buses cannot be reached from the transformer",
    "meshed_network": "the export is not radial; the engine assumes a radial feeder",
    "cable_type": "sections with neither a type name nor full r/x/c data; the site default cable will be assumed",
    "cable_length_missing": "sections with a missing or non-positive length",
    "cable_length_long": "sections longer than {m} m, which is unusual at low voltage and often means metres and feet are mixed",
    "transformer_rating": "sn_mva is {value}, which is not a distribution transformer",
    "vector_group": "not given, Dyn assumed",
    "orphan_customer": "customers whose bus is not in buses.csv",
    "fuse_rating": "customers with a fuse that is not a standard size, for example {fuse} A",
    "phase_not_recorded": "no phase column, so every service is taken as three-phase and voltage unbalance is understated",
    "phase_not_recorded_cost": "no phase column, so every service is taken as three-phase and voltage unbalance is understated; on the reference grid with single-phase inverters, not knowing the phases costs {pct} % of the static hosting capacity in the median placement",
    "phase_all_three": "every service is three-phase",
    "phase_some_blank": "services recorded as three-phase or with no phase given",
    "phase_value": "unrecognised phase labels, for example {label}",
    "footprint_column": "no footprint_m2 column, so no rooftop PV potential can be estimated",
    "footprint_blank": "customers without footprint_m2, so no rooftop PV potential can be estimated",
    "annual_kwh_column": "no annual_kwh column, so consumption will be estimated from the fuse rating",
    "annual_kwh_blank": "customers without annual_kwh, so consumption will be estimated from the fuse rating",
    "no_coordinates": "buses.csv has no usable lon/lat, so the map cannot be drawn",
    "empty_export": "no contract files found in {directory}",
}


@dataclass
class Finding:
    severity: str
    check: str
    message: str
    count: int = 0
    code: str = ""
    params: dict = field(default_factory=dict)


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    tables: dict[str, int] = field(default_factory=dict)

    def add(self, severity: str, check: str, code: str, count: int = 0, **params) -> None:
        self.findings.append(Finding(severity, check, MESSAGES[code].format(**params), count, code, params))

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


def validate(data: dict[str, pd.DataFrame], phase_cost_pct: float | None = None) -> Report:
    """`phase_cost_pct`: what not knowing the phases costs on the reference grid, when the caller has
    computed it (results/phases.json), so the warning can say so; without it the warning says less."""
    report = Report(tables={k: len(v) for k, v in data.items()})
    if not _check_tables_and_columns(data, report):
        return report
    _check_identifiers(data, report)
    _check_topology(data, report)
    _check_electrical_data(data, report)
    _check_customers(data, report, phase_cost_pct)
    return report


def _check_tables_and_columns(data: dict[str, pd.DataFrame], report: Report) -> bool:
    ok = True
    for table in TABLES:
        if table.name not in data:
            report.add(ERROR, "missing file", "missing_file", table=table.name)
            ok = False
            continue
        missing = [c for c in table.required_columns if c not in data[table.name].columns]
        if missing:
            report.add(ERROR, "missing column", "missing_column", table=table.name, columns=", ".join(missing))
            ok = False
    return ok


def _check_identifiers(data: dict[str, pd.DataFrame], report: Report) -> None:
    for table in TABLES:
        df = data[table.name]
        key = table.key
        if df[key].isna().any():
            report.add(ERROR, "empty identifier", "empty_identifier", int(df[key].isna().sum()),
                       table=table.name, key=key)
        duplicated = int(df[key].duplicated().sum())
        if duplicated:
            report.add(ERROR, "duplicate identifier", "duplicate_identifier", duplicated, table=table.name, key=key)
    if len(data["transformer"]) != 1:
        report.add(ERROR, "transformer count", "transformer_count", len(data["transformer"]),
                   n=len(data["transformer"]))


def _check_topology(data: dict[str, pd.DataFrame], report: Report) -> None:
    buses, lines, trafo = data["buses"], data["lines"], data["transformer"]
    known = set(buses["bus_id"].astype(str))
    for col in ("from_bus", "to_bus"):
        unknown = set(lines[col].astype(str)) - known
        if unknown:
            report.add(ERROR, "dangling cable", "dangling_cable", len(unknown), column=col)
    if len(trafo) == 1 and str(trafo["lv_bus"].iloc[0]) not in known:
        report.add(ERROR, "transformer bus", "transformer_bus")

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
        report.add(ERROR, "island", "island", unreached)
    if loops:
        report.add(WARNING, "meshed network", "meshed_network", loops // 2)


def _check_electrical_data(data: dict[str, pd.DataFrame], report: Report) -> None:
    lines = data["lines"]
    electrical = ["r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km", "max_i_ka"]
    has_type = lines["std_type"].notna() if "std_type" in lines else pd.Series(False, index=lines.index)
    has_values = pd.Series(True, index=lines.index)
    for col in electrical:
        has_values &= lines[col].notna() if col in lines else pd.Series(False, index=lines.index)
    undefined = int((~has_type & ~has_values).sum())
    if undefined:
        report.add(WARNING, "cable type", "cable_type", undefined)

    bad_length = int(((lines["length_m"] <= 0) | lines["length_m"].isna()).sum())
    if bad_length:
        report.add(ERROR, "cable length", "cable_length_missing", bad_length)
    long_sections = int((lines["length_m"] > LV_MAX_SECTION_M).sum())
    if long_sections:
        report.add(WARNING, "cable length", "cable_length_long", long_sections, m=f"{LV_MAX_SECTION_M:.0f}")

    trafo = data["transformer"]
    if len(trafo) == 1:
        row = trafo.iloc[0]
        if not (0.0 < float(row["sn_mva"]) < 5.0):
            report.add(ERROR, "transformer rating", "transformer_rating", value=str(row["sn_mva"]))
        if "vector_group" not in trafo or pd.isna(row.get("vector_group")):
            report.add(NOTE, "vector group", "vector_group")


def _check_customers(data: dict[str, pd.DataFrame], report: Report, phase_cost_pct: float | None) -> None:
    cust, buses = data["customers"], data["buses"]
    known = set(buses["bus_id"].astype(str))
    orphan = int((~cust["bus"].astype(str).isin(known)).sum())
    if orphan:
        report.add(ERROR, "orphan customer", "orphan_customer", orphan)

    odd = cust.loc[~cust["fuse_a"].isin(PLAUSIBLE_FUSES), "fuse_a"]
    if len(odd):
        report.add(WARNING, "fuse rating", "fuse_rating", len(odd), fuse=str(odd.iloc[0]))

    if "phase" not in cust.columns:
        if phase_cost_pct is None:
            report.add(WARNING, "phase not recorded", "phase_not_recorded", len(cust))
        else:
            report.add(WARNING, "phase not recorded", "phase_not_recorded_cost", len(cust),
                       pct=f"{phase_cost_pct:.0f}")
    else:
        blank = int(cust["phase"].isna().sum())
        if blank == len(cust):
            report.add(NOTE, "phase", "phase_all_three")
        elif blank:
            report.add(NOTE, "phase", "phase_some_blank", blank)
        bad = cust.loc[cust["phase"].notna() & ~cust["phase"].astype(str).str.lower()
                       .isin(["a", "b", "c", "l1", "l2", "l3"]), "phase"]
        if len(bad):
            report.add(ERROR, "phase value", "phase_value", len(bad), label=repr(bad.iloc[0]))

    for col, code in (("footprint_m2", "footprint"), ("annual_kwh", "annual_kwh")):
        if col not in cust.columns:
            report.add(WARNING, f"{col} missing", f"{code}_column", len(cust))
        else:
            n = int(cust[col].isna().sum())
            if n:
                report.add(WARNING, f"{col} missing", f"{code}_blank", n)

    if "lon" not in buses.columns or buses["lon"].isna().all():
        report.add(WARNING, "no coordinates", "no_coordinates")


def check_directory(directory: str | Path) -> Report:
    data = read_export(directory)
    if not data:
        report = Report()
        report.add(ERROR, "empty export", "empty_export", directory=str(directory))
        return report
    return validate(data)

