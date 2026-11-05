"""Data-quality report on an operator's network export.

This is the first thing a DSO gets from us, before any capacity number: a list of what
their low-voltage records can and cannot support, with a count on every line. It runs on
the four CSV files of `contract.py`, and on the optional `equipment.csv` when it is there, and
needs nothing else.

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

import numpy as np
import pandas as pd

from .contract import ALL_TABLES, BY_NAME, NO, PV_PHASE_LABELS, TABLES, YES

ERROR, WARNING, NOTE = "error", "warning", "note"
LV_MAX_SECTION_M = 2000.0
PLAUSIBLE_FUSES = {16, 20, 25, 35, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400}
# identifiers are read as text, so "007" stays "007" and matches a meter or equipment record
IDENTIFIERS = dict.fromkeys(("bus_id", "line_id", "from_bus", "to_bus", "lv_bus", "bus", "customer_id"), str)


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
    "annual_kwh_column": "no annual_kwh column, so the synthetic household profiles cannot be compared with metered consumption",
    "annual_kwh_blank": "customers without annual_kwh, whose synthetic household profiles cannot be compared with metered consumption",
    "no_coordinates": "buses.csv has no usable lon/lat, so the map cannot be drawn",
    "empty_export": "no contract files found in {directory}",
    # a CGMES network's conversion (importer/cim.py), before there is a contract to check
    "cim_unmapped_consumer": "EnergyConsumers that cim_customers.csv does not name, for example {mrid}: each needs a customer_id and a fuse_a",
    "cim_shared_island": "the low-voltage network is also fed by transformer {mrid}; a station has one transformer, so the open point between them has to be open in the SSH profile",
    "cim_contract": "the network cannot be written as the contract: {message}",
    "cim_converter": "the CGMES converter reports: {message}",
    "cim_no_station": "no in-service transformer steps down to {kv} kV or less",
    "cim_unreached_consumer": "low-voltage EnergyConsumers that no transformer reaches, for example {mrid}, so they are in no station",
    "cim_mapping_unused": "rows of cim_customers.csv whose EnergyConsumer is in no station, for example {mrid}",
    # equipment.csv, the optional table of what is already connected behind each meter
    "equipment_unknown_customer": "equipment.csv names customers that are not in customers.csv, for example {customer}; their rows are not counted",
    "equipment_value": "equipment.csv has {column} values that are not a number of zero or more, for example {value}",
    "equipment_phase": "equipment.csv has pv_phase labels other than a, b, c and abc, for example {label}",
    "equipment_ev_phases": "equipment.csv has ev_phases other than 1 and 3, for example {value}",
    "equipment_heat_pump": "equipment.csv has heat_pump values other than yes and no, for example {value}",
    "equipment_date": "equipment.csv has connected_on values that are not dates (YYYY-MM-DD), for example {value}",
    "equipment_phase_conflict": "customers whose service is single-phase on one phase while equipment.csv puts their equipment on another or on three, for example {customer}",
    "equipment_pv_ac": "PV without pv_kw_ac, whose AC rating is taken as pv_kwp / {ratio}",
    "equipment_pv_phase": "single-phase PV without pv_phase, whose phase is allocated by the rule",
    "equipment_over_fuse": "equipment rated above what the customer's main fuse carries, for example {customer}",
    "equipment_battery": "batteries with only one of battery_kwh and battery_kw, which are not counted",
    "equipment_source": "rows of equipment.csv that do not say where the record comes from",
    "equipment_summary": "equipment.csv records {pv} PV systems ({kw} kW AC), {batteries} batteries, {chargers} EV chargers and {heat_pumps} heat pumps",
    # the meter readings (ingest/meters.py)
    "meter_empty": "no meter readings in the files",
    "meter_missing_column": "the meter readings have no {columns}",
    "meter_no_id": "readings without a customer_id or meter_id, for example row {row}",
    "meter_naive_time": "times without a UTC offset, for example {ts}: on the night the clocks go back they name two different quarter-hours, so they are refused",
    "meter_bad_time": "times that are not ISO 8601, for example {ts}",
    "meter_dst": "times whose UTC offset is not Swedish time at that instant, in a file that changes offset, for example {ts}: daylight saving time is written wrong, and these readings are not used",
    "meter_off_grid": "times off the 15-minute grid, for example {ts}, whose readings are not used",
    "meter_unreadable": "readings with a blank or unreadable value, for example {value}, which are not used",
    "meter_negative": "readings with a negative value, which are not used",
    "meter_unmapped": "meters that are no customer of customers.csv, for example {meter}, whose readings are not used",
    "meter_duplicate_same": "readings given more than once with the same values, counted once",
    "meter_duplicate": "quarter-hours given more than once with different values, for example {example}, which are not used",
    "meter_gaps": "quarter-hours missing between a meter's first and last reading, at {meters} meters",
    "meter_incomplete": "meters with fewer than {pct} % of their hours complete, for example {meter}",
    "meter_complete": "complete hours at {meters} meters, {pct} % of the hours between each one's first and last reading",
    "meter_no_readings": "customers without meter readings, whose household load stays synthetic",
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
    title: str = "Network export check"

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
        lines = [self.title, "=" * len(self.title), "  ".join(head), ""]
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
    for table in ALL_TABLES:
        path = directory / f"{table.name}.csv"
        if path.exists():
            out[table.name] = pd.read_csv(path, dtype=IDENTIFIERS)
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
    if "equipment" in data:
        _check_equipment(data["equipment"], data["customers"], report)
    return report


def check_equipment(equipment: pd.DataFrame, customers: pd.DataFrame) -> Report:
    """The checks of equipment.csv alone, against the customers.csv it refers to."""
    report = Report(tables={"equipment": len(equipment)})
    _check_equipment(equipment, customers, report)
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


# ---------------------------------------------------------------- equipment.csv

EQUIPMENT_NUMBERS = ("pv_kwp", "pv_kw_ac", "battery_kwh", "battery_kw", "ev_charger_kw", "heat_pump_kw")
PHASE_VOLTAGE_V = 230.0


def _blank(values: pd.Series) -> pd.Series:
    return values.isna() | (values.astype(str).str.strip() == "")


def _label(values: pd.Series) -> pd.Series:
    return values.astype(str).str.strip().str.lower().str.replace(" ", "", regex=False)


def equipment_frame(equipment: pd.DataFrame) -> pd.DataFrame:
    """equipment.csv with its values read: the ratings as floats (NaN when blank or not a number of
    zero or more), pv_phase as a, b, c or abc ('' when blank or unknown), ev_phases as 1 or 3 (NaN
    otherwise), heat_pump as True, False or NA, source as text and connected_on as YYYY-MM-DD ('' when
    blank or not a date)."""
    eq = equipment
    nan = pd.Series(float("nan"), index=eq.index)
    out = pd.DataFrame({"customer_id": eq["customer_id"].astype(str).str.strip()}, index=eq.index)
    for col in EQUIPMENT_NUMBERS:
        v = pd.to_numeric(eq[col], errors="coerce") if col in eq else nan
        out[col] = v.where(v >= 0).astype(float)
    if "pv_phase" in eq:
        out["pv_phase"] = _label(eq["pv_phase"]).map(PV_PHASE_LABELS).where(~_blank(eq["pv_phase"])).fillna("")
    else:
        out["pv_phase"] = ""
    v = pd.to_numeric(eq["ev_phases"], errors="coerce") if "ev_phases" in eq else nan
    out["ev_phases"] = v.where(v.isin([1, 3])).astype(float)
    if "heat_pump" in eq:
        lab = _label(eq["heat_pump"]).where(~_blank(eq["heat_pump"]), "")
        out["heat_pump"] = pd.array([True if x in YES else False if x in NO else pd.NA for x in lab],
                                    dtype="boolean")
    else:
        out["heat_pump"] = pd.array([pd.NA] * len(eq), dtype="boolean")
    out["source"] = (eq["source"].where(~_blank(eq["source"]), "").astype(str).str.strip()
                     if "source" in eq else "")
    if "connected_on" in eq:
        when = pd.to_datetime(eq["connected_on"].astype(str).str.strip(), format="%Y-%m-%d", errors="coerce")
        out["connected_on"] = when.dt.strftime("%Y-%m-%d").fillna("")
    else:
        out["connected_on"] = ""
    return out


def service_phases(customers: pd.DataFrame) -> pd.Series:
    """customers.csv's phase per customer_id: a, b or c for a single-phase service, '' otherwise."""
    ids = customers["customer_id"].astype(str).str.strip()
    if "phase" not in customers:
        return pd.Series("", index=ids)
    lab = _label(customers["phase"]).map(PV_PHASE_LABELS).where(~_blank(customers["phase"]))
    return pd.Series(lab.where(lab.isin(["a", "b", "c"])).fillna("").to_numpy(), index=ids)


