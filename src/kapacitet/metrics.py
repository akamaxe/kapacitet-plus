"""The metrics an investor asks for every month, as far as the product can compute them itself.

    kapacitet metrics                 this data folder: results/metrics.json, metrics.md, metrics_sv.md
    kapacitet metrics --study <dir>   a study: the same three files in the study's folder

Nothing here is typed by hand. Each metric is read from a file the installation already writes:

- **Use.** Assessments pre-checked (the records index, requests/index.jsonl), how often the DSO's
  decision agreed with the rule's proposal (the applications log in advisor mode; in a study, the
  case batches run with `--compare-decisions`) and the median days from application to decision
  (the applications log, as the inbox's own indicators fold it).
- **Model quality.** Voltage and transformer-load error against measurements, and how well the
  model matches a dpPower snapshot (each station's results/validation_measured.json, written by
  `study validate`), pooled over the stations by their number of samples.
- **Envelopes.** Hours activated against hours needed (the redispatch report, on the day-ahead
  basis that is issued), station-hours in which production beat the planning bound (the
  day-ahead and intraday audits), the share of agreements within the per-installation budget
  while the headline uses the aggregate one, and the capacity with envelopes over the hourly study
  and over a fixed export cap (the hosting summary).

Every metric carries its source file and its period. A metric whose source is missing is
`not_available`, with the command that writes the source; one a study cannot have (the daily
operation of conditional agreements) says so. Discovery, sales, onboarding and security are
listed as entered by the owner and never given a value here.

The two texts are made from templates/metrics.en.md and metrics.sv.md, which hold no figure: every
number in them is a metric's value, detail or period, formatted (tests/test_metrics.py).
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .dossier import render, sv_number

SCHEMA = "kapacitet.metrics/1"
JSON_NAME = "metrics.json"
TEXT_NAMES = {"en": "metrics.md", "sv": "metrics_sv.md"}
TEMPLATES = {lang: Path(__file__).with_name("templates") / f"metrics.{lang}.md" for lang in ("en", "sv")}

SERVE = "python -m kapacitet serve"
COMPUTE = "python -m kapacitet compute"
EXTRA = "python -m kapacitet compute --stage extra --only "
REDISPATCH = "python -m kapacitet redispatch-report"
METRICS = "python -m kapacitet metrics"
VALIDATE = "python -m kapacitet study validate <dir>"
CASES = "python -m kapacitet study cases <dir> <cases.csv>"
STUDY_BUILD = "python -m kapacitet study build <dir>"

GROUPS = ("use", "model", "envelopes", "owner")
# id: group, unit, decimals, label in English, label in Swedish (no figure in either)
DEFINITIONS: dict[str, tuple[str, str, int, str, str]] = {
    "assessments_prechecked": ("use", "count", 0, "Assessments pre-checked", "Förhandsprövade bedömningar"),
    "proposal_agreement_pct": ("use", "pct", 1, "Proposals the DSO's decision agreed with",
                               "Förslag som nätbolagets beslut stämde överens med"),
    "median_days_to_decision": ("use", "days", 1, "Median time from application to decision",
                                "Mediantid från ansökan till beslut"),
    "voltage_error_pu": ("model", "pu", 4, "Voltage error against measurements (RMSE)",
                         "Spänningsfel mot mätningar (RMSE)"),
    "transformer_error_pp": ("model", "pp", 1, "Transformer-load error against measurements (RMSE)",
                             "Fel i transformatorns belastning mot mätningar (RMSE)"),
    "dppower_matched_pct": ("model", "pct", 1, "dpPower snapshot elements matched by mRID",
                            "Element i dpPower-ögonblicksbilden som matchades via mRID"),
    "dppower_voltage_pu": ("model", "pu", 4, "Voltage difference from the dpPower snapshot (RMSE)",
                           "Spänningsskillnad mot dpPower-ögonblicksbilden (RMSE)"),
    "envelope_hours_activated": ("envelopes", "hours", 0, "Hours with envelopes activated (day-ahead basis)",
                                 "Timmar med aktiverade exportgränser (utfärdade dagen före)"),
    "envelope_hours_needed": ("envelopes", "hours", 0, "Of those, hours a limit actually needed",
                              "Därav timmar då en gräns faktiskt behövdes"),
    "dayahead_bound_exceeded_hours": ("envelopes", "hours", 0,
                                      "Station-hours production beat the day-ahead planning bound",
                                      "Stationstimmar då produktionen översteg planeringsgränsen dagen före"),
    "intraday_bound_exceeded_hours": ("envelopes", "hours", 0,
                                      "Station-hours production beat the intraday planning bound",
                                      "Stationstimmar då produktionen översteg planeringsgränsen inom dygnet"),
    "agreements_within_budget_pct": ("envelopes", "pct", 1, "Agreements within the per-installation budget",
                                     "Avtal inom budgeten per anläggning"),
    "capacity_over_hourly": ("envelopes", "ratio", 2, "Capacity with envelopes over the hourly study",
                             "Kapacitet med exportgränser mot den timvisa studien"),
    "capacity_over_fixed_cap": ("envelopes", "ratio", 2, "Capacity with envelopes over a fixed export cap",
                                "Kapacitet med exportgränser mot en fast exportgräns"),
    "discovery_conversations": ("owner", "count", 0, "Discovery conversations with DSOs",
                                "Samtal med nätbolag om deras behov"),
    "sales_pipeline": ("owner", "count", 0, "DSOs in the sales pipeline", "Nätbolag i säljprocessen"),
    "pilots_signed": ("owner", "count", 0, "Pilots or contracts signed", "Tecknade piloter eller avtal"),
    "onboarding_days": ("owner", "days", 0, "Time to onboard a DSO's network", "Tid för att ta in ett nätbolags nät"),
    "security_incidents": ("owner", "count", 0, "Security incidents", "Säkerhetsincidenter"),
}

# words around the figures, in both languages (no figure in any of them)
WORDS = {
    "en": {"metric": "Metric", "value": "Value", "period": "Period", "source": "Source",
           "missing": "not available: run `{command}`",
           "not_in_study": "not part of a study: run `{command}` on the installation",
           "no_reference": "not available: the reference capacity is zero or missing",
           "wrong_basis": "not available: the report is not on the day-ahead basis; run `{command}`",
           "by_rule": "by the rule, not counted",
           "owner": "entered by the owner", "days": "days", "pp": "pp", "hours": "h", "measured_hours": "metered hours",
           "of": "of", "compared": "compared", "review": "proposals for review not compared", "decided": "decided",
           "stations": "stations", "elements": "elements", "audits": "audits", "orders": "orders",
           "median_of": "median over", "worst": "worst", "against": "against", "rule": "sharing rule", "over": "over",
           "mv": {"measured": "at the measured MV", "band_low": "at the MV band's low end",
                  "band_high": "at the MV band's high end"},
           "demo": "This data folder ({site}). {origin}",
           "synthetic": "The network is synthetic, built on real streets, and the load is synthetic: the "
                        "figures are simulated, not measured.",
           "imported": "The network is the DSO's own (imported); the load and production are modelled.",
           "study": "{dso}, study {study}. The network data are the DSO's; load and production are modelled."},
    "sv": {"metric": "Nyckeltal", "value": "Värde", "period": "Period", "source": "Källa",
           "missing": "saknas: kör `{command}`",
           "not_in_study": "ingår inte i en studie: kör `{command}` på installationen",
           "no_reference": "saknas: referenskapaciteten är noll eller saknas",
           "wrong_basis": "saknas: rapporten gäller inte dagen-före-basen; kör `{command}`",
           "by_rule": "av regeln, ingår inte",
           "owner": "anges av ägaren", "days": "dagar", "pp": "procentenheter", "hours": "h",
           "measured_hours": "uppmätta timmar",
           "of": "av", "compared": "jämförda", "review": "förslag om prövning jämförs inte", "decided": "beslutade",
           "stations": "stationer", "elements": "element", "audits": "granskningar", "orders": "ordningar",
           "median_of": "median över", "worst": "sämsta", "against": "mot", "rule": "fördelningsregel", "over": "över",
           "mv": {"measured": "vid uppmätt mellanspänning", "band_low": "vid spänningsbandets nedre ände",
                  "band_high": "vid spänningsbandets övre ände"},
           "demo": "Den här datamappen ({site}). {origin}",
           "synthetic": "Nätet är syntetiskt, byggt på verkliga gator, och lasten är syntetisk: siffrorna är "
                        "simulerade, inte uppmätta.",
           "imported": "Nätet är nätbolagets eget (importerat); last och produktion är modellerade.",
           "study": "{dso}, studie {study}. Nätdata är nätbolagets; last och produktion är modellerade."},
}

_ID = re.compile(r"[0-9a-f]{12}")
DECIDED = ("firm", "nonfirm", "declined")
COMPARED = ("firm", "nonfirm")        # a "review" proposal leaves the decision to a person: nothing to agree with


class MetricsError(ValueError):
    pass


# ---------------------------------------------------------------- one metric

def _metric(mid: str, status: str, value: float | int | None = None, source: str = "", period: dict | None = None,
            detail: dict | None = None, command: str | None = None, reason: str | None = None) -> dict:
    group, unit, decimals, en, sv = DEFINITIONS[mid]
    if value is not None:
        value = float(value)
        if not math.isfinite(value):
            raise MetricsError(f"{mid}: not a finite value")
        value = int(value) if decimals == 0 and value.is_integer() else value
    out = {"id": mid, "group": group, "unit": unit, "decimals": decimals, "label": {"en": en, "sv": sv},
           "status": status, "value": value, "source": source, "period": period, "detail": detail or {}}
    if command is not None:
        out["command"] = command
    if reason is not None:
        out["reason"] = reason
    return out


def ok(mid: str, value, source: str, period: dict | None, **detail) -> dict:
    if value is None:
        raise MetricsError(f"{mid}: an available metric needs a value")
    return _metric(mid, "ok", value, source, period, detail)


def missing(mid: str, source: str, command: str, reason: str = "missing") -> dict:
    """A metric whose source is not there (or holds nothing to measure yet): never a value."""
    return _metric(mid, "not_available", source=source, command=command, reason=reason)


def outside_study(mid: str, source: str) -> dict:
    """A metric of the daily operation of conditional agreements, which a study does not have."""
    return _metric(mid, "not_available", source=source, command=METRICS, reason="not_in_study")


def owner(mid: str) -> dict:
    return _metric(mid, "entered_by_owner")


# ---------------------------------------------------------------- reading

def _read(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _median(values: list[float]) -> float | None:
    v = sorted(float(x) for x in values if x is not None)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def _day(iso: str | None) -> str | None:
    return str(iso)[:10] if iso else None


def _span(stamps: list[str]) -> dict | None:
    days = sorted(d for d in (_day(s) for s in stamps) if d)
    return {"from": days[0], "to": days[-1]} if days else None


def _years(doc: dict) -> dict | None:
    ys = sorted({int(y) for y in (doc.get("years") or [])})
    return {"years": ys} if ys else None


# ---------------------------------------------------------------- use

def _index_lines(index: list) -> list[dict]:
    return [x for x in index if isinstance(x, dict) and _ID.fullmatch(str(x.get("id", "")))]


def assessments(lines: list[dict], source: str, command: str) -> dict:
    mid = "assessments_prechecked"
    if not lines:
        return missing(mid, source, command, "no_records")
    return ok(mid, len(lines), source, _span([x.get("created") for x in lines]))


def application_metrics(events: list[dict], source: str) -> list[dict]:
    """Agreement of the rule's proposal with the DSO's decision, and the median days from
    application to decision, from the applications log as the inbox folds it."""
    from .api import inbox

    apps = inbox.fold(events)
    compared = [a for a in apps.values() if a["status"] in DECIDED and a.get("by") == "dso"
                and a.get("proposal") in COMPARED]
    review = sum(1 for a in apps.values() if a["status"] in DECIDED and a.get("by") == "dso"
                 and a.get("proposal") == "review")
    out = []
    if compared:
        agree = sum(1 for a in compared if a["status"] == a["proposal"])
        out.append(ok("proposal_agreement_pct", 100.0 * agree / len(compared), source,
                      _span([a["received"] for a in compared]), agreed=agree, compared=len(compared),
                      review_not_compared=review))
    else:
        out.append(missing("proposal_agreement_pct", source, SERVE, "no_decisions"))
    k = inbox.kpis(apps)
    # only a decision a person took has a wait worth reporting: the rule decides within seconds
    decided = [a for a in apps.values() if a["status"] in DECIDED and a.get("decided") and a.get("by") == "dso"]
    if decided:
        waits = [(datetime.fromisoformat(a["decided"]) - datetime.fromisoformat(a["received"])).total_seconds()
                 for a in decided]
        out.append(ok("median_days_to_decision", _median(waits) / 86400.0, source,
                      _span([a["received"] for a in decided] + [a["decided"] for a in decided]),
                      decided=len(decided), decided_by_rule=k["decided_by_rule"],
                      all_decisions_median_days=None if k["median_s"] is None else k["median_s"] / 86400.0,
                      p90_days=float(np.percentile(waits, 90)) / 86400.0))
    else:
        out.append(missing("median_days_to_decision", source, SERVE, "no_decisions"))
    return out


def case_agreement(rows: list[dict], source: str) -> dict:
    """The case batches' proposals against decision_dso (study cases --compare-decisions)."""
    mid = "proposal_agreement_pct"
    both = [r for r in rows if not r.get("error") and r.get("proposal") and r.get("decision_dso")]
    compared = [r for r in both if r["proposal"] in COMPARED]
    if not compared:
        return missing(mid, source, CASES + " --compare-decisions", "no_decisions")
    agree = sum(1 for r in compared if r["decision_dso"].strip().lower() == r["proposal"])
    return ok(mid, 100.0 * agree / len(compared), source, _span([r.get("received_at") for r in compared]),
              agreed=agree, compared=len(compared), review_not_compared=len(both) - len(compared))


