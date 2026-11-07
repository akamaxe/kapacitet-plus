"""Module 4: electrification scenarios on each station of a study, from its own data.

    kapacitet study scenarios <dir> --run 2026Q4
        stations/<sid>/scenarios/<Q>.json          what each station's own process computed
                                                   (`kapacitet station scenarios`), reused while the
                                                   station's build and the [scenarios] growth are the same
        stations/<sid>/processed/results/scenarios.json
                                                   the same, where the portfolio reads it (trend column)
        runs/<Q>/scenarios.csv (+ .machine.csv)    one row per station and year

The 2030 scenarios of the reference network (engine/run_extra.scenarios_2030) vary the shares of
houses with PV, EVs and heat pumps on the synthetic network. Here each station starts from what it
has today, by its own equipment table and metered load (scenario.existing, run.background_masks:
the existing PV, the heat pumps and chargers on record, the meters' load, and the default shares of
today_hp_share and today_ev_share only where the records say nothing), and grows a year at a time
from the quarter's year to [scenarios] year. In a year `dy` after the start each device has today's
count plus round(rate × customers × dy), at most every customer that can take it:
- PV goes on the candidates (no PV yet, a roof) in the order the station's published generation
  capacity uses, so the new systems take the first places of that deployment;
- heat pumps and EV chargers go to the first customers without one of the fixed orders today's are
  drawn from (the same as the reference scenarios and `study devplan`);
- batteries (none on record today: the equipment table holds none) go only to houses with PV, in a
  fixed order, at scenario_battery_kwh each, and run for self-consumption: they charge from the
  house's own surplus and discharge to its own load, never to or from the grid (`self_consumption`).
  They cannot help the consumption side, which is computed without solar.
The rates are the DSO's own in study.toml [scenarios] (with their source) or, for a rate left out,
the default of config/assumptions.toml (scenario_*_growth_per_year), and every output says which.

In each year, with the build's weather years, its MV band and its verification:
- the state itself (the year's devices, nobody else added): the hours that break an export limit,
  every hour solved with the nonlinear flow at both ends of the band (the end with more counts), and
  the hours that break an import limit at the low end, summed over the weather years;
- what is left of the station's published capacity: generation, the published order's deployment
  verified again on every hour at both ends with the year's devices, less the systems the growth
  put on it (a count that verifies, found by halving when the published one no longer does); and
  consumption, the published order's new devices of the study's kind on top of the year's, at the
  low end. Never more than today's published deployment, so a year can show the capacity shrinking,
  never a gain the published figure did not verify; with no growth the year is today's grid and its
  figures are the published ones.
The figures are the station's own, before the upstream caps and the reviewer's corrections that
the portfolio's margins carry. No envelopes are computed here: the scenarios are on the firm basis.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..atomic import write_json_atomic
from . import Study, StudyError, csvio, value_digest
from .config import METHOD_VERSION

SCHEMA = "kapacitet.station-scenarios/1"
DEVICES = ("pv", "ev", "hp", "battery")
# the default of each rate (config/assumptions.toml), by device
DEFAULTS = {"pv": "scenario_pv_growth_per_year", "ev": "scenario_ev_growth_per_year",
            "hp": "scenario_hp_growth_per_year", "battery": "scenario_battery_growth_per_year"}
BATTERY_KWH = "scenario_battery_kwh"
SEEDS = {"hp": 101, "ev": 202, "battery": 303}      # 101 and 202: run.background_masks' orders

COLUMNS = [
    "station_id", "name", "status", "year", "years_from_start", "rates", "customers",
    *DEVICES, *(f"new_{d}" for d in DEVICES),
    "generation_status", "generation_published_kw", "generation_kw_left", "export_violation_hours",
    "export_binding_mv_pu", "consumption_device", "consumption_status", "consumption_published_kw",
    "consumption_kw_left", "import_violation_hours",
]


# ---------------------------------------------------------------- the growth

def growth_doc(study: Study, start_year: int) -> dict:
    """The growth a station's process reads: the start and end year, each rate with whether it is
    the DSO's ("entered") or config/assumptions.toml's ("default"), the source and the battery size."""
    from ..config import A

    s = study.config.scenarios
    rates, basis = {}, {}
    for d in DEVICES:
        own = getattr(s, f"{d}_per_year")
        rates[d] = float(A(DEFAULTS[d]) if own is None else own)
        basis[d] = "default" if own is None else "entered"
    return {"start_year": int(start_year), "year": max(int(s.year), int(start_year)), "rates": rates,
            "basis": basis, "source": s.source.strip() or None, "battery_kwh": float(A(BATTERY_KWH))}


def rates_label(doc: dict) -> str:
    """"entered", "default" or "mixed": whose rates a scenario runs on."""
    kinds = set((doc.get("basis") or {}).values())
    return kinds.pop() if len(kinds) == 1 else "mixed"


def added(rate: float, customers: int, dy: int, room: int) -> int:
    """How many devices the growth adds in `dy` years: round(rate × customers × dy), at most `room`.
    Pure."""
    return int(min(max(room, 0), round(float(rate) * customers * max(dy, 0))))


def _first_without(order: np.ndarray, has: np.ndarray, n: int) -> np.ndarray:
    out = has.copy()
    free = [int(c) for c in order if not has[c]]
    out[free[:n]] = True
    return out


def grow(rates: dict, dy: int, has_pv: np.ndarray, pv_order: np.ndarray, hp: np.ndarray,
         ev: np.ndarray) -> dict[str, np.ndarray]:
    """The year's devices `dy` years after today: {"pv_new" (the new systems, on the first places of
    `pv_order`), "pv" (with the existing ones), "hp", "ev", "battery"} as (C,) bool masks. Pure: the
    test's way in."""
    from ..engine.scenario import adoption_order

    C = len(has_pv)
    pv_new = np.zeros(C, dtype=bool)
    pv_new[pv_order[:added(rates["pv"], C, dy, len(pv_order))]] = True
    pv = pv_new | has_pv
    hp_y = _first_without(adoption_order(SEEDS["hp"], C), hp, added(rates["hp"], C, dy, C - int(hp.sum())))
    ev_y = _first_without(adoption_order(SEEDS["ev"], C), ev, added(rates["ev"], C, dy, C - int(ev.sum())))
    room = np.zeros(C, dtype=bool)
    room[~pv] = True                              # only a house with PV takes a battery
    bat = _first_without(adoption_order(SEEDS["battery"], C), room,
                         added(rates["battery"], C, dy, int(pv.sum()))) & pv
    return {"pv_new": pv_new, "pv": pv, "hp": hp_y, "ev": ev_y, "battery": bat}


