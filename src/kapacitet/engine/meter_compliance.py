"""Did every customer keep to the envelope it was sent? The whole fleet's metered export against
the day-ahead envelopes, interval by interval.

A limit nobody follows protects nothing: in SA Power Networks' Flexible Exports trial the limits
were kept, but elsewhere in Australia a quarter of the sites on flexible exports did not comply.
`delivery.meter_check` checks one meter file at a time; this checks every agreement at once, from
the readings a study keeps in processed/meters/:

    export_15min.parquet   customer_id, start (UTC), kwh_export: the quarter-hours, as a Swedish
                           meter reports them (ingest/meters.to_hourly writes it when the files
                           carry kwh_export)
    hourly.parquet         the same summed to complete hours, read when there are no quarter-hours

The envelopes are verified hour by hour, so the check is made per hour: four quarter-hours sum to
the hour's energy (an hour with fewer than four is not checked), and an hour's energy in kWh is
its mean export in kW. An hour is above its envelope when that mean exceeds the day-ahead envelope
by more than `compliance_tolerance_kw`; its energy above is the excess over the envelope itself.
A short excursion that averages out within the hour is not seen here; the single-meter check,
which compares every reading, still sees it.

The envelope is always the day-ahead one, the only basis that is ever issued, under the DSO's
sharing rule, and only from a combination the operation stage verified over the whole year.

    meter_compliance.json    per agreement: hours checked, hours above, energy above, the worst
                             hour and a status (compliant, non_compliant or no_readings)
    meter_compliance_sv.txt  the same as a page in Swedish

Without readings nothing is written. `synthetic` builds a labelled fleet from the operation
stage's own figures instead: each agreement exports its simulated potential with the envelope
applied (min(desired, envelope)), and `inject` lets a test add over-export, as the share of what
the envelope held back that the house exports anyway.
"""

from __future__ import annotations

import json
from datetime import date as Date
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .. import paths

SCHEMA = "kapacitet.meter_compliance/1"
BASIS = "dayahead"
COMMAND = "python -m kapacitet meter-compliance"
OPERATION = "python -m kapacitet compute --stage extra --only operation"
STATUSES = ("compliant", "non_compliant", "no_readings")
_ORDER = ("non_compliant", "no_readings", "compliant")
INTERVAL_MIN = 60     # the step the envelopes are verified in, and so the step they are checked in


class MeterComplianceError(RuntimeError):
    """The check cannot be made honestly: the envelopes or the period are missing or wrong."""


# ---------------------------------------------------------------- readings

def readings(root: Path | None = None) -> tuple[pd.DataFrame, int] | None:
    """The metered export in processed/meters/ as (customer_id, start, kwh_export), with the
    minutes each reading covers (15 or 60); None when there is no export reading at all."""
    folder = Path(root or paths.PROCESSED) / "meters"
    quarters = folder / "export_15min.parquet"
    if quarters.is_file():
        df = pd.read_parquet(quarters, columns=["customer_id", "start", "kwh_export"]).dropna(subset=["kwh_export"])
        if len(df):
            return df, 15
    hourly = folder / "hourly.parquet"
    if hourly.is_file():
        df = pd.read_parquet(hourly, columns=["customer_id", "start", "kwh_export"]).dropna(subset=["kwh_export"])
        if len(df):
            return df, 60
    return None


def to_hours(quarters: pd.DataFrame) -> pd.DataFrame:
    """Quarter-hour export summed to complete hours (all four quarters present): customer_id,
    start (the hour's start in UTC), kwh_export. An hour in UTC is an hour in UTC+1 as well."""
    df = quarters[["customer_id", "start", "kwh_export"]].copy()
    df["customer_id"] = df["customer_id"].astype(str)
    df["start"] = pd.to_datetime(df["start"], utc=True)
    df = df.drop_duplicates(["customer_id", "start"])
    df["hour"] = df["start"].dt.floor("h")
    g = df.groupby(["customer_id", "hour"], sort=True)["kwh_export"].agg(["sum", "count"]).reset_index()
    g = g[g["count"] == 4]
    out = g.rename(columns={"hour": "start", "sum": "kwh_export"})[["customer_id", "start", "kwh_export"]]
    return out.astype({"kwh_export": float}).reset_index(drop=True)


def on_year(hourly: pd.DataFrame, year: int) -> pd.DataFrame:
    """Each hour's position on timeindex.year_index(year), as a column `hour`; hours outside the
    year are dropped (the caller counts them)."""
    from ..timeindex import year_index

    idx = year_index(year)
    start = pd.DatetimeIndex(pd.to_datetime(hourly["start"], utc=True)).as_unit("ns")
    pos = (start.asi8 - idx[0].value) // 3_600_000_000_000
    out = hourly.assign(hour=pos.astype(np.int64))
    return out[(out["hour"] >= 0) & (out["hour"] < len(idx))]


