"""Module 4's start: per station, its place in the portfolio and the choice to reinforce, connect
with conditions or wait. Both read one collected quarter (runs/<Q>/stations.parquet).

    kapacitet study portfolio <dir> --run 2026Q4
        runs/<Q>/portfolio.csv (+ .machine.csv)   one row per station, the least margin per customer
                                                   first (`rank`), with what the decision rests on
        runs/<Q>/portfolio_sv.md                   the same in Swedish (templates/portfolio_sv.md)

    kapacitet study options <dir> --run 2026Q4
        stations/<sid>/options/<Q>.json            what each station's own process computed
                                                   (`kapacitet station options`), reused while the
                                                   station's build and the method approval are the same
        runs/<Q>/options.csv (+ .machine.csv)      one row per station: the three options
        runs/<Q>/options/<sid>.sv.md               a Swedish note per station (templates/options_sv.md)

The portfolio, per station:
- the margins: the published generation and consumption capacity (after the upstream caps, with
  the reviewer's corrections in runs/<Q>/corrections.csv, which can only lower them), and each per
  customer;
- the risk: the generation capacity at each end of the MV band and the end that binds, the hours
  today's grid already breaks a limit, the verification, and the measured peak of the metered
  customers against Velander's estimate for the same customers (peak_check.json);
- the trend: the cases received and not known to be decided (cases/<batch>/verdicts.csv without
  decision_dso, and sync reports' pending rows, less every case a sync report lists as approved),
  the existing equipment with its connection dates when the equipment table has them, and the
  future scenarios when the station has a scenarios.json;
- `rank`: by the smaller of the two margins per customer, the least first; a station without a
  figure on either side comes first, and ties go by station id, so the ranking is deterministic.

The options, per station:
- Reinforce now: the next of pandapower's standard transformers for the same voltages (as a case's
  reinforcement draft, study/cases.next_transformer), with the station's capacities computed again
  on it by its own process, at both ends of the band for generation and at the low end for
  consumption, hourly over the build's weather years and orders. Priced at the DSO's own contract
  price in study.toml [costs] transformer_swap_sek; Ei's norm values are given beside it only as a
  labelled reference, never as the price.
- Connect with conditions: the station's envelopes at the per-installation curtailment budget (the
  build's when its study uses that budget, otherwise computed again by the station's process), only
  with the method's approval and only when they verify on every hour; the expected curtailment is
  their mean curtailed energy a year, valued at [costs] curtailment_sek_per_kwh.
- Wait: the value of deferring the swap [costs] deferral_years (one regulatory period by default)
  at Ei's real rates in config/assumptions.toml (summaries.RATES), against the curtailment meanwhile,
  with summaries.deferral_case and years_of_curtailment, the same arithmetic as /business.

A price left out is "not entered", never guessed, and an option is recommended only with every
input it rests on (`why_none` lists what is missing). The recommendation is a proposal for a person:
- "wait" when nobody is waiting (no pending case) and both margins are above zero;
- otherwise "condition" when the envelopes verify and connecting with them is worth more than
  reinforcing now at the rate in force (the net of deferral and curtailment above zero), and
  "reinforce" when it is not, or when there are no verified envelopes to connect with;
- none when the bigger transformer adds no capacity on either side: the limit is not the
  transformer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .. import procs
from ..atomic import write_json_atomic, write_text_atomic
from . import Study, StudyError
from . import csvio
from .config import METHOD_VERSION

OPTIONS_SCHEMA = "kapacitet.station-options/1"
TEMPLATES = Path(__file__).with_name("templates")
PORTFOLIO_TEMPLATE = TEMPLATES / "portfolio_sv.md"
OPTIONS_TEMPLATE = TEMPLATES / "options_sv.md"
NOT_ENTERED = "not entered"
# the rates of summaries.RATES, as the columns name them
RATE_COLUMNS = {"ei_wacc_real_pct": "in_force", "ei_wacc_real_pct_2028_prelim": "2028_proposed",
                "ei_wacc_real_pct_2028_current_method": "2028_current"}
IN_FORCE = "ei_wacc_real_pct"
RECOMMENDATIONS = ("reinforce", "condition", "wait")

PORTFOLIO_COLUMNS = [
    "rank", "station_id", "name", "status", "customers", "zone", "hidden",
    "generation_kw", "consumption_device", "consumption_kw", "corrected",
    "generation_kw_per_customer", "consumption_kw_per_customer", "margin_kw_per_customer",
    "binding_mv_pu", "mv_low_pu", "mv_high_pu", "generation_kw_mv_low", "generation_kw_mv_high",
    "generation_baseline_violation_hours", "consumption_baseline_violation_hours", "full_year_violations",
    "velander_kw", "modelled_peak_kw", "measured_customers", "measured_peak_kw", "measured_velander_kw",
    "measured_peak_ratio",
    "pending_cases", "equipment_rows", "equipment_pv_kwp", "equipment_dated", "equipment_first_connected_on",
    "equipment_last_connected_on", "equipment_per_year",
    "scenarios_year", "scenarios_cases", "scenarios_static_ok", "scenarios_envelopes_ok",
    "flags",
]
OPTIONS_COLUMNS = [
    "rank", "station_id", "name", "customers", "pending_cases", "generation_kw", "consumption_kw",
    "reinforce_status", "trafo_now_type", "trafo_now_kva", "trafo_next_type", "trafo_next_kva",
    "reinforce_generation_kw", "reinforce_generation_limit", "reinforce_consumption_kw",
    "reinforce_generation_gain_kw", "reinforce_consumption_gain_kw", "reinforce_violations",
    "reinforce_cost_status", "reinforce_cost_sek", "cost_price_level_year", "cost_source",
    "reference_ei_500_kva_sek", "reference_ei_800_kva_sek", "reference_ei_price_level_year",
    "condition_status", "condition_budget", "condition_kw", "condition_adopters", "condition_z_pct",
    "condition_z_customer_max_pct", "condition_curtailed_kwh_per_year", "curtailment_price_status",
    "condition_curtailment_sek_per_year", "years_of_curtailment", "wait_years",
    *(f"rate_{c}_pct" for c in RATE_COLUMNS.values()),
    *(f"wait_deferral_sek_{c}" for c in RATE_COLUMNS.values()),
    *(f"condition_net_sek_{c}" for c in RATE_COLUMNS.values()),
    "recommendation", "why_none",
]

# Every Swedish sentence the notes are made of: the figures go in through the {slots}, written the
# Swedish way (publish.sv), so no number is typed here (tests/test_study_decision.py).
SV = {
    "not_entered": "inte angivet",
    "not_computed": "inte beräknat",
    "none": "inget",
    "kw": "{v} kW",
    "sek": "{v} SEK",
    "rec.reinforce": "förstärk nu",
    "rec.condition": "anslut med villkor",
    "rec.wait": "vänta",
    "rec.none": "inget förslag: {why}",
    "why.reinforce_not_computed": "förstärkningen är inte beräknad (kör `kapacitet study options`)",
    "why.reinforce_failed": "beräkningen av förstärkningen misslyckades (se stationens build.log)",
    "why.reinforce_not_verified": "förstärkningen klarade inte kontrollen på varje timme",
    "why.no_larger_unit": "det finns ingen större standardtransformator för samma spänningar",
    "why.cost_not_entered": "nätföretagets pris för bytet är inte angivet i [costs]",
    "why.curtailment_price_not_entered": "priset för bortkopplad energi är inte angivet i [costs]",
    "why.no_gain": "en större transformator ger inget utrymme: det är inte transformatorn som begränsar",
    "reinforce.ok": ("Nästa transformator, {next}, i stället för dagens {now}. Kontrollerad timme för timme över "
                     "{years} väderår, för produktion vid båda ändar av mellanspänningsbandet och för förbrukning "
                     "vid den lägre: utrymme för ny produktion {gen} (i dag {gen_now}, gräns efter bytet: {limit}) "
                     "och för ny förbrukning {cons} (i dag {cons_now}). Pris enligt nätföretaget: {cost}."),
    "reinforce.not_verified": ("Nästa transformator, {next}, i stället för dagens {now}: beräkningen klarade inte "
                               "kontrollen på varje timme, så den redovisas inte som förstärkning."),
    "reinforce.no_larger_unit": "Det finns ingen större standardtransformator för samma spänningar som dagens {now}.",
    "reinforce.not_computed": "Förstärkningen är inte beräknad för stationen.",
    "reinforce.failed": "Beräkningen av förstärkningen misslyckades: se stationens build.log.",
    "condition.ok": ("Med villkorad anslutning (styrning med dynamiska gränser, budget per anläggning) ryms {kw}, "
                     "kontrollerat på varje timme. Förväntad bortkoppling: {kwh} kWh per år, {z} % av "
                     "produktionen som helhet och högst {zmax} % för en enskild anläggning; värderat till {sek} "
                     "per år."),
    "condition.not_approved": ("Villkorad anslutning beräknas inte: nätföretaget har inte godkänt metoden "
                               "([method] approval_reference)."),
    "condition.not_verified": "Villkorad anslutning redovisas inte: gränserna klarade inte kontrollen på varje timme.",
    "condition.other": "Villkorad anslutning redovisas inte ({status}).",
    "wait.line": "{rate}, {pct} %: att skjuta upp bytet i {years} år är värt {deferral}; netto efter bortkopplingen {net}.",
    "wait.no_cost": "Värdet av att vänta kan inte anges: priset för bytet är inte angivet.",
    "pending": "{n} ärenden väntar på beslut.",
    "reference": "Ei:s normvärden ({year}), endast som referens: {lo} för en transformator på femhundra kVA, {hi} för en på åttahundra kVA.",
    "costs_source": "Nätföretagets priser: {source} (prisnivå {year}).",
    "costs_none": "Nätföretagets priser är inte angivna i study.toml [costs].",
    "portfolio.header": ("Rang | Nätstation | Kunder | Produktion (kW) | Förbrukning (kW) | Marginal per kund (kW) | "
                         "Produktion vid låg / hög mellanspänning (kW) | Timmar med överträdelse i dag | "
                         "Uppmätt topp mot Velander | Väntande ärenden | Befintlig utrustning (senast ansluten) | "
                         "Framtidsscenarier klarade statiskt / med dynamiska gränser"),
    "portfolio.scenarios": "{static} / {doe} av {n}",
    "limit.voltage": "spänningen",
    "limit.cable": "en kabel",
    "limit.transformer": "transformatorn",
    "limit.nonconverged": "lastflödet konvergerar inte",
    "limit.candidates": "ingen, alla tänkbara anläggningar ryms",
    "yes": "ja",
    "no": "nej",
}
PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
SLOT = re.compile(r"\{[a-z_]+\}")


# ---------------------------------------------------------------- the portfolio

def pending_cases(study: Study) -> dict[str, int]:
    """{station: cases received and not known to be decided}, from every batch under cases/."""
    root = study.root / "cases"
    decided: set[str] = set()
    seen: dict[str, str] = {}
    for batch in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        for r in csvio.read(batch / f"sync_report{csvio.MACHINE}"):
            if r.get("status") == "approved":
                decided.add(r.get("case_id", ""))
            elif r.get("status") == "pending" and r.get("station"):
                seen[r["case_id"]] = r["station"]
        for r in csvio.read(batch / f"verdicts{csvio.MACHINE}"):
            if r.get("error") or not r.get("station"):
                continue
            if r.get("decision_dso"):
                decided.add(r["case_id"])
            else:
                seen.setdefault(r["case_id"], r["station"])
    out: dict[str, int] = {}
    for cid, sid in sorted(seen.items()):
        if cid not in decided:
            out[sid] = out.get(sid, 0) + 1
    return out


def equipment_trend(study: Study, sid: str) -> dict:
    """What is already connected behind the station's meters, and when (equipment.csv)."""
    import pandas as pd

    from .intake import station_equipment

    eq, _ = station_equipment(study, sid)
    if eq is None:
        return {}
    out = {"equipment_rows": int(len(eq))}
    if "pv_kwp" in eq.columns:
        out["equipment_pv_kwp"] = float(pd.to_numeric(eq["pv_kwp"].astype(str).str.replace(",", "."),
                                                      errors="coerce").fillna(0).sum())
    if "connected_on" in eq.columns:
        when = pd.to_datetime(eq["connected_on"].astype(str).str.strip(), format="%Y-%m-%d", errors="coerce").dropna()
        out["equipment_dated"] = int(len(when))
        if len(when):
            out["equipment_first_connected_on"] = when.min().date().isoformat()
            out["equipment_last_connected_on"] = when.max().date().isoformat()
            per = when.dt.year.value_counts().sort_index()
            out["equipment_per_year"] = "; ".join(f"{int(y)}: {int(n)}" for y, n in per.items())
    return out


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}