def self_consumption(pv: np.ndarray, load: np.ndarray, kwh: float, kw: float, eta_in: float,
                     eta_out: float) -> np.ndarray:
    """(B, H) the change a self-consumption battery makes to each house's net injection: it charges
    from the house's surplus (pv − load, at most `kw`, until full) and discharges to its deficit (at
    most `kw`, while it holds energy); nothing flows from or to the grid through it. Starts empty.
    Pure."""
    pv, load = np.asarray(pv, dtype=float), np.asarray(load, dtype=float)
    surplus = pv - load
    e = np.zeros(pv.shape[0])
    out = np.zeros_like(surplus)
    for t in range(surplus.shape[1]):
        s = surplus[:, t]
        ch = np.minimum(np.minimum(np.maximum(s, 0.0), kw), (kwh - e) / eta_in)
        dis = np.minimum(np.minimum(np.maximum(-s, 0.0), kw), e * eta_out)
        dis = np.where(ch > 0, 0.0, dis)
        e = e + ch * eta_in - dis / eta_out
        out[:, t] = dis - ch
    return out


def largest_verified(lo: int, hi: int, ok) -> int:
    """`hi` when ok(hi); otherwise a count in [lo, hi) that verifies, found by halving with `lo`
    known to verify. Each count it returns has passed ok (or is `lo`)."""
    if hi <= lo or ok(hi):
        return max(hi, lo)
    good, bad = lo, hi
    while bad - good > 1:
        mid = (good + bad) // 2
        if ok(mid):
            good = mid
        else:
            bad = mid
    return good