# ---------------------------------------------------------------- the check

def check(hourly: pd.DataFrame, envelopes: np.ndarray, cids: list[int], year: int, tolerance_kw: float,
          hours: tuple[int, int] | None = None, ids: list[str] | None = None) -> dict:
    """Every agreement's hourly export against its day-ahead envelope.

    `hourly` has the columns cid (the engine's customer index), hour (on year_index(year)) and
    kwh_export; `envelopes` is (agreements, hours) in kW, row i for cids[i]; `hours` the half-open
    span of hours checked; `ids` the customer ids of the network by cid, for the report."""
    from ..timeindex import year_index

    H = envelopes.shape[1]
    lo, hi = hours if hours is not None else (0, H)
    idx = year_index(year)
    inside = hourly[(hourly["hour"] >= lo) & (hourly["hour"] < hi)]
    agreement = {int(c): i for i, c in enumerate(cids)}
    groups = {int(c): g for c, g in inside.groupby("cid", sort=False)}
    per = []
    for c in cids:
        c = int(c)
        g = groups.get(c)
        entry = {"cid": c, "customer_id": ids[c] if ids is not None and c < len(ids) else None}
        if g is None or not len(g):
            per.append(entry | {"intervals_checked": 0, "intervals_above": 0, "energy_above_kwh": 0.0,
                                "worst": None, "status": "no_readings"})
            continue
        h = g["hour"].to_numpy(dtype=np.int64)
        export = g["kwh_export"].to_numpy(dtype=float)        # kWh in an hour = mean kW
        limit = np.asarray(envelopes[agreement[c]], dtype=float)[h]
        excess = export - limit
        above = excess > tolerance_kw
        k = int(np.argmax(excess))
        worst = None
        if excess[k] > 0:
            worst = {"hour": int(h[k]), "start": idx[int(h[k])].isoformat(),
                     "export_kw": round(float(export[k]), 3), "limit_kw": round(float(limit[k]), 3),
                     "excess_kw": round(float(excess[k]), 3)}
        per.append(entry | {"intervals_checked": int(len(h)), "intervals_above": int(above.sum()),
                            "energy_above_kwh": round(float(excess[above].sum()), 3), "worst": worst,
                            "status": "non_compliant" if above.any() else "compliant"})
    checked = sum(p["intervals_checked"] for p in per)
    above_n = sum(p["intervals_above"] for p in per)
    summary = {"agreements": len(per), **{s: sum(1 for p in per if p["status"] == s) for s in STATUSES},
               "intervals_checked": int(checked), "intervals_above": int(above_n),
               "energy_above_kwh": round(sum(p["energy_above_kwh"] for p in per), 3),
               "intervals_within_pct": round(100.0 * (1 - above_n / checked), 3) if checked else None,
               "meters_without_agreement": int(len(set(map(int, inside["cid"])) - set(agreement)))}
    # the houses above their envelope first, the most energy above at the top
    per.sort(key=lambda p: (_ORDER.index(p["status"]), -p["energy_above_kwh"], p["cid"]))
    return {"summary": summary, "per_customer": per}


# ---------------------------------------------------------------- what the operation stage wrote

def _operation(root: Path, policy: str | None) -> dict:
    """The day-ahead combination to check against: its year, rule, agreements and envelopes."""
    from .. import dso

    path = root / "results" / "operation" / "index.json"
    if not path.is_file():
        raise MeterComplianceError(f"no issued envelopes: results/operation/index.json is missing; run {OPERATION}")
    index = json.loads(path.read_text(encoding="utf-8"))
    policy = policy or dso.load().allocation_policy
    combo = (index.get("combos", {}).get(BASIS) or {}).get(policy)
    env_path = root / "ui" / "operation" / BASIS / policy / "envelopes.npy"
    if not combo or not env_path.is_file():
        raise MeterComplianceError(f"{BASIS}/{policy} was not computed: run {OPERATION}")
    if not combo.get("verified"):
        raise MeterComplianceError(f"{BASIS}/{policy} did not verify over the year, so its envelopes are not issued")
    return {"year": int(index["year"]), "policy": policy, "cids": [int(c) for c in index["nonfirm_cids"]],
            "envelopes": np.asarray(np.load(env_path), dtype=float), "root": root}


def _customer_ids(root: Path) -> list[str] | None:
    """The network's customer ids by cid: customers.csv's customer_id, which the engine keeps as
    osm_id (an OSM id on the demo's network), the id the meter readings carry."""
    path = root / "grid" / "customers.parquet"
    if not path.is_file():
        return None
    cust = pd.read_parquet(path, columns=["osm_id"])
    return cust["osm_id"].astype(str).tolist()          # by position, which is the cid (metered.build)