# ---------------------------------------------------------------- model quality

def pool(blocks: list[dict]) -> dict | None:
    """Error statistics of several samples as one (validation_measured.stats), weighted by their n:
    bias and MAE as weighted means, RMSE from the weighted mean square, max the largest. The p95
    cannot be pooled and is left out."""
    got = [b for b in blocks if b and int(b.get("n") or 0) > 0 and b.get("rmse") is not None]
    if not got:
        return None
    n = sum(int(b["n"]) for b in got)

    def mean(key: str) -> float:
        return sum(int(b["n"]) * float(b[key]) for b in got) / n

    return {"n": n, "bias": mean("bias"), "mae": mean("mae"),
            "rmse": math.sqrt(sum(int(b["n"]) * float(b["rmse"]) ** 2 for b in got) / n),
            "max": max(float(b["max"]) for b in got)}


def _headline(bases: dict[str, dict]) -> tuple[str, dict]:
    """The measured MV when the stations gave one, else the worse end of the band."""
    if "measured" in bases:
        return "measured", bases["measured"]
    name = max(bases, key=lambda k: bases[k]["rmse"])
    return name, bases[name]


def model_metrics(docs: list[tuple[str, dict]], source: str, command: str) -> list[dict]:
    """Voltage and transformer error against measurements, and the dpPower match, from every
    station's validation_measured.json."""
    out = []
    measured = [(sid, d["measured"]) for sid, d in docs if (d.get("measured") or {}).get("bases")]
    for mid, key in (("voltage_error_pu", "voltage_pu"), ("transformer_error_pp", "transformer_pct")):
        by_base: dict[str, list] = {}
        stations: dict[str, set] = {}
        for sid, m in measured:
            for name, b in m["bases"].items():
                if (b.get(key) or {}).get("n"):
                    by_base.setdefault(name, []).append(b[key])
                    stations.setdefault(name, set()).add(sid)
        bases = {k: p for k, v in by_base.items() if (p := pool(v)) is not None}
        if not bases:
            out.append(missing(mid, source, command, "no_measurements" if docs else "missing"))
            continue
        name, best = _headline(bases)
        hours = sum(int(m.get("hours_complete") or 0) for sid, m in measured if sid in stations[name])
        out.append(ok(mid, best["rmse"], source, {"hours": hours}, mv=name, stations=len(stations[name]),
                      bias=best["bias"], mae=best["mae"], max=best["max"], n=best["n"], bases=bases))
    rows = matched = 0
    hours: list[str] = []
    volt: dict[str, list] = {}
    snap_stations = set()
    for sid, d in docs:
        for stamp, runs in (d.get("dppower") or {}).items():
            if not isinstance(runs, dict):
                continue
            cmp = [v for k, v in runs.items() if isinstance(v, dict) and k != "metered_injection"]
            if not cmp:
                continue
            snap_stations.add(sid)
            hours.append(stamp)
            rows += int(cmp[0].get("rows") or 0)
            matched += int(cmp[0].get("matched") or 0)
            for k, v in runs.items():
                if isinstance(v, dict) and (v.get("voltage_pu") or {}).get("n"):
                    volt.setdefault(k, []).append(v["voltage_pu"])
    if rows:
        out.append(ok("dppower_matched_pct", 100.0 * matched / rows, source, _span(hours), matched=matched,
                      elements=rows, stations=len(snap_stations)))
    else:
        out.append(missing("dppower_matched_pct", source, command, "no_snapshot" if docs else "missing"))
    bases = {k: p for k, v in volt.items() if (p := pool(v)) is not None}
    if bases:
        name, best = _headline(bases)
        out.append(ok("dppower_voltage_pu", best["rmse"], source, _span(hours), mv=name, bias=best["bias"],
                      mae=best["mae"], max=best["max"], n=best["n"], bases=bases))
    else:
        out.append(missing("dppower_voltage_pu", source, command, "no_snapshot" if docs else "missing"))
    return out


# ---------------------------------------------------------------- envelopes (the installation)

def envelope_metrics(res: Path) -> list[dict]:
    out = []
    src = "results/redispatch.json"
    r = _read(res / "redispatch.json")
    if r is None or r.get("hours") is None:
        out += [missing("envelope_hours_activated", src, REDISPATCH), missing("envelope_hours_needed", src, REDISPATCH)]
    elif r.get("basis") != "dayahead":        # the labels say day-ahead: never publish another basis under them
        out += [missing(m, src, REDISPATCH, "wrong_basis") for m in ("envelope_hours_activated", "envelope_hours_needed")]
    else:
        period = {"year": int(r["year"])} if r.get("year") is not None else None
        out.append(ok("envelope_hours_activated", r["hours"], src, period, basis=r.get("basis"), policy=r.get("policy"),
                      hours_needed=r.get("hours_needed"), hours_not_needed=r.get("hours_not_needed")))
        out.append(ok("envelope_hours_needed", r.get("hours_needed") or 0, src, period, basis=r.get("basis"),
                      policy=r.get("policy"), hours_activated=r["hours"]))
    for mid, name in (("dayahead_bound_exceeded_hours", "foresight.json"), ("intraday_bound_exceeded_hours", "intraday.json")):
        src = f"results/{name}"
        d = _read(res / name)
        pe = (d or {}).get("plan_exceedance") or {}
        pe = pe.get("total", pe)
        if d is None or pe.get("pv_aggregate_hours_over") is None:
            out.append(missing(mid, src, EXTRA + name.removesuffix(".json")))
            continue
        out.append(ok(mid, pe["pv_aggregate_hours_over"], src, _years(d), audits=pe.get("audits"),
                      customer_hours_over=pe.get("pv_customer_hours_over"),
                      worst_aggregate_kw=pe.get("pv_worst_aggregate_kw"),
                      margins=d.get("margins"), variant=d.get("headline_variant")))
    src = "results/hosting_summary.json"
    h = _read(res / "hosting_summary.json")
    if h is None:
        return out + [missing(m, src, COMPUTE) for m in
                      ("agreements_within_budget_pct", "capacity_over_hourly", "capacity_over_fixed_cap")]
    per = [o.get("customers_within_pct") for o in (h.get("per_order") or []) if o.get("customers_within_pct") is not None]
    if per:
        out.append(ok("agreements_within_budget_pct", _median(per), src, _years(h), orders=len(per), worst=min(per),
                      z_star_pct=h.get("z_star_pct"), headline_budget=(h.get("budget_basis") or {}).get("doe")))
    else:
        out.append(missing("agreements_within_budget_pct", src, COMPUTE))
    m = h.get("measures") or {}

    def kw(name: str) -> float | None:
        return ((m.get(name) or {}).get("kw_ac") or {}).get("median")

    for mid, base in (("capacity_over_hourly", "timeseries"), ("capacity_over_fixed_cap", "fixed_cap")):
        doe, ref = kw("doe"), kw(base)
        if doe is None or not ref:
            # a zero or absent reference is not something `compute` would fix
            out.append(missing(mid, src, COMPUTE, "no_reference" if doe is not None else "missing"))
            continue
        out.append(ok(mid, doe / ref, src, _years(h), envelope_kw=doe, reference_kw=ref, reference=base,
                      orders=h.get("n_orders")))
    return out