def _per(kw, customers) -> float | None:
    return float(kw) / customers if kw is not None and customers else None


def station_facts(study: Study, row: dict, fix: dict, pending: dict[str, int]) -> dict:
    """One station's portfolio row, without its rank."""
    from .publish import _effective
    from .review import flags

    sid = row["station_id"]
    results = study.station_dir(sid) / "processed" / "results"
    cap = _json(results / "capacity.json")
    gen_kw, cons_kw = _effective(row, fix, "generation"), _effective(row, fix, "consumption")
    n = row.get("customers")
    g, c = _per(gen_kw, n), _per(cons_kw, n)
    out = {name: None for name in PORTFOLIO_COLUMNS}
    out.update({k: row.get(k) for k in ("station_id", "name", "status", "customers", "zone", "hidden",
                                         "consumption_device", "binding_mv_pu", "generation_baseline_violation_hours",
                                         "consumption_baseline_violation_hours", "full_year_violations")})
    out.update(generation_kw=gen_kw, consumption_kw=cons_kw,
               corrected="generation_kw" in fix or "consumption_kw" in fix,
               generation_kw_per_customer=g, consumption_kw_per_customer=c,
               margin_kw_per_customer=None if g is None or c is None else min(g, c),
               pending_cases=pending.get(sid, 0))
    band = cap.get("mv_band_pu") or []
    if band:
        lo, hi = float(min(band)), float(max(band))
        by_end = (cap.get("generation") or {}).get("by_end") or {}
        out.update(mv_low_pu=lo, mv_high_pu=hi, generation_kw_mv_low=(by_end.get(f"{lo:.2f}") or {}).get("kw"),
                   generation_kw_mv_high=(by_end.get(f"{hi:.2f}") or {}).get("kw"))
    peak = _json(results / "peak_check.json") or cap.get("peak_check") or {}
    out.update(velander_kw=peak.get("velander_kw"), modelled_peak_kw=peak.get("modelled_peak_kw"))
    measured = peak.get("measured") or {}
    if measured.get("max_kw") is not None:
        ref = measured.get("velander_kw")
        out.update(measured_customers=measured.get("customers"), measured_peak_kw=measured["max_kw"],
                   measured_velander_kw=ref, measured_peak_ratio=measured["max_kw"] / ref if ref else None)
    out.update(equipment_trend(study, sid))
    scen = _json(results / "scenarios.json")
    if scen.get("cases"):
        cases = scen["cases"]
        out.update(scenarios_year=scen.get("year"), scenarios_cases=len(cases),
                   scenarios_static_ok=sum(1 for x in cases if x.get("static_ok")),
                   scenarios_envelopes_ok=sum(1 for x in cases if x.get("doe_ok")))
    risk = flags(row, study.config.dso.method_version)
    if (out["measured_peak_ratio"] or 0) > 1:
        risk.append("measured_above_velander")
    if out["pending_cases"]:
        risk.append("pending_cases")
    out["flags"] = ",".join(risk) or None
    return out