# ---------------------------------------------------------------- inside a station's process

def _year_generation(models: dict, order: np.ndarray, gen: dict, dev: dict, years, bp: dict) -> dict:
    from ..engine import doe
    from ..engine import station as es
    from ..engine.scenario import Scenario

    C = len(dev["pv"])
    n_new = int(dev["pv_new"].sum())
    bat = np.flatnonzero(dev["battery"])
    delta: dict[int, np.ndarray] = {}
    if len(bat):
        for y in years:
            arr = Scenario(year=y, pv=dev["pv"], hp=dev["hp"], ev=dev["ev"]).arrays()
            delta[y] = self_consumption(arr["pv"][bat], arr["load"][bat], bp["kwh"], bp["kw"], bp["eta_in"],
                                        bp["eta_out"])

    def states(k: int):
        mask = np.zeros(C, dtype=bool)
        mask[order[:max(k, n_new)]] = True
        for y in years:
            arr = Scenario(year=y, pv=mask, hp=dev["hp"], ev=dev["ev"]).arrays()
            p = arr["p_net"].astype(float)
            if len(bat):
                p[bat] += delta[y]
            yield y, p, arr["q"].astype(float)

    base = {e: es.verify(m, states(n_new), "export") for e, m in models.items()}
    worst = max(base, key=lambda e: (base[e]["full_year_violations"], e))
    out = {"violation_hours": int(base[worst]["full_year_violations"]), "binding_mv_pu": float(worst),
           "by_end": {f"{e:.2f}": int(c["full_year_violations"]) for e, c in base.items()}}
    if gen.get("status") != "ok":
        return {**out, "status": gen.get("status") or "not_computed", "kw_left": None}
    if out["violation_hours"]:
        return {**out, "status": "state_violates", "kw_left": 0.0, "adopters_left": 0}
    k_pub = int(gen.get("adopters") or 0)
    k = largest_verified(n_new, k_pub, lambda n: all(not es.verify(m, states(n), "export")["full_year_violations"]
                                                     for m in models.values()))
    cap = doe.customer_caps()
    left = order[n_new:k] if k > n_new else order[:0]
    return {**out, "status": "ok", "kw_left": float(cap[left].sum()), "adopters_left": int(len(left))}


def _year_consumption(model, cons: dict, device: str, dev: dict, years, kw_each: np.ndarray) -> dict:
    from ..engine import station as es
    from ..engine.scenario import adoption_order

    hp, ev = dev["hp"], dev["ev"]
    order = adoption_order(int(cons.get("order") or 0), model.C)
    new = es._new_devices(order, device, hp, ev)
    base = es.verify(model, es._load_states(new, 0, device, hp, ev, years), "import")
    out = {"violation_hours": int(base["full_year_violations"])}
    if cons.get("status") != "ok":
        return {**out, "status": cons.get("status") or "not_computed", "kw_left": None}
    if out["violation_hours"]:
        return {**out, "status": "state_violates", "kw_left": 0.0, "adopters_left": 0}
    n_pub = min(int(cons.get("adopters") or 0), len(new))
    n = largest_verified(0, n_pub, lambda c: not es.verify(model, es._load_states(new, c, device, hp, ev, years),
                                                           "import")["full_year_violations"])
    return {**out, "status": "ok", "kw_left": float(kw_each[new[:n]].sum()), "adopters_left": int(n)}


