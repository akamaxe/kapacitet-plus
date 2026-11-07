"""Module 4: the inputs a DSO's network development plan asks for, per station and for the whole
study, from one collected quarter.

    kapacitet study devplan <dir> --run 2026Q4
        stations/<sid>/devplan/<Q>.json, .npz      what each station's own process computed
                                                   (`kapacitet station devplan`), reused while the
                                                   station's build and the [devplan] scenario are the same
        runs/<Q>/devplan/profiles/<sid>.parquet    the station's hourly load and generation (kW) in
              (+ .csv, .machine.csv)               the reference year, and each hour's P50 and P90
                                                   over the weather years
        runs/<Q>/devplan/profiles/study.parquet    the same for the whole study
        runs/<Q>/devplan/flexibility.csv (+ .machine.csv)
                                                   per station and for the study, today's grid and the
                                                   scenario: hours and energy above what the grid
                                                   hosts, with the binding component
        runs/<Q>/devplan/investments.csv (+ .machine.csv)
                                                   the next ten years from `study options`: the option,
                                                   the year it is needed and why, the cost when entered
        runs/<Q>/devplan/devplan_sv.md             what each file holds, in Swedish

The development plan is the one the regulation named in REGULATION describes; these files are
inputs to it. They are cited by name only: whether they satisfy its requirements is for the DSO to
check, and nothing here claims they do.

The profiles. A station's load is the sum of its customers' load (household profile or meter, the
heat pumps and EVs of today) and its generation the existing PV, hour by hour, in each weather
year: the state the station's capacity is computed from, with nobody added. The reference year's
hours are the profile; P50 and P90 are each hour's percentiles over the weather years, each year
aligned to the reference year's hours (a 29 February is dropped, or filled with 28 February's
hours when the reference year has one and the weather year does not). The study's profile is the
stations' sum in each hour of each weather year, and its percentiles are those of that sum, never
a sum of percentiles. The CSV is the study's dialect beside the machine copy (csvio).

The flexibility need, for today's grid and for the [devplan] scenario (the DSO's own shares of
customers with PV, EVs and heat pumps in its scenario_year; none entered means no scenario, "not
entered", never a guessed one). Every hour of every weather year is solved with the nonlinear flow
(engine/station.flexibility_need): the hours that break a limit and the energy that has to be
curtailed (generation side, the PV, at both ends of the MV band and the end with more energy
reported) or moved (consumption side, heat pumps and EV charging, at the low end) in them to keep
within the limits, with the kind of limit that binds. The published capacity is beside it for
reference. The hours that no flexibility clears are reported on their own. A study's row sums the
stations' energy in each weather year; hours are per station and are not summed.

The investment list, for the quarter's year and the nine after it: per station the swap to the next
standard transformer from runs/<Q>/options.machine.csv (`study options`, which has to run first),
its cost when the DSO entered it, otherwise "not entered", and the first year in the horizon it is
needed: the quarter's year when today's grid already needs flexibility or options recommends to
reinforce now, the scenario year when the scenario needs flexibility, and the end of the deferral
when options proposes to connect with conditions or to wait; none within the horizon otherwise.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from ..atomic import write_json_atomic, write_text_atomic
from . import Study, StudyError, csvio, runs, value_digest
from .config import METHOD_VERSION

SCHEMA = "kapacitet.station-devplan/1"
REGULATION = "EIFS 2026:4"
HORIZON_YEARS = 10
PERCENTILES = (50, 90)
STUDY_ID = "study"
TEMPLATE = Path(__file__).with_name("templates") / "devplan_sv.md"
STATES = ("today", "scenario")
SIDES = {"generation": "export", "consumption": "import"}
LIMITS = ("voltage", "cable", "transformer", "nonconverged")

PROFILE_COLUMNS = ["time_utc", "time_local", "load_kw", "generation_kw",
                   *(f"{k}_p{p}_kw" for k in ("load", "generation") for p in PERCENTILES)]
FLEX_COLUMNS = [
    "station_id", "name", "state", "scenario_year", "side", "published_kw", "status",
    "hours_per_year_mean", "hours_per_year_max", "hours_reference_year",
    "energy_kwh_per_year_mean", "energy_kwh_per_year_max", "energy_kwh_reference_year",
    "unresolved_hours", "binding_component", "binding_mv_pu", "hours_by_component", "stations_with_need",
]
INVESTMENT_COLUMNS = [
    "station_id", "name", "option", "reinforce_status", "trafo_now_kva", "trafo_next_type", "trafo_next_kva",
    "recommendation", "year_needed", "needed_because", "cost_sek", "cost_status", "cost_price_level_year",
]

# Every Swedish sentence of the summary: the figures go in through the {slots} (publish.sv), so no
# number is typed here (tests/test_study_devplan.py).
SV = {
    "scenario.entered": ("Scenariot för {year} är nätföretagets eget ({source}): andelen kunder med solceller "
                         "{pv}, med elbil {ev} och med värmepump {hp}, befintliga medräknade."),
    "scenario.none": ("Inget scenario är angivet i study.toml [devplan]: flexibilitetsbehovet redovisas bara "
                      "för dagens nät."),
    "share.today": "som i dag",
    "share": "{v} %",
    "none": "–",
}
SLOT = re.compile(r"\{[a-z_]+\}")


# ---------------------------------------------------------------- the scenario

def scenario_doc(study: Study) -> dict | None:
    """The [devplan] scenario as the station's process reads it, or None when no share is entered."""
    d = study.config.devplan
    if not d.entered():
        return None
    return {"year": d.scenario_year, "pv_share": d.pv_share, "ev_share": d.ev_share, "hp_share": d.hp_share,
            "source": d.source}