def rank(rows: list[dict]) -> list[dict]:
    """The rows by margin per customer, the least first (None first), ties by station id; each with
    its `rank` from one."""
    ordered = sorted(rows, key=lambda r: (r["margin_kw_per_customer"] is not None,
                                          r["margin_kw_per_customer"] or 0.0, r["station_id"]))
    return [{**r, "rank": i} for i, r in enumerate(ordered, start=1)]


def portfolio_rows(study: Study, q: str) -> list[dict]:
    from .review import corrections, load_rows

    rows = load_rows(study, q)
    fixes, _ = corrections(study, q, rows)
    pending = pending_cases(study)
    return rank([station_facts(study, r, fixes.get(r["station_id"], {}), pending) for r in rows])


def sv_kw(x, decimals: int = 1) -> str:
    from .publish import sv

    return SV["not_computed"] if x is None else SV["kw"].format(v=sv(x, decimals))


def sv_sek(x) -> str:
    """Money the Swedish way: whole SEK, thousands apart by a no-break space, a true minus."""
    if x is None:
        return SV["not_entered"]
    text = f"{abs(round(float(x))):,}".replace(",", "\u00a0")
    return SV["sek"].format(v=("−" if round(float(x)) < 0 else "") + text)


def _cell(x, decimals: int = 1) -> str:
    from .publish import sv

    if x is None:
        return "–"
    if isinstance(x, bool):
        return SV["yes"] if x else SV["no"]
    if isinstance(x, (int, float)):
        return sv(x, decimals)
    return str(x).replace("|", "/")