def _check_equipment(eq: pd.DataFrame, cust: pd.DataFrame, report: Report) -> None:
    from ..config import A

    table = BY_NAME["equipment"]
    missing = [c for c in table.required_columns if c not in eq.columns]
    if missing:
        report.add(ERROR, "missing column", "missing_column", table=table.name, columns=", ".join(missing))
        return
    given = eq["customer_id"].notna()
    if not given.all():
        report.add(ERROR, "empty identifier", "empty_identifier", int((~given).sum()), table=table.name, key=table.key)
    ids = eq["customer_id"].astype(str).str.strip()
    twice = int(ids[given].duplicated().sum())
    if twice:
        report.add(ERROR, "duplicate identifier", "duplicate_identifier", twice, table=table.name, key=table.key)
    unknown = given & ~ids.isin(set(cust["customer_id"].astype(str).str.strip()))
    if unknown.any():
        report.add(WARNING, "equipment customer", "equipment_unknown_customer", int(unknown.sum()),
                   customer=str(ids[unknown].iloc[0]))
    _check_equipment_values(eq, report)

    f = equipment_frame(eq[given & ~unknown])
    if f.empty:
        return
    by_id = cust.assign(_id=cust["customer_id"].astype(str).str.strip()).drop_duplicates("_id").set_index("_id")
    service = service_phases(cust).groupby(level=0).first().reindex(f["customer_id"]).fillna("").to_numpy()
    fuse = pd.to_numeric(by_id["fuse_a"], errors="coerce").reindex(f["customer_id"]).to_numpy(dtype=float)
    kwp = f["pv_kwp"].fillna(0.0).to_numpy()
    pv, pv_phase = kwp > 0, f["pv_phase"].to_numpy()
    charger_kw = f["ev_charger_kw"].fillna(0.0).to_numpy()
    charger, ev_phases = charger_kw > 0, f["ev_phases"].to_numpy()
    single_service = service != ""
    conflict = single_service & ((pv & (pv_phase != "") & (pv_phase != service)) | (charger & (ev_phases == 3)))
    if conflict.any():
        report.add(ERROR, "equipment phase", "equipment_phase_conflict", int(conflict.sum()),
                   customer=str(f["customer_id"].to_numpy()[conflict][0]))

    ratio = float(A("pv_dc_ac_ratio"))
    ac = f["pv_kw_ac"].to_numpy()
    no_ac = pv & ~(ac > 0)
    if no_ac.any():
        report.add(WARNING, "equipment fallback", "equipment_pv_ac", int(no_ac.sum()), ratio=f"{ratio:g}")
    ac = np.where(no_ac, kwp / ratio, np.nan_to_num(ac))
    small = pv & (ac <= float(A("pv_single_phase_max_kva")))
    unphased = small & (pv_phase == "") & ~single_service
    if unphased.any():
        report.add(WARNING, "equipment fallback", "equipment_pv_phase", int(unphased.sum()))
    bat_kwh, bat_kw = f["battery_kwh"].fillna(0.0).to_numpy(), f["battery_kw"].fillna(0.0).to_numpy()
    battery, half = (bat_kwh > 0) & (bat_kw > 0), (bat_kwh > 0) ^ (bat_kw > 0)
    if half.any():
        report.add(WARNING, "equipment fallback", "equipment_battery", int(half.sum()))

    # what the main fuse carries: on three phases, or on one for a single-phase service or item
    one = PHASE_VOLTAGE_V * fuse / 1000.0
    pv_single = single_service | ((pv_phase != "") & (pv_phase != "abc")) | (small & (pv_phase == ""))
    pv_cap = np.where(pv_single, one, 3 * one)
    ev_cap = np.where(single_service | (ev_phases == 1), one, 3 * one)
    bat_cap = np.where(single_service, one, 3 * one)
    over = np.isfinite(fuse) & ((pv & (ac > pv_cap + 1e-9)) | (charger & (charger_kw > ev_cap + 1e-9))
                                | (battery & (bat_kw > bat_cap + 1e-9)))
    if over.any():
        report.add(WARNING, "equipment rating", "equipment_over_fuse", int(over.sum()),
                   customer=str(f["customer_id"].to_numpy()[over][0]))
    unsourced = int((f["source"] == "").sum())
    if unsourced:
        report.add(NOTE, "equipment source", "equipment_source", unsourced)
    report.add(NOTE, "equipment", "equipment_summary", len(f), pv=str(int(pv.sum())), kw=f"{float(ac[pv].sum()):.1f}",
               batteries=str(int(battery.sum())), chargers=str(int(charger.sum())),
               heat_pumps=str(int(f["heat_pump"].fillna(False).astype(bool).sum())))