def scenario_masks(doc: dict, has_pv: np.ndarray, pv_order: np.ndarray, hp: np.ndarray,
                   ev: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(pv, hp, ev) masks of the scenario. PV: the candidates of `pv_order` (the published order)
    in turn until `pv_share` of the customers have PV, the existing ones counted; heat pumps and EVs:
    today's, plus the first of the fixed orders background_masks draws today's from, up to the
    share. A share left out keeps today's. Pure: the test's way in."""
    from ..engine.scenario import adoption_order, mask_from_share

    C = len(has_pv)
    pv = np.zeros(C, dtype=bool)
    if doc.get("pv_share") is not None:
        new = max(0, int(round(doc["pv_share"] * C)) - int(np.count_nonzero(has_pv)))
        pv[pv_order[:new]] = True
    hp_s = hp.copy() if doc.get("hp_share") is None else hp | mask_from_share(adoption_order(101, C), doc["hp_share"])
    ev_s = ev.copy() if doc.get("ev_share") is None else ev | mask_from_share(adoption_order(202, C), doc["ev_share"])
    return pv, hp_s, ev_s


# ---------------------------------------------------------------- inside a station's process

def summarize(per_year: dict, reference_year: int) -> dict:
    """Over the weather years: hours and energy a year (mean, max, the reference year), the
    unresolved hours, the hours of each kind of limit and the one with the most. `per_year`:
    {year: flexibility_need(...)}. Pure."""
    if not per_year:
        return {"per_year": {}}
    hours = [int(r["hours"]) for r in per_year.values()]
    energy = [float(r["energy_kwh"]) for r in per_year.values()]
    by = {k: sum(int((r.get("by_limit") or {}).get(k, 0)) for r in per_year.values()) for k in LIMITS}
    ref = per_year.get(reference_year) or per_year.get(str(reference_year)) or {}
    return {"hours_mean": float(np.mean(hours)), "hours_max": max(hours), "hours_reference_year": ref.get("hours"),
            "energy_kwh_mean": float(np.mean(energy)), "energy_kwh_max": max(energy),
            "energy_kwh_reference_year": ref.get("energy_kwh"),
            "unresolved_hours": sum(int(r["unresolved_hours"]) for r in per_year.values()),
            "by_limit": by if any(by.values()) else {},
            "limit": max(LIMITS, key=lambda k: (by[k], -LIMITS.index(k))) if any(by.values()) else None,
            "per_year": {str(y): {"hours": int(r["hours"]), "energy_kwh": float(r["energy_kwh"]),
                                  "unresolved_hours": int(r["unresolved_hours"])} for y, r in per_year.items()}}


def _worse_end(by_end: dict[float, dict]) -> tuple[float, dict]:
    """The end of the band with more energy over the years (then more hours; then the higher end)."""
    end = max(by_end, key=lambda e: (by_end[e].get("energy_kwh_mean", 0.0), by_end[e].get("hours_mean", 0.0), e))
    return end, by_end[end]


def station_devplan(study: Study, sid: str, q: str) -> dict:
    """`kapacitet station devplan`: this station's hourly profiles in every weather year and its
    flexibility need today and in the scenario, on its own folder, into devplan/<q>.json and .npz.
    The need arises with the devices of each state (today's, or the scenario's), not with the published
    capacity, which only appears as a reference column; hours no flexibility clears are counted apart
    (unresolved_hours) and carry no energy."""
    import warnings

    from ..config import set_overrides, settings
    from ..engine import request as rq
    from ..engine import run
    from ..engine import station as es
    from ..engine.scenario import Scenario, existing
    from .cases import station_output
    from .station import capacity_path, isolated

    warnings.filterwarnings("ignore")
    if not isolated(study, sid):
        raise StudyError("not_isolated", id=sid, study=str(study.root))
    set_overrides(study.config.overrides(), layer="study")
    cap = json.loads(capacity_path().read_text(encoding="utf-8"))
    years = [int(y) for y in cap["years"]]
    ref_year = int(settings().reference_year) if int(settings().reference_year) in years else years[-1]
    band = (float(min(cap["mv_band_pu"])), float(max(cap["mv_band_pu"])))
    ends = sorted(set(band))
    models = {e: rq.model_at(e) for e in ends}
    C = models[ends[0]].C
    hp, ev = run.background_masks(C)
    none = np.zeros(C, dtype=bool)
    scen = scenario_doc(study)
    states = {"today": (none, hp, ev)}
    if scen is not None:
        order = es.candidate_order(int((cap.get("generation") or {}).get("order") or 0), es.candidates())
        states["scenario"] = scenario_masks(scen, existing()["has_pv"], order, hp, ev)
    per = {s: {"export": {e: {} for e in ends}, "import": {}} for s in states}
    arrays: dict[str, np.ndarray] = {}
    for y in years:
        for s, (pv, hp_s, ev_s) in states.items():
            arr = Scenario(year=y, pv=pv, hp=hp_s, ev=ev_s).arrays()
            base = Scenario(year=y, pv=pv, hp=none, ev=none).arrays()["load"]
            p, qv = arr["p_net"].astype(float), arr["q"].astype(float)
            if s == "today":
                arrays[f"load_{y}"] = arr["load"].astype(float).sum(axis=0)
                arrays[f"generation_{y}"] = arr["pv"].astype(float).sum(axis=0)
            for e in ends:
                per[s]["export"][e][y] = es.flexibility_need(models[e], p, qv, arr["pv"], "export")
            per[s]["import"][y] = es.flexibility_need(models[band[0]], p, qv, arr["load"] - base, "import")
    flex = {}
    for s in states:
        end, gen = _worse_end({e: summarize(per[s]["export"][e], ref_year) for e in ends})
        flex[s] = {"generation": {**gen, "mv_pu": end}, "consumption": {**summarize(per[s]["import"], ref_year),
                                                                         "mv_pu": band[0]}}
    out = {"schema": SCHEMA, "station": sid, "quarter": q, "computed_at": _now(), "method_version": METHOD_VERSION,
           "fingerprint": cap.get("fingerprint"), "scenario_digest": value_digest(scen), "scenario": scen,
           "years": years, "reference_year": ref_year, "mv_band_pu": list(band),
           "energy": {"load_kwh_reference_year": float(arrays[f"load_{ref_year}"].sum()),
                      "generation_kwh_reference_year": float(arrays[f"generation_{ref_year}"].sum())},
           "flexibility": flex}
    if scen is not None:
        pv, hp_s, ev_s = states["scenario"]
        has = existing()["has_pv"]
        out["scenario_customers"] = {"customers": int(C), "pv": int(np.count_nonzero(pv | has)),
                                     "hp": int(np.count_nonzero(hp_s)), "ev": int(np.count_nonzero(ev_s))}
    target = station_output(q, "devplan")
    _save_arrays(target.with_suffix(".npz"), arrays)
    write_json_atomic(target, out, default=run._default)
    return out


def _now() -> str:
    from .. import procs

    return procs.now()


def _save_arrays(path: Path, arrays: dict[str, np.ndarray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".tmp.npz")
    np.savez_compressed(tmp, **arrays)
    tmp.replace(path)


# ---------------------------------------------------------------- the profiles

def align(values: np.ndarray, year: int, reference_year: int) -> np.ndarray:
    """A year's hourly values on the reference year's hours: a 29 February the reference year has
    not is dropped, and one it has and the year has not takes 28 February's hours. Values already
    on the reference year's hours are returned as they are. Pure."""
    import calendar

    values = np.asarray(values, dtype=float)
    leap, ref_leap = calendar.isleap(year), calendar.isleap(reference_year)
    if len(values) == (8784 if ref_leap else 8760):
        return values
    feb28 = 58 * 24
    if leap and not ref_leap:
        return np.concatenate([values[:feb28 + 24], values[feb28 + 48:]])
    if ref_leap and not leap:
        return np.concatenate([values[:feb28 + 24], values[feb28:feb28 + 24], values[feb28 + 24:]])
    return values


def profile_frame(per_year: dict[int, tuple[np.ndarray, np.ndarray]], reference_year: int):
    """The profile table: the reference year's load and generation per hour and each hour's
    percentiles over the years. `per_year`: {year: (load, generation)}, kW. Pure."""
    import pandas as pd

    from ..timeindex import local_standard, year_index

    idx = year_index(reference_year)
    load = np.vstack([align(v[0], y, reference_year) for y, v in sorted(per_year.items())])
    gen = np.vstack([align(v[1], y, reference_year) for y, v in sorted(per_year.items())])
    ref = per_year[reference_year]
    df = pd.DataFrame({"time_utc": idx, "time_local": local_standard(idx), "load_kw": np.asarray(ref[0], dtype=float),
                       "generation_kw": np.asarray(ref[1], dtype=float)})
    for name, m in (("load", load), ("generation", gen)):
        for p in PERCENTILES:
            df[f"{name}_p{p}_kw"] = np.percentile(m, p, axis=0)
    return df[PROFILE_COLUMNS]


def write_profile(folder: Path, name: str, df, dialect: str) -> list[Path]:
    """<name>.parquet beside <name>.csv and <name>.machine.csv (csvio), all written atomically."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.parquet"
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)
    rows = [{**r, "time_utc": r["time_utc"].strftime("%Y-%m-%dT%H:%MZ"),
             "time_local": r["time_local"].strftime("%Y-%m-%d %H:%M")} for r in df.to_dict("records")]
    return [path, *csvio.write(folder, name, rows, PROFILE_COLUMNS, dialect)]


def _load_arrays(study: Study, sid: str, q: str) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    path = study.station_dir(sid) / "devplan" / f"{q}.npz"
    with np.load(path) as z:
        years = sorted({int(k.split("_")[1]) for k in z.files if k.startswith("load_")})
        return {y: (z[f"load_{y}"], z[f"generation_{y}"]) for y in years}


# ---------------------------------------------------------------- the flexibility rows

def _by_component(by: dict) -> str | None:
    return "; ".join(f"{k}: {int(v)}" for k, v in by.items() if v) or None


def flex_rows(facts: dict, doc: dict | None, scenario_year: int, scenario: bool) -> list[dict]:
    """A station's four rows (two states, two sides) from its devplan/<q>.json (None when not
    computed; {"failed": code} when its process failed). Pure."""
    rows = []
    for state in STATES:
        for side in SIDES:
            r = {c: None for c in FLEX_COLUMNS}
            r.update(station_id=facts["station_id"], name=facts.get("name"), state=state, side=side,
                     scenario_year=scenario_year if state == "scenario" else None,
                     published_kw=facts.get(f"{side}_kw"))
            if state == "scenario" and not scenario:
                r["status"] = "not_entered"
            elif doc is None:
                r["status"] = "not_computed"
            elif "failed" in doc:
                r["status"] = "failed"
            else:
                s = ((doc.get("flexibility") or {}).get(state) or {}).get(side)
                if s is None:
                    r["status"] = "not_computed"
                else:
                    r.update(status="unresolved" if int(s.get("unresolved_hours") or 0) > 0 else "ok",
                             hours_per_year_mean=s.get("hours_mean"), hours_per_year_max=s.get("hours_max"),
                             hours_reference_year=s.get("hours_reference_year"),
                             energy_kwh_per_year_mean=s.get("energy_kwh_mean"),
                             energy_kwh_per_year_max=s.get("energy_kwh_max"),
                             energy_kwh_reference_year=s.get("energy_kwh_reference_year"),
                             unresolved_hours=s.get("unresolved_hours"), binding_component=s.get("limit"),
                             binding_mv_pu=s.get("mv_pu"), hours_by_component=_by_component(s.get("by_limit") or {}))
            rows.append(r)
    return rows


def study_flex_rows(docs: dict[str, dict | None], reference_year: int | None, scenario_year: int,
                    scenario: bool) -> list[dict]:
    """The study's rows: per state and side, the stations' energy summed in each weather year (the
    years every computed station has), then its mean, max and reference year; the stations with a
    need; hours are per station and stay blank. "incomplete" when a station has no figures. Pure."""
    ok = {sid: d for sid, d in docs.items() if d and "failed" not in d}
    rows = []
    for state in STATES:
        for side in SIDES:
            r = {c: None for c in FLEX_COLUMNS}
            r.update(station_id=STUDY_ID, state=state, side=side,
                     scenario_year=scenario_year if state == "scenario" else None)
            if state == "scenario" and not scenario:
                r["status"] = "not_entered"
                rows.append(r)
                continue
            blocks = [((d.get("flexibility") or {}).get(state) or {}).get(side) for d in ok.values()]
            blocks = [b for b in blocks if b is not None]
            if not blocks:
                r["status"] = "not_computed"
                rows.append(r)
                continue
            years = set.intersection(*(set((b.get("per_year") or {})) for b in blocks))
            energy = {y: sum(float(b["per_year"][y]["energy_kwh"]) for b in blocks) for y in sorted(years)}
            by = {k: sum(int((b.get("by_limit") or {}).get(k, 0)) for b in blocks) for k in LIMITS}
            r.update(status="ok" if len(blocks) == len(docs) else "incomplete",
                     energy_kwh_per_year_mean=float(np.mean(list(energy.values()))) if energy else None,
                     energy_kwh_per_year_max=max(energy.values()) if energy else None,
                     energy_kwh_reference_year=energy.get(str(reference_year)),
                     unresolved_hours=sum(int(b.get("unresolved_hours") or 0) for b in blocks),
                     binding_component=max(LIMITS, key=lambda k: (by[k], -LIMITS.index(k))) if any(by.values()) else None,
                     hours_by_component=_by_component(by),
                     stations_with_need=sum(1 for b in blocks if (b.get("hours_max") or 0) > 0))
            rows.append(r)
    return rows


# ---------------------------------------------------------------- the investment list

def _num(x) -> float | None:
    if x is None or isinstance(x, (int, float)):
        return None if x is None else float(x)
    try:
        return csvio.number(x)
    except ValueError:
        return None


def investment_row(facts: dict, option: dict | None, flex: list[dict], first_year: int,
                   horizon: int = HORIZON_YEARS) -> dict:
    """A station's line of the investment list from its options row (runs/<Q>/options.machine.csv,
    None when options has not run) and its flexibility rows. Pure: the test's way in."""
    from .decision import NOT_ENTERED

    out = {c: None for c in INVESTMENT_COLUMNS}
    out.update(station_id=facts["station_id"], name=facts.get("name"))
    if option is None:
        out.update(option="not_computed", needed_because="options_not_computed")
        return out
    status = option.get("reinforce_status") or "not_computed"
    rec = option.get("recommendation") or None
    out.update(reinforce_status=status, recommendation=rec, trafo_now_kva=_num(option.get("trafo_now_kva")))
    if status == "no_larger_unit":
        out.update(option="none", needed_because="no_larger_unit")
        return out
    if status in ("not_computed", "failed"):
        out.update(option="not_computed", needed_because=f"reinforce_{status}")
        return out
    cost = _num(option.get("reinforce_cost_sek"))
    out.update(option="transformer_swap", trafo_next_type=option.get("trafo_next_type") or None,
               trafo_next_kva=_num(option.get("trafo_next_kva")), cost_sek=cost,
               cost_status="entered" if cost is not None else NOT_ENTERED,
               cost_price_level_year=_num(option.get("cost_price_level_year")))
    if out["cost_price_level_year"] is not None:
        out["cost_price_level_year"] = int(out["cost_price_level_year"])
    last = first_year + horizon - 1

    def need(state: str) -> bool:
        return any(r["state"] == state and r["status"] in ("ok", "unresolved") and (r["hours_per_year_max"] or 0) > 0 for r in flex)

    when = []
    if need("today"):
        when.append((first_year, "violations_today"))
    if rec == "reinforce":
        when.append((first_year, "recommended_now"))
    scen_year = next((r["scenario_year"] for r in flex if r["state"] == "scenario" and r["scenario_year"]), None)
    if need("scenario") and scen_year is not None:
        when.append((max(int(scen_year), first_year), "scenario"))
    wait = _num(option.get("wait_years"))
    if rec in ("condition", "wait") and wait is not None:
        when.append((first_year + int(wait), "end_of_deferral"))
    inside = sorted(w for w in when if w[0] <= last)
    if inside:
        out.update(year_needed=inside[0][0], needed_because=inside[0][1])
    else:
        out["needed_because"] = "not_within_horizon"
    return out