def first_years(rows: list[dict]) -> dict:
    """The first year the state breaks a limit and the first a published capacity is used up (kW
    left zero), or None. Pure."""
    def first(pred):
        return next((r["year"] for r in rows if pred(r)), None)

    return {"first_violation_year": first(lambda r: (r["generation"].get("violation_hours") or 0)
                                          + (r["consumption"].get("violation_hours") or 0) > 0),
            "first_exhausted_year": first(lambda r: any(r[s].get("kw_left") == 0.0 for s in ("generation", "consumption")))}


def station_scenarios(study: Study, sid: str, q: str) -> dict:
    """`kapacitet station scenarios`: this station grown a year at a time from the quarter's year to
    [scenarios] year, on its own folder, into scenarios/<q>.json and processed/results/scenarios.json."""
    import warnings

    from ..config import set_overrides
    from ..engine import request as rq
    from ..engine import run
    from ..engine import station as es
    from ..engine.scenario import customers, existing
    from .cases import station_output
    from .station import capacity_path, isolated

    warnings.filterwarnings("ignore")
    if not isolated(study, sid):
        raise StudyError("not_isolated", id=sid, study=str(study.root))
    set_overrides(study.config.overrides(), layer="study")
    cap = json.loads(capacity_path().read_text(encoding="utf-8"))
    years = [int(y) for y in cap["years"]]
    band = (float(min(cap["mv_band_pu"])), float(max(cap["mv_band_pu"])))
    models = {e: rq.model_at(e) for e in sorted(set(band))}
    low = models[band[0]]
    C = low.C
    hp, ev = run.background_masks(C)
    gen = cap.get("generation") or {}
    cons = cap.get("consumption") or {}
    device = cons.get("consumption_device") or cons.get("device") or study.config.method.consumption_device
    device = es.DEVICES.get(device, device)
    growth = growth_doc(study, int(q[:4]))
    order = es.candidate_order(int(gen.get("order") or 0), es.candidates())
    kw_each = es.device_kw(device, years)
    bp = rq.battery_params(growth["battery_kwh"])
    has_pv = existing()["has_pv"]
    rows = []
    for year in range(growth["start_year"], growth["year"] + 1):
        dy = year - growth["start_year"]
        dev = grow(growth["rates"], dy, has_pv, order, hp, ev)
        counts = {"pv": int(dev["pv"].sum()), "ev": int(dev["ev"].sum()), "hp": int(dev["hp"].sum()),
                  "battery": int(dev["battery"].sum())}
        new = {"pv": int(dev["pv_new"].sum()), "ev": int((dev["ev"] & ~ev).sum()),
               "hp": int((dev["hp"] & ~hp).sum()), "battery": counts["battery"]}
        rows.append({"year": year, "years_from_start": dy, "counts": counts, "new": new,
                     "generation": _year_generation(models, order, gen, dev, years, bp),
                     "consumption": _year_consumption(low, cons, device, dev, years, kw_each)})
    out = {"schema": SCHEMA, "station": sid, "quarter": q, "computed_at": _now(), "method_version": METHOD_VERSION,
           "fingerprint": cap.get("fingerprint"), "scenario_digest": value_digest(growth), "scenario": growth,
           "weather_years": years, "mv_band_pu": list(band), "customers": int(C),
           "kwp_existing": float(existing()["pv_kwp"].sum()), "roofs": int((customers()["kwp"] > 0).sum()),
           "published": {"generation_kw": gen.get("kw"), "generation_status": gen.get("status"),
                         "consumption_kw": cons.get("kw"), "consumption_status": cons.get("status"),
                         "consumption_device": device},
           "years": rows, **first_years(rows)}
    write_json_atomic(station_output(q, "scenarios"), out, default=run._default)
    write_json_atomic(capacity_path().with_name("scenarios.json"), out, default=run._default)
    return out


def _now() -> str:
    from .. import procs

    return procs.now()


# ---------------------------------------------------------------- the study's table

def station_rows(facts: dict, doc: dict | None) -> list[dict]:
    """A station's rows of scenarios.csv from its scenarios/<q>.json (None when not computed;
    {"failed": code} when its process failed): one per year, or one with the status. Pure."""
    base = {c: None for c in COLUMNS}
    base.update(station_id=facts["station_id"], name=facts.get("name"))
    if doc is None:
        return [{**base, "status": "not_computed"}]
    if "failed" in doc:
        return [{**base, "status": "failed"}]
    pub = doc.get("published") or {}
    rows = []
    for r in doc.get("years") or []:
        g, c = r.get("generation") or {}, r.get("consumption") or {}
        row = {**base, "status": "ok", "year": r["year"], "years_from_start": r.get("years_from_start"),
               "rates": rates_label(doc.get("scenario") or {}), "customers": doc.get("customers"),
               "generation_status": g.get("status"), "generation_published_kw": pub.get("generation_kw"),
               "generation_kw_left": g.get("kw_left"), "export_violation_hours": g.get("violation_hours"),
               "export_binding_mv_pu": g.get("binding_mv_pu"), "consumption_device": pub.get("consumption_device"),
               "consumption_status": c.get("status"), "consumption_published_kw": pub.get("consumption_kw"),
               "consumption_kw_left": c.get("kw_left"), "import_violation_hours": c.get("violation_hours")}
        for d in DEVICES:
            row[d] = (r.get("counts") or {}).get(d)
            row[f"new_{d}"] = (r.get("new") or {}).get(d)
        rows.append(row)
    return rows or [{**base, "status": "not_computed"}]


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}


def _current(study: Study, sid: str, q: str, digest: str) -> dict | None:
    """The station's scenarios/<q>.json while its build and the growth are the same."""
    folder = study.station_dir(sid)
    doc = _json(folder / "scenarios" / f"{q}.json")
    cap = _json(folder / "processed" / "results" / "capacity.json")
    if (doc and doc.get("schema") == SCHEMA and doc.get("method_version") == METHOD_VERSION
            and doc.get("fingerprint") == cap.get("fingerprint") and doc.get("scenario_digest") == digest):
        return doc
    return None


def scenarios(study: Study, q: str, jobs: int | None = None, runner=None, allow_git: bool = False) -> dict:
    """runs/<q>/scenarios.csv and its machine copy; each ready station's scenarios/<q>.json computed
    by its own process first, unless it is current."""
    from .cases import run_stations
    from .decision import portfolio_rows
    from .review import run_dir

    facts = portfolio_rows(study, q)
    folder = run_dir(study, q)
    growth = growth_doc(study, int(q[:4]))
    digest = value_digest(growth)
    ready = [f["station_id"] for f in facts
             if f["status"] == "ready" and (study.station_dir(f["station_id"]) / "processed" / "results"
                                            / "capacity.json").is_file()]
    docs: dict[str, dict | None] = {sid: _current(study, sid, q, digest) for sid in ready}
    todo = {sid: {"quarter": q} for sid in ready if docs[sid] is None}
    if todo:
        docs.update(run_stations(study, "scenarios", q, todo, jobs=jobs, runner=runner, allow_git=allow_git,
                                 work_dir=folder / "scenarios-work"))
    rows = [r for f in facts for r in station_rows(f, docs.get(f["station_id"]))]
    paths = csvio.write(folder, "scenarios", rows, COLUMNS, study.config.output.csv_dialect)
    failed = sorted(sid for sid, d in docs.items() if d and "failed" in d)
    return {"rows": rows, "paths": paths, "computed": sorted(todo), "failed": failed, "quarter": q,
            "growth": growth}


__all__ = ["COLUMNS", "DEFAULTS", "DEVICES", "SCHEMA", "added", "first_years", "grow", "growth_doc",
           "largest_verified", "rates_label", "scenarios", "self_consumption", "station_rows", "station_scenarios"]