# ---------------------------------------------------------------- a study

def _station_capacity(study) -> list[dict]:
    out = []
    for sid in study.station_ids():
        c = _read(study.station_dir(sid) / "processed" / "results" / "capacity.json")
        if c is not None:
            out.append({"sid": sid, **c})
    return out


def study_capacity(study) -> dict:
    """The study's capacity with envelopes over its hourly capacity, summed over the stations that
    have both (a station without approved conditional agreements has no envelope figure)."""
    mid, src = "capacity_over_hourly", "stations/*/processed/results/capacity.json"
    both = []
    for c in _station_capacity(study):
        g = c.get("generation") or {}
        d = g.get("doe") or {}
        if g.get("status") == "ok" and d.get("status") == "ok" and g.get("kw") and d.get("kw") is not None:
            both.append((c, float(d["kw"]), float(g["kw"])))
    if not both:
        return missing(mid, src, STUDY_BUILD, "no_envelopes")
    doe, ref = sum(x[1] for x in both), sum(x[2] for x in both)
    years = sorted({int(y) for c, *_ in both for y in (c.get("years") or [])})
    return ok(mid, doe / ref, src, {"years": years} if years else None, envelope_kw=doe, reference_kw=ref,
              reference="timeseries", stations=len(both))


def _study_metrics(study) -> list[dict]:
    from .store.files import Records
    from .study import csvio

    out = []
    lines: list[dict] = []
    for sid in study.station_ids():
        lines += _index_lines(Records(study.station_dir(sid) / "requests").index())
    out.append(assessments(lines, "stations/*/requests/index.jsonl", CASES))
    rows: list[dict] = []
    for path in sorted((study.root / "cases").glob(f"*/verdicts{csvio.MACHINE}")):
        rows += csvio.read(path)
    out.append(case_agreement(rows, "cases/*/verdicts.machine.csv"))
    out.append(outside_study("median_days_to_decision", "requests/applications.jsonl"))
    docs = [(sid, d) for sid in study.station_ids()
            if (d := _read(study.station_dir(sid) / "processed" / "results" / "validation_measured.json")) is not None]
    out += model_metrics(docs, "stations/*/processed/results/validation_measured.json", VALIDATE)
    out += [outside_study("envelope_hours_activated", "results/redispatch.json"),
            outside_study("envelope_hours_needed", "results/redispatch.json"),
            outside_study("dayahead_bound_exceeded_hours", "results/foresight.json"),
            outside_study("intraday_bound_exceeded_hours", "results/intraday.json"),
            outside_study("agreements_within_budget_pct", "results/hosting_summary.json"),
            study_capacity(study),
            outside_study("capacity_over_fixed_cap", "results/hosting_summary.json")]
    return out