# ---------------------------------------------------------------- the summary

def summary_markdown(study: Study, q: str, n_stations: int, reference_year: int | None, years: list[int],
                     scenario: dict | None, first_year: int) -> str:
    from .publish import render, sv

    if scenario is None:
        scen = SV["scenario.none"]
    else:
        def share(x):
            return SV["share.today"] if x is None else SV["share"].format(v=sv(100.0 * x, 1))
        scen = SV["scenario.entered"].format(year=scenario["year"], source=scenario["source"],
                                             pv=share(scenario["pv_share"]), ev=share(scenario["ev_share"]),
                                             hp=share(scenario["hp_share"]))
    span = (f"{years[0]}–{years[-1]}" if len(years) > 1 else str(years[0])) if years else SV["none"]
    return render(TEMPLATE.read_text(encoding="utf-8"), {
        "dso_name": study.config.dso.name or study.id, "quarter": q, "period_word": runs.word_sv(q),
        "regulation": REGULATION,
        "stations": str(n_stations), "reference_year": str(reference_year) if reference_year else SV["none"],
        "years": span, "scenario": scen, "p_median": "P50", "p_high": "P90", "machine_dialect": "RFC 4180",
        "horizon": str(HORIZON_YEARS), "first_year": str(first_year), "last_year": str(first_year + HORIZON_YEARS - 1),
    })