def period(year: int, start: str | None, end: str | None) -> tuple[tuple[int, int], dict]:
    """The half-open span of hours from the first date's first hour to the last date's last, both
    inclusive, as calendar dates in the study's UTC+1 year."""
    from .delivery import day_of

    first = Date(year, 1, 1)
    last = Date(year, 12, 31)
    try:
        d1 = Date.fromisoformat(start) if start else first
        d2 = Date.fromisoformat(end) if end else last
        a, b = day_of(year, d1), day_of(year, d2)
    except ValueError as err:
        raise MeterComplianceError(f"the period has to be dates in the study year {year}: {err}") from None
    if b < a:
        raise MeterComplianceError(f"the period ends ({d2}) before it starts ({d1})")
    return (24 * a, 24 * (b + 1)), {"from": d1.isoformat(), "to": d2.isoformat()}


def parse_inject(items: list[str] | None) -> dict[int, float]:
    """`cid:share` pairs (repeated, or comma-separated) as {cid: share}."""
    out: dict[int, float] = {}
    for item in items or []:
        for part in str(item).split(","):
            if not part.strip():
                continue
            try:
                cid, share = part.split(":")
                c, s = int(cid), float(share)
            except ValueError:
                raise MeterComplianceError(f"--inject takes cid:share, such as 12:0.5, not {part!r}") from None
            if not np.isfinite(s) or s < 0:
                raise MeterComplianceError(f"--inject {part}: the share has to be 0 or more")
            out[c] = s
    return out


def synthetic_hourly(desired: np.ndarray, envelopes: np.ndarray, cids: list[int],
                     inject: dict[int, float] | None = None) -> pd.DataFrame:
    """A labelled fleet: each agreement's simulated export with its envelope applied, plus, for an
    injected cid, `share` of what the envelope held back (cid, hour, kwh_export)."""
    inject = inject or {}
    unknown = sorted(set(inject) - set(map(int, cids)))
    if unknown:
        raise MeterComplianceError(f"--inject names houses without a conditional agreement: {unknown}")
    desired = np.asarray(desired, dtype=float)
    env = np.asarray(envelopes, dtype=float)
    export = np.minimum(desired, env)
    held = np.maximum(desired - env, 0.0)
    for i, c in enumerate(cids):
        if int(c) in inject:
            export[i] = export[i] + inject[int(c)] * held[i]
    n, H = export.shape
    return pd.DataFrame({"cid": np.repeat(np.asarray(cids, dtype=np.int64), H),
                         "hour": np.tile(np.arange(H, dtype=np.int64), n),
                         "kwh_export": export.ravel()})


def build(root: Path | None = None, policy: str | None = None, start: str | None = None, end: str | None = None,
          synthetic: bool = False, inject: dict[int, float] | None = None,
          tolerance_kw: float | None = None) -> dict | None:
    """The fleet's report, or None when there are no readings and no synthetic fleet was asked for."""
    from ..config import A

    root = Path(root or paths.PROCESSED)
    if inject and not synthetic:
        raise MeterComplianceError("--inject adds over-export to the synthetic fleet, so it needs --synthetic")
    metered = None if synthetic else readings(root)
    if metered is None and not synthetic:
        return None
    op = _operation(root, policy)
    year, cids, env = op["year"], op["cids"], op["envelopes"]
    span, dates = period(year, start, end)
    tol = float(A("compliance_tolerance_kw") if tolerance_kw is None else tolerance_kw)
    ids = _customer_ids(root)
    extra: dict = {}
    if synthetic:
        desired_path = root / "ui" / "operation" / "desired.npy"
        if not desired_path.is_file():
            raise MeterComplianceError(f"ui/operation/desired.npy is missing: run {OPERATION}")
        hourly = synthetic_hourly(np.load(desired_path), env, cids, inject)
        source, step = "synthetic", INTERVAL_MIN
        extra = {"injected": {str(c): s for c, s in sorted((inject or {}).items())}, "unmatched_meters": 0,
                 "outside_period": int(((hourly["hour"] < span[0]) | (hourly["hour"] >= span[1])).sum())}
    else:
        df, step = metered
        hours = to_hours(df) if step == 15 else df.assign(customer_id=df["customer_id"].astype(str),
                                                          start=pd.to_datetime(df["start"], utc=True))
        lookup = {cid_text: i for i, cid_text in enumerate(ids or [])}
        hours = hours.assign(cid=hours["customer_id"].map(lookup))
        unmatched = int(hours.loc[hours["cid"].isna(), "customer_id"].nunique())
        hours = hours[hours["cid"].notna()].astype({"cid": np.int64})
        placed = on_year(hours, year)
        in_span = (placed["hour"] >= span[0]) & (placed["hour"] < span[1])
        extra = {"unmatched_meters": unmatched, "outside_period": int(len(hours) - int(in_span.sum()))}
        hourly = placed
        source = "metered"
    out = check(hourly, env, cids, year, tol, span, ids)
    out["summary"].update({k: extra.pop(k) for k in ("unmatched_meters", "outside_period")})
    return {
        "schema": SCHEMA,
        "year": year,
        "basis": BASIS,
        "policy": op["policy"],
        "readings": source,
        "reading_resolution_min": int(step),
        "interval_min": INTERVAL_MIN,
        "period": dates,
        "tolerance_kw": tol,
        **extra,
        **out,
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": ("Each hour's metered export (four quarter-hours summed, or an hourly reading) against the "
                 "day-ahead envelope issued for that hour; above means more than the tolerance over it, and the "
                 "energy above is the excess over the envelope itself."
                 + (" Synthetic readings: the simulated export with the envelope applied, not measured."
                    if synthetic else "")),
    }


# ---------------------------------------------------------------- the page in Swedish

_SV_POLICY = {"maxmin": "maxmin", "prorata": "proportionell", "propfair": "proportionellt rättvis",
              "maxtotal": "största totala export"}
_SV_STATUS = {"compliant": "följde gränsen", "non_compliant": "över gränsen", "no_readings": "inga mätvärden"}


def text_sv(r: dict) -> str:
    """The report as a page in Swedish."""
    from .redispatch import _n

    s = r["summary"]
    readings_sv = {"metered": ("mätvärden per kvart, summerade till hela timmar" if r["reading_resolution_min"] == 15
                               else "mätvärden per timme"),
                   "synthetic": "syntetiska värden: den simulerade exporten med exportgränsen tillämpad, inte uppmätt"}
    lines = [
        f"Efterlevnad av exportgränser, {r['period']['from']} till {r['period']['to']}",
        "=" * 58,
        "Underlag: exportgränser utfärdade dagen före, timme för timme",
        f"Fördelningsregel: {_SV_POLICY.get(r['policy'], r['policy'])}",
        f"Mätvärden: {readings_sv[r['readings']]}",
        f"Tolerans: {_n(r['tolerance_kw'], 2)} kW över gränsen räknas som överträdelse",
        "",
        f"Villkorade avtal                  {_n(s['agreements'])}",
        f"  följde gränsen                  {_n(s['compliant'])}",
        f"  över gränsen                    {_n(s['non_compliant'])}",
        f"  inga mätvärden                  {_n(s['no_readings'])}",
        f"Kontrollerade timmar              {_n(s['intervals_checked'])}",
        f"Timmar över gränsen               {_n(s['intervals_above'])}",
        f"Energi över gränsen               {_n(s['energy_above_kwh'], 1)} kWh",
    ]
    if s.get("intervals_within_pct") is not None:
        lines.append(f"Timmar inom gränsen               {_n(s['intervals_within_pct'], 2)} %")
    if s.get("unmatched_meters"):
        lines.append(f"Mätare som inte finns i nätet     {_n(s['unmatched_meters'])}")
    over = [p for p in r["per_customer"] if p["status"] == "non_compliant"]
    lines += ["", "Kunder över gränsen"]
    for p in over:
        w = p["worst"] or {}
        who = f"kund {p['cid']}" + (f" ({p['customer_id']})" if p.get("customer_id") else "")
        lines.append(f"  {who}: {_n(p['intervals_above'])} timmar, {_n(p['energy_above_kwh'], 1)} kWh över; "
                     f"värst {w.get('start', '')[:16]} UTC, {_n(w.get('export_kw', 0), 2)} kW mot "
                     f"{_n(w.get('limit_kw', 0), 2)} kW")
    if not over:
        lines.append("  inga")
    lines += ["", ("Syntetiska värden, inte uppmätta: underlag, aldrig en juridisk bedömning." if r["readings"] == "synthetic"
                   else "Underlag för uppföljning, aldrig en juridisk bedömning.")]
    return "\n".join(lines) + "\n"


def write(report: dict, root: Path | None = None) -> tuple[Path, Path]:
    """results/meter_compliance.json and meter_compliance_sv.txt under `root` (processed/)."""
    from ..atomic import write_json_atomic, write_text_atomic

    folder = Path(root or paths.PROCESSED) / "results"
    folder.mkdir(parents=True, exist_ok=True)
    js, txt = folder / "meter_compliance.json", folder / "meter_compliance_sv.txt"
    write_json_atomic(js, report)
    write_text_atomic(txt, text_sv(report))
    return js, txt