# ---------------------------------------------------------------- the document

def build(study_dir: str | Path | None = None, *, data_dir: Path | None = None, now: str | None = None) -> dict:
    """metrics.json's document for this data folder (or `data_dir`), or for a study."""
    from . import paths, procs, store

    computed_at = now or procs.now()
    if study_dir is None:
        from .dossier import _flags

        data = Path(data_dir) if data_dir is not None else Path(paths.DATA)
        proc = data / "processed"
        res = proc / "results"
        req = data / "requests"
        meta = _read(res / "meta.json") or {}
        site = meta.get("site") or (_read(proc / "grid" / "summary.json") or {}).get("site") or "–"
        imported = _flags(proc)["imported"]
        lines = _index_lines(store.records(req).index())
        events = store.applications(req / "applications.jsonl").entries()
        metrics = [assessments(lines, "requests/index.jsonl", SERVE),
                   *application_metrics(events, "requests/applications.jsonl")]
        val = _read(res / "validation_measured.json")
        metrics += model_metrics([("this", val)] if val is not None else [], "results/validation_measured.json", VALIDATE)
        metrics += envelope_metrics(res)
        subject = {"kind": "demo", "site": site, "imported": imported}
        source = str(data)
    else:
        from .study import open_study

        study = open_study(study_dir, allow_git=True)
        metrics = _study_metrics(study)
        subject = {"kind": "study", "dso": study.config.dso.name or study.id, "study": study.id}
        source = str(study.root)
    metrics += [owner(mid) for mid, d in DEFINITIONS.items() if d[0] == "owner"]
    order = list(DEFINITIONS)
    metrics.sort(key=lambda m: order.index(m["id"]))
    return {"schema": SCHEMA, "kind": subject["kind"], "computed_at": computed_at, "source": source,
            "subject": subject, "metrics": metrics,
            "not_available": [{"id": m["id"], "source": m["source"], "command": m["command"], "reason": m["reason"]}
                              for m in metrics if m["status"] == "not_available"],
            "entered_by_owner": [m["id"] for m in metrics if m["status"] == "entered_by_owner"]}