def portfolio_markdown(study: Study, q: str, rows: list[dict]) -> str:
    # internal to the DSO: hidden stations (disclosure k) are listed here; never part of the published files
    from .publish import render

    header = SV["portfolio.header"].split(" | ")
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for r in rows:
        viol = sum(v or 0 for v in (r["generation_baseline_violation_hours"], r["consumption_baseline_violation_hours"]))
        scen = (SV["portfolio.scenarios"].format(static=r["scenarios_static_ok"], doe=r["scenarios_envelopes_ok"],
                                                 n=r["scenarios_cases"]) if r["scenarios_cases"] else "–")
        equip = (f"{r['equipment_rows']} ({_cell(r['equipment_last_connected_on'])})"
                 if r["equipment_rows"] is not None else "–")
        lines.append("| " + " | ".join([
            str(r["rank"]), _cell(r["name"]), _cell(r["customers"]), _cell(r["generation_kw"]),
            _cell(r["consumption_kw"]), _cell(r["margin_kw_per_customer"], 2),
            f"{_cell(r['generation_kw_mv_low'])} / {_cell(r['generation_kw_mv_high'])}", _cell(viol),
            _cell(r["measured_peak_ratio"], 2), _cell(r["pending_cases"]), equip, scen]) + " |")
    device = {"ev": "laddbox för elbil", "heat_pump": "värmepump"}.get(study.config.method.consumption_device,
                                                                       study.config.method.consumption_device)
    return render(PORTFOLIO_TEMPLATE.read_text(encoding="utf-8"), {
        "dso_name": study.config.dso.name or study.id, "quarter": q, "stations": str(len(rows)),
        "device": device, "table": "\n".join(lines)})


def portfolio(study: Study, q: str) -> dict:
    """runs/<q>/portfolio.csv, its machine copy and portfolio_sv.md."""
    from .review import run_dir

    rows = portfolio_rows(study, q)
    folder = run_dir(study, q)
    paths = csvio.write(folder, "portfolio", rows, PORTFOLIO_COLUMNS, study.config.output.csv_dialect)
    md = folder / "portfolio_sv.md"
    write_text_atomic(md, portfolio_markdown(study, q, rows))
    return {"rows": rows, "paths": [*paths, md], "quarter": q}


# ---------------------------------------------------------------- inside a station's process

def _brief(block: dict) -> dict:
    return {"status": block.get("status"), "kw": block.get("kw"), "binding_mv_pu": block.get("binding_mv_pu"),
            "limit": ((block.get("binding") or {}).get("limit")),
            "full_year_violations": (block.get("verification") or {}).get("full_year_violations"),
            "nonconverged_hours": (block.get("verification") or {}).get("nonconverged_hours"),
            "by_end": {k: (v or {}).get("kw") for k, v in (block.get("by_end") or {}).items()} or None}


def reusable_doe(doe: dict, ref: str) -> bool:
    """capacity.json's DOE block stands in for a recomputation only when it verified, at the
    per-installation budget, with the curtailment, under the same approval."""
    return bool(doe.get("status") == "ok" and doe.get("budget") == "installation"
                and "curtailed_kwh_per_year" in doe and (doe.get("approval_reference") or "").strip() == ref)


def station_options(study: Study, sid: str, q: str) -> dict:
    """`kapacitet station options`: this station's reinforcement and envelopes at the per-installation
    budget, on its own folder, into options/<q>.json."""
    import warnings

    from ..config import set_overrides
    from ..engine import request as rq
    from ..engine import run
    from ..engine import station as es
    from .cases import next_transformer, station_output
    from .station import capacity_path, isolated

    warnings.filterwarnings("ignore")
    if not isolated(study, sid):
        raise StudyError("not_isolated", id=sid, study=str(study.root))
    set_overrides(study.config.overrides(), layer="study")
    cap = json.loads(capacity_path().read_text(encoding="utf-8"))
    years, n_orders, pct = cap["years"], int(cap["orders"]), float(cap["percentile"])
    band = (float(min(cap["mv_band_pu"])), float(max(cap["mv_band_pu"])))
    m = run.model()
    hp, ev = run.background_masks(m.C)
    low = rq.model_at(band[0])
    t = low.net.trafo.iloc[0]
    now = {"std_type": None if t.get("std_type") is None else str(t["std_type"]), "sn_mva": float(t["sn_mva"])}
    nxt = next_transformer(low.net)
    ref = study.config.method.approval_reference.strip()
    out = {"schema": OPTIONS_SCHEMA, "station": sid, "quarter": q, "computed_at": procs.now(),
           "method_version": METHOD_VERSION, "fingerprint": cap.get("fingerprint"), "approval_reference": ref,
           "years": list(years), "orders": n_orders, "mv_band_pu": list(band), "current": now, "next": nxt}
    if nxt is not None:
        device = (cap.get("consumption") or {}).get("consumption_device") or study.config.method.consumption_device
        gen = es.generation_capacity(band, years, n_orders, pct, hp, ev, trafo_type=nxt["std_type"])
        cons = es.consumption_capacity(rq.model_at(band[0], trafo_type=nxt["std_type"]), es.engine_device(device),
                                       years, n_orders, pct, hp, ev)
        out["reinforce"] = {"generation": _brief(gen), "consumption": _brief(cons)}
    doe = (cap.get("generation") or {}).get("doe") or {}
    if reusable_doe(doe, ref):
        out["condition"] = {**doe, "source": "capacity.json"}
    elif not ref:
        out["condition"] = {"status": "not_approved"}
    elif (cap.get("generation") or {}).get("status") != "ok":
        out["condition"] = {"status": (cap.get("generation") or {}).get("status") or "not_computed"}
    else:
        again = es.generation_capacity(band, years, n_orders, pct, hp, ev, approval_reference=ref,
                                       budget="installation")
        out["condition"] = {**(again.get("doe") or {"status": "not_computed"}), "source": "computed"}
    write_json_atomic(station_output(q, "options"), out, default=run._default)
    return out