# ---------------------------------------------------------------- the command

def _current(study: Study, sid: str, q: str, digest: str) -> dict | None:
    """The station's devplan/<q>.json while its build and the scenario are the same."""
    folder = study.station_dir(sid)
    doc = _json(folder / "devplan" / f"{q}.json")
    cap = _json(folder / "processed" / "results" / "capacity.json")
    if (doc and doc.get("schema") == SCHEMA and doc.get("method_version") == METHOD_VERSION
            and doc.get("fingerprint") == cap.get("fingerprint") and doc.get("scenario_digest") == digest
            and (folder / "devplan" / f"{q}.npz").is_file()):
        return doc
    return None


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}


def devplan(study: Study, q: str, jobs: int | None = None, runner=None, allow_git: bool = False) -> dict:
    """runs/<q>/devplan/: the profiles, flexibility.csv, investments.csv and devplan_sv.md; each
    ready station's devplan/<q>.json computed by its own process first, unless it is current."""
    from .cases import run_stations
    from .decision import portfolio_rows
    from .review import run_dir

    facts = portfolio_rows(study, q)
    folder = run_dir(study, q) / "devplan"
    dialect = study.config.output.csv_dialect
    scen = scenario_doc(study)
    digest = value_digest(scen)
    ready = [f["station_id"] for f in facts
             if f["status"] == "ready" and (study.station_dir(f["station_id"]) / "processed" / "results"
                                            / "capacity.json").is_file()]
    docs: dict[str, dict | None] = {sid: _current(study, sid, q, digest) for sid in ready}
    todo = {sid: {"quarter": q} for sid in ready if docs[sid] is None}
    if todo:
        docs.update(run_stations(study, "devplan", q, todo, jobs=jobs, runner=runner, allow_git=allow_git,
                                 work_dir=folder / "work"))
    ok = {sid: d for sid, d in docs.items() if d and "failed" not in d}
    ref_years = sorted({int(d["reference_year"]) for d in ok.values()})
    reference_year = ref_years[-1] if ref_years else None
    paths: list[Path] = []
    skipped: list[str] = []
    total: dict[int, tuple[np.ndarray, np.ndarray]] | None = None
    years_all: set[int] | None = None
    for sid, d in sorted(ok.items()):
        per_year = _load_arrays(study, sid, q)
        if int(d["reference_year"]) != reference_year:
            skipped.append(sid)
            continue
        paths += write_profile(folder / "profiles", sid, profile_frame(per_year, reference_year), dialect)
        aligned = {y: (align(v[0], y, reference_year), align(v[1], y, reference_year)) for y, v in per_year.items()}
        years_all = set(aligned) if years_all is None else years_all & set(aligned)
        if total is None:
            total = aligned
        else:
            total = {y: (total[y][0] + aligned[y][0], total[y][1] + aligned[y][1]) for y in years_all}
    if total and reference_year in total:
        paths += write_profile(folder / "profiles", STUDY_ID, profile_frame(total, reference_year), dialect)
    scen_year = study.config.devplan.scenario_year
    flex = {f["station_id"]: flex_rows(f, docs.get(f["station_id"]), scen_year, scen is not None) for f in facts}
    flex_all = [r for f in facts for r in flex[f["station_id"]]]
    flex_all += study_flex_rows({sid: docs.get(sid) for sid in (f["station_id"] for f in facts)},
                                reference_year, scen_year, scen is not None)
    paths += csvio.write(folder, "flexibility", flex_all, FLEX_COLUMNS, dialect)
    first_year = int(q[:4])
    options = {r.get("station_id"): r for r in csvio.read(run_dir(study, q) / f"options{csvio.MACHINE}")}
    inv = [investment_row(f, options.get(f["station_id"]), flex[f["station_id"]], first_year) for f in facts]
    paths += csvio.write(folder, "investments", inv, INVESTMENT_COLUMNS, dialect)
    md = folder / "devplan_sv.md"
    write_text_atomic(md, summary_markdown(study, q, len(facts), reference_year,
                                           sorted(years_all) if years_all else [], scen, first_year))
    paths.append(md)
    failed = sorted(sid for sid, d in docs.items() if d and "failed" in d)
    return {"flexibility": flex_all, "investments": inv, "paths": paths, "computed": sorted(todo), "failed": failed,
            "options_missing": not options, "profiles_skipped": skipped, "quarter": q, "reference_year": reference_year}


__all__ = ["FLEX_COLUMNS", "HORIZON_YEARS", "INVESTMENT_COLUMNS", "PROFILE_COLUMNS", "REGULATION", "SCHEMA", "SV",
           "align", "devplan", "flex_rows", "investment_row", "profile_frame", "scenario_masks", "station_devplan",
           "study_flex_rows", "summarize"]