# ---------------------------------------------------------------- the two texts

def number(x: float | int | None, decimals: int, lang: str) -> str:
    """A number as each text writes it: Swedish as the glossary says (sv_number), English with a
    decimal point and a comma between thousands from five digits on."""
    if lang == "sv":
        return sv_number(x, decimals)
    if x is None:
        return "–"
    return sv_number(x, decimals).replace(",", ".").replace(" ", ",").replace("−", "-")


def _range(years: list[int]) -> str:
    ys = sorted(set(int(y) for y in years))
    if len(ys) > 2 and ys == list(range(ys[0], ys[-1] + 1)):
        return f"{ys[0]}–{ys[-1]}"
    return ", ".join(str(y) for y in ys)


def period_text(m: dict, lang: str) -> str:
    p = m.get("period") or {}
    w = WORDS[lang]
    if "from" in p:
        return p["from"] if p["from"] == p["to"] else f"{p['from']} – {p['to']}"
    if "year" in p:
        return str(p["year"])
    if "years" in p:
        return _range(p["years"])
    if "hours" in p:
        return f"{number(p['hours'], 0, lang)} {w['measured_hours']}"
    return "–"


def _detail(m: dict, lang: str) -> str:
    d, w = m["detail"], WORDS[lang]

    def n(x, decimals: int = 0) -> str:
        return number(x, decimals, lang)

    mid = m["id"]
    if mid == "proposal_agreement_pct":
        text = f"{n(d['agreed'])} {w['of']} {n(d['compared'])} {w['compared']}"
        if d.get("review_not_compared"):
            text += f"; {n(d['review_not_compared'])} {w['review']}"
        return text
    if mid == "median_days_to_decision":
        text = f"{n(d['decided'])} {w['decided']}"
        if d.get("decided_by_rule"):
            text += f"; {n(d['decided_by_rule'])} {w['by_rule']}"
        return text
    if mid in ("voltage_error_pu", "transformer_error_pp"):
        return f"{n(d['stations'])} {w['stations']}, {w['mv'].get(d['mv'], d['mv'])}"
    if mid == "dppower_matched_pct":
        return f"{n(d['matched'])} {w['of']} {n(d['elements'])} {w['elements']}"
    if mid == "dppower_voltage_pu":
        return w["mv"].get(d["mv"], d["mv"])
    if mid in ("envelope_hours_activated", "envelope_hours_needed") and d.get("policy"):
        return f"{w['rule']} {d['policy']}"
    if mid in ("dayahead_bound_exceeded_hours", "intraday_bound_exceeded_hours") and d.get("audits"):
        return f"{w['over']} {n(d['audits'])} {w['audits']}"
    if mid == "agreements_within_budget_pct":
        return f"{w['median_of']} {n(d['orders'])} {w['orders']}, {w['worst']} {n(d['worst'], 1)} %"
    if mid in ("capacity_over_hourly", "capacity_over_fixed_cap"):
        return f"{n(d['envelope_kw'])} kW {w['against']} {n(d['reference_kw'])} kW"
    return ""


def value_text(m: dict, lang: str) -> str:
    """A metric's value cell: the value with its unit and what it was computed over, or why there is none."""
    w = WORDS[lang]
    if m["status"] == "entered_by_owner":
        return w["owner"]
    if m["status"] == "not_available":
        key = m.get("reason") if m.get("reason") in ("not_in_study", "no_reference", "wrong_basis") else "missing"
        return w[key].format(command=m["command"])
    v = number(m["value"], m["decimals"], lang)
    unit = m["unit"]
    text = {"count": v, "pct": f"{v} %", "days": f"{v} {w['days']}", "pu": f"{v} p.u.", "pp": f"{v} {w['pp']}",
            "hours": f"{v} {w['hours']}", "ratio": f"×{v}"}[unit]
    extra = _detail(m, lang)
    return f"{text} ({extra})" if extra else text


def stamp_text(iso: str) -> str:
    """When the metrics were computed, to the minute, in UTC (procs.now)."""
    return iso[:16].replace("T", " ") + " UTC"


def subject_text(doc: dict, lang: str) -> str:
    s, w = doc["subject"], WORDS[lang]
    if s["kind"] == "study":
        return w["study"].format(dso=s["dso"], study=s["study"])
    return w["demo"].format(site=s["site"], origin=w["imported" if s.get("imported") else "synthetic"])