# ---------------------------------------------------------------- the options

def _assumption(study: Study, key: str):
    from .publish import _assumption as a

    return a(study, key)


def _current(study: Study, sid: str, q: str) -> dict | None:
    """The station's options/<q>.json while its build and the method approval are the same."""
    doc = _json(study.station_dir(sid) / "options" / f"{q}.json")
    cap = _json(study.station_dir(sid) / "processed" / "results" / "capacity.json")
    if (doc and doc.get("schema") == OPTIONS_SCHEMA and doc.get("method_version") == METHOD_VERSION
            and doc.get("fingerprint") == cap.get("fingerprint")
            and doc.get("approval_reference", "") == study.config.method.approval_reference.strip()):
        return doc
    return None


def _kva(sn_mva) -> float | None:
    return None if sn_mva is None else round(float(sn_mva) * 1000.0, 3)


def option_row(facts: dict, doc: dict | None, costs, rates: dict[str, float], horizon: int,
               reference: dict) -> dict:
    """One station's three options from its portfolio row and options/<q>.json (None when not
    computed; {"failed": code} when its process failed). Pure: the test's way in."""
    from ..engine.summaries import deferral_case, years_of_curtailment

    out = {name: None for name in OPTIONS_COLUMNS}
    out.update({k: facts.get(k) for k in ("rank", "station_id", "name", "customers", "pending_cases",
                                           "generation_kw", "consumption_kw")})
    out.update(reference_ei_500_kva_sek=reference.get("lo"), reference_ei_800_kva_sek=reference.get("hi"),
               reference_ei_price_level_year=reference.get("year"), cost_price_level_year=costs.price_level_year,
               cost_source=costs.source or None, wait_years=horizon)
    for key, col in RATE_COLUMNS.items():
        out[f"rate_{col}_pct"] = rates.get(key)
    if doc is None:
        out["reinforce_status"] = "not_computed"
    elif "failed" in doc:
        out["reinforce_status"] = "failed"
    else:
        now, nxt = doc.get("current") or {}, doc.get("next")
        out.update(trafo_now_type=now.get("std_type"), trafo_now_kva=_kva(now.get("sn_mva")))
        if nxt is None:
            out["reinforce_status"] = "no_larger_unit"
        else:
            g, c = (doc.get("reinforce") or {}).get("generation") or {}, (doc.get("reinforce") or {}).get("consumption") or {}
            ok = (g.get("status") == "ok" and c.get("status") == "ok"
                  and not g.get("full_year_violations") and not c.get("full_year_violations")
                  and not g.get("nonconverged_hours") and not c.get("nonconverged_hours"))
            out.update(reinforce_status="ok" if ok else "not_verified", trafo_next_type=nxt["std_type"],
                       trafo_next_kva=_kva(nxt["sn_mva"]), reinforce_generation_kw=g.get("kw"),
                       reinforce_generation_limit=g.get("limit"), reinforce_consumption_kw=c.get("kw"),
                       reinforce_violations=g.get("full_year_violations"))
            if g.get("kw") is not None:
                out["reinforce_generation_gain_kw"] = g["kw"] - (facts.get("generation_kw") or 0.0)
            if c.get("kw") is not None:
                out["reinforce_consumption_gain_kw"] = c["kw"] - (facts.get("consumption_kw") or 0.0)
            out["reinforce_cost_sek"] = costs.swap_sek(nxt["sn_mva"])
        cond = doc.get("condition") or {"status": "not_computed"}
        out.update(condition_status=cond.get("status"), condition_budget=cond.get("budget"))
        if cond.get("status") == "ok":
            out.update(condition_kw=cond.get("kw"), condition_adopters=cond.get("adopters"),
                       condition_z_pct=cond.get("z_pct"), condition_z_customer_max_pct=cond.get("z_customer_max_pct"),
                       condition_curtailed_kwh_per_year=cond.get("curtailed_kwh_per_year"))
    out["reinforce_cost_status"] = "entered" if out["reinforce_cost_sek"] is not None else NOT_ENTERED
    out["curtailment_price_status"] = "entered" if costs.curtailment_sek_per_kwh is not None else NOT_ENTERED
    if out["condition_curtailed_kwh_per_year"] is not None and costs.curtailment_sek_per_kwh is not None:
        out["condition_curtailment_sek_per_year"] = out["condition_curtailed_kwh_per_year"] * costs.curtailment_sek_per_kwh
    cost, k = out["reinforce_cost_sek"], out["condition_curtailment_sek_per_year"]
    out["years_of_curtailment"] = years_of_curtailment(cost, k)
    if cost is not None:
        for key, col in RATE_COLUMNS.items():
            if rates.get(key) is None:
                continue
            case = deferral_case(cost, k, rates[key] / 100.0, horizon)
            out[f"wait_deferral_sek_{col}"] = case["deferral_sek"]
            out[f"condition_net_sek_{col}"] = case["net_sek"]
    clean = not any(facts.get(k) for k in ("generation_baseline_violation_hours",
                                           "consumption_baseline_violation_hours", "full_year_violations"))
    rec, why = recommend(out, clean)
    out["recommendation"], out["why_none"] = rec, ",".join(why) or None
    return out


