"""Hosting capacity: the conservative static method and the time-series view.

Static (worst-case) method used in connection studies, as two parameters of config/assumptions.toml
(`static_definition` writes them out next to the results):
  summer snapshot, for solar: every PV system at `static_pv_factor` × its AC rating while each
  household draws the load `static_load_rule` picks ("min_daytime_summer": its lowest load on
  summer days, 10–16 h in May–August, over all weather years). The static solar capacity
  (`static_adopters`, `nodal_pv_hc`) checks this snapshot only, on the export side.
  winter snapshot, for consumption: the hour with the highest simulated neighbourhood demand in
  the whole record with today's heat pumps and EVs; a new charger counts at full power. It is
  the static consumption capacity of each house (`nodal_import_hc`), and the requests' check.

Time-series method: the same deployment is simulated for every hour of every weather year
with real weather; hosting capacity is the largest deployment without any violation (all
customers firm, nobody curtailed). For solar that is `ts_adopters`; for consumption,
`nodal_import_hc_ts` (each house alone) and `ts_import_adopters` (a device added house by house).
"""

from __future__ import annotations

import logging

import numpy as np

from ..cache import memo
from ..config import A, settings
from ..timeindex import local_standard, year_index
from .netmodel import NetModel
from .scenario import Scenario, customers, existing_pv_kw, year_profiles

log = logging.getLogger(__name__)


# ------------------------------------------------------------------------------ snapshot inputs

SUMMER_MONTHS = (5, 8)      # May to August, inclusive
DAYTIME_HOURS = (10, 16)    # 10:00 to 16:00 on the study's clock (UTC+1), end excluded

# What `static_load_rule` may name: the household load the static summer snapshot assumes.
STATIC_LOAD_RULES = {
    "min_daytime_summer": "each household's lowest load on summer days (May-August, 10-16 h, UTC+1) "
                          "in any weather year",
    "no_load": "no household load at all, the strictest reading",
}


def static_load_rule() -> str:
    rule = A("static_load_rule")
    if rule not in STATIC_LOAD_RULES:
        raise ValueError(f"static_load_rule must be one of {sorted(STATIC_LOAD_RULES)}, not {rule!r}")
    return rule


def min_daytime_load() -> np.ndarray:
    """Per-customer household load (kW) of the static summer snapshot, by `static_load_rule`
    (× the sensitivity factors)."""
    if static_load_rule() == "no_load":
        return np.zeros(len(customers()))
    return _min_daytime_load() * A("static_min_load_factor") * A("household_load_factor")


@memo(maxsize=1)
def _min_daytime_load() -> np.ndarray:
    out = None
    for y in settings().weather_years:
        idx = local_standard(year_index(y))
        m = ((idx.month >= SUMMER_MONTHS[0]) & (idx.month <= SUMMER_MONTHS[1])
             & (idx.hour >= DAYTIME_HOURS[0]) & (idx.hour < DAYTIME_HOURS[1]))
        base = year_profiles(y)["base"][:, m].min(axis=1)
        out = base if out is None else np.minimum(out, base)
    return out.astype(float)


def static_definition(model: NetModel | None = None) -> dict:
    """The static reference as the results state it (hosting_summary.json): what the summer
    snapshot puts on the grid and what it checks, at `model`'s MV voltage (the default's if None)."""
    rule = static_load_rule()
    out = {
        "snapshot": "summer",
        "checks": "export side: overvoltage and reverse-flow overloads",
        "pv_factor": float(A("static_pv_factor")),
        "pv_rating": "inverter AC rating: kWp / DC-AC ratio, at most the main fuse",
        "load_rule": rule,
        "load_rule_text": STATIC_LOAD_RULES[rule],
        "mv_voltage_pu": float(A("mv_voltage_pu") if model is None else model.vm_slack),
    }
    if rule == "min_daytime_summer":
        out.update(months=list(SUMMER_MONTHS), hours=list(DAYTIME_HOURS),
                   load_factor=float(A("static_min_load_factor") * A("household_load_factor")))
    return out