def table(metrics: list[dict], lang: str) -> str:
    w = WORDS[lang]
    rows = [f"| {w['metric']} | {w['value']} | {w['period']} | {w['source']} |", "|---|---|---|---|"]
    for m in metrics:
        source = f"`{m['source']}`" if m["source"] else "–"
        rows.append(f"| {m['label'][lang]} | {value_text(m, lang)} | {period_text(m, lang)} | {source} |")
    return "\n".join(rows)


def text(doc: dict, lang: str) -> str:
    """metrics.md (en) or metrics_sv.md (sv) from its template."""
    values = {"subject": subject_text(doc, lang), "computed_at": stamp_text(doc["computed_at"])}
    for g in GROUPS:
        values[g] = table([m for m in doc["metrics"] if m["group"] == g], lang)
    return render(TEMPLATES[lang].read_text(encoding="utf-8"), values)


def write(study_dir: str | Path | None = None, out: str | Path | None = None) -> dict[str, Path]:
    """Write metrics.json, metrics.md and metrics_sv.md: into the study's folder, or results/."""
    from . import paths
    from .atomic import write_json_atomic, write_text_atomic

    doc = build(study_dir)
    if out is not None:
        folder = Path(out)
    elif study_dir is not None:
        folder = Path(doc["source"])
    else:
        folder = paths.processed("results", JSON_NAME).parent
    folder.mkdir(parents=True, exist_ok=True)
    write_json_atomic(folder / JSON_NAME, doc)
    written = {"json": folder / JSON_NAME}
    for lang, name in TEXT_NAMES.items():
        write_text_atomic(folder / name, text(doc, lang))
        written[lang] = folder / name
    return written


__all__ = ["DEFINITIONS", "JSON_NAME", "SCHEMA", "TEMPLATES", "TEXT_NAMES", "build", "number", "period_text",
           "pool", "text", "value_text", "write"]