def recommend(r: dict, clean: bool = True) -> tuple[str | None, list[str]]:
    """The proposal for a station's options row, or None with what it lacks (see the docstring).
    `clean`: no baseline violation hours and no full-year violations; "wait" needs it and at least
    one deferral value."""
    status = r["reinforce_status"]
    why = []
    if status in ("not_computed", "failed", "no_larger_unit", "not_verified"):
        why.append(status if status == "no_larger_unit" else f"reinforce_{status}")
    if r["reinforce_cost_sek"] is None:
        why.append("cost_not_entered")
    if why:
        return None, why
    gains = [r["reinforce_generation_gain_kw"], r["reinforce_consumption_gain_kw"]]
    if not any(g is not None and g > 1e-9 for g in gains):
        return None, ["no_gain"]
    margins = [r["generation_kw"], r["consumption_kw"]]
    valued = any(v is not None for k, v in r.items() if k.startswith("wait_deferral_sek_"))
    if not r["pending_cases"] and clean and valued and all(m is not None and m > 0 for m in margins):
        return "wait", []
    if r["condition_status"] == "ok":
        net = r["condition_net_sek_in_force"]
        if net is None:
            return None, ["curtailment_price_not_entered"]
        return ("condition" if net > 0 else "reinforce"), []
    return "reinforce", []


def options_note(study: Study, q: str, r: dict, doc: dict | None, rates: dict[str, float]) -> str:
    from ..config import assumptions
    from .publish import render, sv

    def trafo(kind: str) -> str:
        t, kva = r[f"trafo_{kind}_type"], r[f"trafo_{kind}_kva"]
        if kva is None:
            return SV["not_computed"]
        return f"{t} ({sv(kva, 0)} kVA)" if t else f"{sv(kva, 0)} kVA"

    status = r["reinforce_status"]
    if status == "ok":
        reinforce = SV["reinforce.ok"].format(
            next=trafo("next"), now=trafo("now"), years=len((doc or {}).get("years") or []),
            gen=sv_kw(r["reinforce_generation_kw"]), gen_now=sv_kw(r["generation_kw"]),
            limit=SV.get(f"limit.{r['reinforce_generation_limit']}", r["reinforce_generation_limit"] or SV["none"]), cons=sv_kw(r["reinforce_consumption_kw"]),
            cons_now=sv_kw(r["consumption_kw"]), cost=sv_sek(r["reinforce_cost_sek"]))
    elif status in ("no_larger_unit", "not_verified"):
        reinforce = SV[f"reinforce.{status}"].format(now=trafo("now"), next=trafo("next"))
    else:
        reinforce = SV[f"reinforce.{status}"]
    cs = r["condition_status"]
    if cs == "ok":
        condition = SV["condition.ok"].format(kw=sv_kw(r["condition_kw"]), kwh=sv(r["condition_curtailed_kwh_per_year"], 0),
                                              z=sv(r["condition_z_pct"], 2), zmax=sv(r["condition_z_customer_max_pct"], 2),
                                              sek=sv_sek(r["condition_curtailment_sek_per_year"]))
    elif cs in ("not_approved", "not_verified"):
        condition = SV[f"condition.{cs}"]
    else:
        condition = SV["condition.other"].format(status=cs or SV["not_computed"])
    if r["reinforce_cost_sek"] is None:
        wait = SV["wait.no_cost"]
    else:
        known = assumptions()
        wait = "\n".join("- " + SV["wait.line"].format(
            rate=known[key].label_sv or known[key].label, pct=sv(rates[key], 2), years=r["wait_years"],
            deferral=sv_sek(r[f"wait_deferral_sek_{col}"]),
            net=sv_sek(r[f"condition_net_sek_{col}"]) if r[f"condition_net_sek_{col}"] is not None
            else SV["not_entered"]) for key, col in RATE_COLUMNS.items() if key in rates)
    if r["recommendation"]:
        recommendation = SV[f"rec.{r['recommendation']}"]
    else:
        recommendation = SV["rec.none"].format(why="; ".join(SV[f"why.{w}"] for w in (r["why_none"] or "").split(",") if w))
    costs = study.config.costs
    return render(OPTIONS_TEMPLATE.read_text(encoding="utf-8"), {
        "station": r["name"] or r["station_id"], "station_id": r["station_id"], "quarter": q,
        "dso_name": study.config.dso.name or study.id, "rank": str(r["rank"]),
        "generation_kw": sv_kw(r["generation_kw"]), "consumption_kw": sv_kw(r["consumption_kw"]),
        "pending": SV["pending"].format(n=r["pending_cases"] or 0),
        "reinforce": reinforce, "condition": condition, "wait": wait, "recommendation": recommendation,
        "costs_source": (SV["costs_source"].format(source=costs.source, year=costs.price_level_year or "–")
                         if costs.source else SV["costs_none"]),
        "reference": SV["reference"].format(year=r["reference_ei_price_level_year"],
                                            lo=sv_sek(r["reference_ei_500_kva_sek"]),
                                            hi=sv_sek(r["reference_ei_800_kva_sek"])),
    })


def options(study: Study, q: str, jobs: int | None = None, runner=None, allow_git: bool = False) -> dict:
    """runs/<q>/options.csv, its machine copy and a Swedish note per station; each ready station's
    options/<q>.json computed by its own process first, unless it is current."""
    from ..engine.summaries import RATES
    from .cases import run_stations
    from .review import run_dir

    facts = portfolio_rows(study, q)
    folder = run_dir(study, q)
    ready = [f["station_id"] for f in facts
             if f["status"] == "ready" and (study.station_dir(f["station_id"]) / "processed" / "results"
                                            / "capacity.json").is_file()]
    docs = {sid: _current(study, sid, q) for sid in ready}
    todo = {sid: {"quarter": q} for sid in ready if docs[sid] is None}
    if todo:
        done = run_stations(study, "options", q, todo, jobs=jobs, runner=runner, allow_git=allow_git,
                            work_dir=folder / "options" / "work")
        docs.update(done)
    costs = study.config.costs
    rates = {k: float(_assumption(study, k)) for k in RATES}
    horizon = costs.deferral_years or int(_assumption(study, "regulatory_period_years"))
    reference = {"lo": float(_assumption(study, "reinforce_trafo_500_sek")),
                 "hi": float(_assumption(study, "reinforce_trafo_800_sek")),
                 "year": int(_assumption(study, "reinforce_price_level_year"))}
    rows = [option_row(f, docs.get(f["station_id"]), costs, rates, horizon, reference) for f in facts]
    paths = csvio.write(folder, "options", rows, OPTIONS_COLUMNS, study.config.output.csv_dialect)
    notes = []
    for r in rows:
        path = folder / "options" / f"{r['station_id']}.sv.md"
        write_text_atomic(path, options_note(study, q, r, docs.get(r["station_id"]), rates))
        notes.append(path)
    failed = sorted(sid for sid, d in docs.items() if d and "failed" in d)
    return {"rows": rows, "paths": paths, "notes": notes, "computed": sorted(todo), "failed": failed, "quarter": q}


__all__ = ["NOT_ENTERED", "OPTIONS_COLUMNS", "PORTFOLIO_COLUMNS", "RATE_COLUMNS", "SV", "option_row", "options",
           "pending_cases", "portfolio", "portfolio_rows", "rank", "recommend", "station_options"]