def _check_equipment_values(eq: pd.DataFrame, report: Report) -> None:
    """Values the engine cannot read: an error, since a record it guessed at is no record."""
    for col in EQUIPMENT_NUMBERS:
        if col in eq:
            v = pd.to_numeric(eq[col], errors="coerce")
            bad = ~_blank(eq[col]) & (v.isna() | (v < 0))
            if bad.any():
                report.add(ERROR, "equipment value", "equipment_value", int(bad.sum()), column=col,
                           value=repr(eq.loc[bad, col].iloc[0]))
    checks = (("pv_phase", "equipment phase", "equipment_phase", "label",
               lambda s: _label(s).isin(PV_PHASE_LABELS)),
              ("ev_phases", "equipment value", "equipment_ev_phases", "value",
               lambda s: pd.to_numeric(s, errors="coerce").isin([1, 3])),
              ("heat_pump", "equipment value", "equipment_heat_pump", "value",
               lambda s: _label(s).isin(YES | NO)),
              ("connected_on", "equipment value", "equipment_date", "value",
               lambda s: pd.to_datetime(s.astype(str).str.strip(), format="%Y-%m-%d", errors="coerce").notna()))
    for col, check, code, param, ok in checks:
        if col in eq:
            bad = ~_blank(eq[col]) & ~ok(eq[col])
            if bad.any():
                report.add(ERROR, check, code, int(bad.sum()), **{param: repr(eq.loc[bad, col].iloc[0])})


def check_directory(directory: str | Path) -> Report:
    data = read_export(directory)
    if not data:
        report = Report()
        report.add(ERROR, "empty export", "empty_export", directory=str(directory))
        return report
    return validate(data)