@memo(maxsize=1)
def winter_design_load() -> dict[str, np.ndarray]:
    """Per-customer demand at the peak hour of the record (today's heat pumps and EVs)."""
    from ..profiles.weather import load_weather
    from .run import background_masks

    C = len(customers())
    hp, ev = background_masks(C)
    best = None
    for y in settings().weather_years:
        prof = year_profiles(y)
        total = prof["base"].sum(axis=0) + prof["hp"][hp].sum(axis=0) + prof["ev"][ev].sum(axis=0)
        h = int(np.argmax(total))
        if best is None or total[h] > best[2]:
            best = (y, h, float(total[h]))
    y, h, peak = best
    prof = year_profiles(y)
    t = float(load_weather(y)["t_air"].iloc[h])
    return {"base": prof["base"][:, h].astype(float), "hp": prof["hp"][:, h].astype(float),
            "ev": prof["ev"][:, h].astype(float), "year": y, "hour": h, "t_air": t, "peak_kw": peak}


def cap_kw() -> np.ndarray:
    cust = customers()
    return np.minimum(cust["kwp"].to_numpy() / A("pv_dc_ac_ratio"), cust["fuse_kva"].to_numpy()).astype(float)


def summer_snapshot(pv_mask: np.ndarray, pv_kw: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Net injection (kW) and reactive power (kvar) of the static summer worst case: each PV
    system in `pv_mask` at `static_pv_factor` × its rating (`pv_kw`, by default `cap_kw`), and the
    existing PV of the equipment records at the same share of its own rating, whatever the mask."""
    load = min_daytime_load()
    ex = existing_pv_kw()
    pv = np.where(pv_mask & (ex <= 0), (cap_kw() if pv_kw is None else pv_kw), ex) * A("static_pv_factor")
    # (cap_kw follows the DC/AC ratio, so the static method sees the same inverters)
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    return pv - load, -load * tanphi


def winter_snapshot(hp_mask: np.ndarray, ev_mask: np.ndarray, extra_kw: np.ndarray | None = None):
    w = winter_design_load()
    load = w["base"] + np.where(hp_mask, w["hp"], 0.0) + np.where(ev_mask, w["ev"], 0.0)
    if extra_kw is not None:
        load = load + extra_kw
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    return -load, -load * tanphi


# ------------------------------------------------------------------------------ static method

def nodal_pv_hc(model: NetModel, base_pv: np.ndarray | None = None, max_kw: float = 250.0) -> np.ndarray:
    """Largest PV (kW AC) at each house alone that keeps the summer snapshot within limits; the
    snapshot puts `static_pv_factor` of that rating on the grid."""
    C = model.C
    base_pv = np.zeros(C, dtype=bool) if base_pv is None else base_pv
    p0, q0 = summer_snapshot(base_pv)
    factor = A("static_pv_factor")
    lo, hi = np.zeros(C), np.full(C, max_kw)
    eye = np.eye(C)
    for _ in range(24):
        mid = (lo + hi) / 2.0
        P = p0[:, None] + eye * (factor * mid)[None, :]
        Q = np.repeat(q0[:, None], C, axis=1)
        ok = ~model.violations(model.solve(P, Q), P).export_side
        lo = np.where(ok, mid, lo)
        hi = np.where(ok, hi, mid)
    return lo


def nodal_import_hc(model: NetModel, hp_mask: np.ndarray, ev_mask: np.ndarray, max_kw: float = 250.0) -> np.ndarray:
    """Largest extra load (kW) at each house alone in the winter snapshot."""
    C = model.C
    p0, q0 = winter_snapshot(hp_mask, ev_mask)
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    lo, hi = np.zeros(C), np.full(C, max_kw)
    eye = np.eye(C)
    for _ in range(24):
        mid = (lo + hi) / 2.0
        P = p0[:, None] - eye * mid[None, :]
        Q = q0[:, None] - eye * (mid * tanphi)[None, :]
        ok = ~model.violations(model.solve(P, Q), P).import_side
        lo = np.where(ok, mid, lo)
        hi = np.where(ok, hi, mid)
    return lo


def static_adopters(model: NetModel, order: np.ndarray, hp_mask: np.ndarray, ev_mask: np.ndarray) -> int:
    """Largest m such that the first m adopters of `order` pass the static summer snapshot.

    Solar only meets the export side, so the winter snapshot (consumption) is not checked here;
    `hp_mask` and `ev_mask` are kept for the callers and do not change the count. The existing PV
    of the equipment records is in every column, firm. `order` may name fewer houses than the
    network has (a study's candidates); the count is at most its length, and -1 when the snapshot
    fails with nobody added (the existing PV alone breaks it)."""
    C, n = model.C, len(order)
    masks = np.zeros((C, n + 1), dtype=bool)
    for m in range(1, n + 1):
        masks[order[:m], m] = True
    load = min_daytime_load()
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    firm = existing_pv_kw() * A("static_pv_factor")
    P = np.where(masks, (cap_kw() * A("static_pv_factor"))[:, None], 0.0) + (firm - load)[:, None]
    Q = np.repeat((-load * tanphi)[:, None], n + 1, axis=1)
    ok_summer = ~model.violations(model.solve(P, Q), P).export_side
    bad = np.flatnonzero(~ok_summer)
    return int(bad[0] - 1) if len(bad) else n


# ------------------------------------------------------------------------------ time-series view

def uncontrolled_ok(model: NetModel, pv_mask: np.ndarray, hp_mask: np.ndarray, ev_mask: np.ndarray,
                    years, screen_v: float = 0.012, screen_pct: float = 6.0) -> bool:
    """True when the deployment has no export-side violation in any hour of `years`.

    A linear screen flags candidate hours; only those are solved with the nonlinear power flow.
    The screen margins are validated against full nonlinear runs (see validation page).
    """
    for y in years:
        scn = Scenario(year=y, pv=pv_mask, hp=hp_mask, ev=ev_mask)
        arr = scn.arrays()
        p, q = arr["p_net"].astype(float), arr["q"].astype(float)
        if len(model.export_critical_hours(p, q)):
            return False
    return True


def ts_adopters(model: NetModel, order: np.ndarray, hp_mask: np.ndarray, ev_mask: np.ndarray, years,
                lo: int) -> int:
    """Largest m (≥ lo, at most len(order)) such that the first m adopters run the whole record
    without violation."""
    C = model.C

    def ok(m: int) -> bool:
        mask = np.zeros(C, dtype=bool)
        mask[order[:m]] = True
        return uncontrolled_ok(model, mask, hp_mask, ev_mask, years)

    if not ok(lo):
        return lo
    hi = len(order) + 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo


# ------------------------------------------------------------------- consumption, hour by hour

IMPORT_DEVICES = ("ev", "hp", "flat")
CANDIDATES_PER_YEAR = 3      # hours of each year that each house's bisection solves first


def consumption_state(year: int, hp_mask: np.ndarray, ev_mask: np.ndarray,
                      extra_kw: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Net injection and reactive power (C, H) of one weather year with nobody's solar: today's
    heat pumps and EVs, plus `extra_kw` (C, H) of load. Solar only relieves the import side, so
    leaving it out, the existing PV included, is the safe side for consumption."""
    C = len(hp_mask)
    arr = Scenario(year=year, pv=np.zeros(C, dtype=bool), hp=hp_mask, ev=ev_mask,
                   extra_load_kw=extra_kw, existing_pv=False).arrays()
    return arr["p_net"].astype(float), arr["q"].astype(float)


def import_candidate_hours(model: NetModel, p: np.ndarray, q: np.ndarray,
                           top: int = CANDIDATES_PER_YEAR) -> np.ndarray:
    """(C, top): for each house, the hours of one year where the linear model leaves it the least
    room to add load on the import side.

    The room of house i in hour h is the smallest of: at each voltage check point, the distance
    to the lower limit over the drop one kW at i causes there; on each cable of i's path and at
    the transformer, the apparent power left, over the sqrt(1 + tan²φ) kVA one kW adds. It only
    ranks the hours for the search, so the nonlinear flow and the screen's margins can reorder
    them a little; `nodal_import_hc_ts` confirms its result on every hour of the year."""
    C, H = p.shape
    top = max(1, min(int(top), H))
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    per_kw = np.hypot(1.0, tanphi)
    v_lin = (model.v_noload[:, None] - model.dv_dp @ np.clip(-p, 0.0, None)
             + model.dv_dq @ np.clip(q, None, 0.0))
    room_v = np.clip(v_lin - model.v_min, 0.0, None)                       # (check point, H) pu
    drop = np.clip(model.dv_dp + model.dv_dq * tanphi, 1e-12, None)        # (check point, C) pu/kW
    room_l = np.clip(model.line_s_max_kw[:, None] - np.hypot(model.phi @ p, model.phi @ q), 0.0, None) / per_kw
    room_t = np.clip(model.trafo_s_max_kw - np.hypot(p.sum(axis=0), q.sum(axis=0)), 0.0, None) / per_kw
    out = np.empty((C, top), dtype=int)
    for i in range(C):
        room = np.minimum((room_v / drop[:, i:i + 1]).min(axis=0), room_t)
        path = model.phi[:, i] > 0
        if path.any():
            room = np.minimum(room, room_l[path].min(axis=0))
        out[i] = np.argpartition(room, top - 1)[:top]
    return out


def _bisect_nodal(model: NetModel, P0: np.ndarray, Q0: np.ndarray, rows: np.ndarray, owner: np.ndarray,
                  n: int, max_kw: float) -> np.ndarray:
    """The largest extra load of each of `n` trials that keeps its own columns of (P0, Q0) inside
    the import-side limits. Column c belongs to trial owner[c], whose load is added at customer
    rows[c]; every trial bisects at once, 24 halvings of [0, max_kw]."""
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    cols = np.arange(P0.shape[1])
    lo, hi = np.zeros(n), np.full(n, float(max_kw))
    for _ in range(24):
        mid = (lo + hi) / 2.0
        P, Q = P0.copy(), Q0.copy()
        P[rows, cols] -= mid[owner]
        Q[rows, cols] -= mid[owner] * tanphi
        bad = np.bincount(owner, weights=model.violations(model.solve(P, Q), P).import_side, minlength=n) > 0
        lo = np.where(bad, lo, mid)
        hi = np.where(bad, mid, hi)
    return lo


def import_baseline_violations(model: NetModel, hp_mask: np.ndarray, ev_mask: np.ndarray,
                               years=None) -> dict[int, int]:
    """Hours of each year of `years` in which today's grid (no solar, today's heat pumps and EVs)
    already breaks an import-side limit, before anyone adds load: the case in which
    `nodal_import_hc_ts` and `ts_import_adopters` return zero, which is then no measured capacity."""
    years = tuple(settings().weather_years if years is None else years)
    return {int(y): int(len(model.import_critical_hours(*consumption_state(y, hp_mask, ev_mask))))
            for y in years}


def nodal_import_hc_ts(model: NetModel, hp_mask: np.ndarray, ev_mask: np.ndarray, years=None,
                       max_kw: float = 250.0, top: int = CANDIDATES_PER_YEAR, max_rounds: int = 20) -> np.ndarray:
    """Largest extra load (kW, constant in every hour) at each house alone that every hour of
    `years` carries without an import-side violation: `nodal_import_hc` on the hourly record.

    A bisection over all houses at once, each on the union over the years of its own candidate
    hours (`import_candidate_hours`), with the record's peak hour (the static winter
    snapshot's) among them, so the result is never above the static one (but for the float32
    rounding of the hourly profiles, a bisection step at most). Each house's result is
    then confirmed on every hour of every year (`import_critical_hours`: the linear screen and
    the nonlinear flow); an hour its candidates missed joins them and that house is searched
    again, until every house is confirmed. If today's grid already breaks an import limit in
    some hour, no house can add anything and the result is zero (`import_baseline_violations`
    counts those hours, for the output to say so).
    """
    years = tuple(settings().weather_years if years is None else years)
    C = model.C
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    winter = winter_design_load()
    cand: list[dict[int, np.ndarray]] = [{} for _ in range(C)]
    for y in years:
        p, q = consumption_state(y, hp_mask, ev_mask)
        if len(model.import_critical_hours(p, q)):
            log.warning("today's grid breaks an import limit in %d: no house can add load", y)
            return np.zeros(C)
        hours = import_candidate_hours(model, p, q, top)
        peak = [winter["hour"]] if y == winter["year"] else []
        for j in range(C):
            cand[j][y] = np.union1d(hours[j], peak).astype(int)

    def search(houses: np.ndarray) -> np.ndarray:
        P, Q, rows, owner = [], [], [], []
        for y in years:
            p, q = consumption_state(y, hp_mask, ev_mask)
            for t, j in enumerate(houses):
                h = cand[j][y]
                P.append(p[:, h])
                Q.append(q[:, h])
                rows.append(np.full(len(h), j))
                owner.append(np.full(len(h), t))
        return _bisect_nodal(model, np.concatenate(P, axis=1), np.concatenate(Q, axis=1),
                             np.concatenate(rows), np.concatenate(owner), len(houses), max_kw)

    out = np.zeros(C)
    todo = np.arange(C)
    for _ in range(max_rounds):
        out[todo] = search(todo)
        failed: set[int] = set()
        for y in years:
            p, q = consumption_state(y, hp_mask, ev_mask)
            for j in todo:
                row_p, row_q = p[j].copy(), q[j].copy()
                p[j], q[j] = row_p - out[j], row_q - out[j] * tanphi
                missed = model.import_critical_hours(p, q)
                p[j], q[j] = row_p, row_q
                if len(missed):
                    failed.add(int(j))
                    cand[j][y] = np.union1d(cand[j][y], missed).astype(int)
        if not failed:
            return out
        log.info("nodal import capacity: %d houses met a limit outside their candidate hours; searching again",
                 len(failed))
        todo = np.array(sorted(failed))
    raise RuntimeError(f"nodal_import_hc_ts: {len(todo)} houses not confirmed after {max_rounds} rounds")


def ts_import_adopters(model: NetModel, order: np.ndarray, device: str = "ev", hp_mask: np.ndarray | None = None,
                       ev_mask: np.ndarray | None = None, years=None, *, flat_kw: float | None = None) -> int:
    """How many houses of `order`, one after another, can add `device` with every hour of
    `years` inside the import-side limits (no solar, today's heat pumps and EVs).

    "ev" adds the house's own uncontrolled home charging, "hp" its own heat pump, "flat" a
    constant `flat_kw` (by default the charger rating, the static study's convention for a new
    charger). A house that already has the EV or the heat pump is skipped, so the count is of
    new devices. Each count is checked on every hour (the linear screen and the nonlinear flow);
    if today's grid already breaks a limit the count is zero.
    """
    if device not in IMPORT_DEVICES:
        raise ValueError(f"device must be one of {IMPORT_DEVICES}, not {device!r}")
    C = model.C
    if hp_mask is None or ev_mask is None:
        from .run import background_masks
        hp_bg, ev_bg = background_masks(C)
        hp_mask = hp_bg if hp_mask is None else hp_mask
        ev_mask = ev_bg if ev_mask is None else ev_mask
    years = list(settings().weather_years if years is None else years)
    has = {"ev": ev_mask, "hp": hp_mask, "flat": np.zeros(C, dtype=bool)}[device]
    new = np.array([c for c in order if not has[c]], dtype=int)
    kw = A("ev_charger_kw") if flat_kw is None else float(flat_kw)

    def ok(m: int) -> bool:
        add = np.zeros(C, dtype=bool)
        add[new[:m]] = True
        hp = hp_mask | add if device == "hp" else hp_mask
        ev = ev_mask | add if device == "ev" else ev_mask
        for i, y in enumerate(years):
            extra = None
            if device == "flat":
                extra = np.zeros((C, len(year_index(y))))
                extra[add] = kw
            p, q = consumption_state(y, hp, ev, extra)
            if len(model.import_critical_hours(p, q)):
                years.insert(0, years.pop(i))          # the year that failed is tried first next time
                return False
        return True

    lo, hi = 0, len(new)
    if not ok(lo):
        return 0
    if ok(hi):
        return hi
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo

