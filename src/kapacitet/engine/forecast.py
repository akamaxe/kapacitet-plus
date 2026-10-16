"""What the envelopes cost once you stop knowing the future.

Every published number in this repository is computed with perfect foresight: the envelope
for 11:00 is allocated knowing exactly what the sun and the houses will do at 11:00. A real
envelope is issued the day before, and the gap between the two is the caveat that most
limits what the figures mean. This module measures the gap instead of hedging about it.

The measurement rests on one property. Call a planning state **dominating** if, hour by
hour, it assumes at least as much generation and at most as much load as actually occurs.
Every export-side constraint is monotone in those two quantities, so an envelope feasible
against a dominating state is feasible against what happens — including in the hours the
planner never flagged as critical, which is where a point forecast fails. Safety then needs
no forecast skill at all. What it costs is how loose the bound is.

Two planning states are measured, and they bracket any real operator.

**Climatological** (`climatological`): the extremes the same fortnight reached in the other
weather years — generation at its highest, demand at its lowest, hour of the day by hour of
the day. It is weather-blind — a clock and eleven years of history, no forecast — so it is
the pessimistic end of the bracket. It is a bound in all but a handful of hours, and
`exceedance_audit` counts those rather than claiming otherwise.

The climatology leaves the operated year out, which keeps it honest about the year it
plans. For the reference year that is also strictly causal, since every other weather year
precedes it; planning an early year against the set uses years that came later, so the
eleven-year medians are leave-one-out rather than real-time. Nothing in it is foresight of
the hour being planned, which is the quantity under test.

**Persistence** (`persistence`): yesterday at this hour. A point forecast, not a bound, and
the weakest forecast an operator would use. It is here to show what goes wrong without
domination, which is the part that is easy to get wrong: the failure is not that envelopes
come out slightly too generous, it is that hours drop out of the critical set altogether and
the customer is handed its inverter rating.

Real day-ahead practice sits between the two, so perfect foresight and the climatological
bound bracket the cost of the forecast rather than guessing at it.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from scipy.ndimage import maximum_filter1d, minimum_filter1d

from ..config import A, settings
from ..profiles.pv import load_ac_per_kwp
from ..timeindex import year_index
from . import doe
from .netmodel import NetModel
from .scenario import Scenario, customers, year_profiles

WINDOW_DAYS = 14        # calendar half-window of the climatological envelope
SCHEMES = ("static", "climatological", "persistence", "foresight")


@dataclass
class Issued:
    """One year of envelopes issued ahead of time and checked against what happened."""

    scheme: str
    critical_hours: int          # hours the planner issued an envelope for
    violation_hours: int         # hours the published envelope failed to keep, against actual
    curtailed_kwh: float
    curtailed_share_pct: float   # of the non-firm customers' export
    export_kwh: float
    hours_limited: int           # hours in which somebody was actually cut

    @property
    def safe(self) -> bool:
        return self.violation_hours == 0


# ----------------------------------------------------------------- the climatological bound

def _day_shaped(year: int, a: np.ndarray) -> np.ndarray:
    """(C, 365, 24), with 29 February dropped so every year lines up on the calendar."""
    idx = year_index(year)
    keep = np.asarray(~((idx.month == 2) & (idx.day == 29)))
    a = a[:, keep]
    return a.reshape(a.shape[0], -1, 24)


def _restore_leap(year: int, days: np.ndarray) -> np.ndarray:
    """Flatten (C, 365, 24) back onto the year's own index, repeating 28 February if needed."""
    idx = year_index(year)
    leap = np.asarray((idx.month == 2) & (idx.day == 29))
    flat = days.reshape(days.shape[0], -1)
    if not leap.any():
        return flat
    out = np.empty((days.shape[0], len(idx)), dtype=flat.dtype)
    out[:, ~leap] = flat
    out[:, leap] = days[:, 58, :]            # 0-based 28 February of a 365-day year
    return out


def _window_extreme(year: int, per_year, extreme: str, window_days: int) -> np.ndarray:
    """The highest (or lowest) the other weather years reached at this hour of this fortnight.

    The window matters: a single calendar hour has one sample per weather year, far too few
    to bound anything, so the extreme is taken over the neighbouring days as well. The hour
    of the day is never mixed, because that is the information the clock gives for free.
    """
    years = [y for y in settings().weather_years if y != year]
    if not years:
        raise ValueError(f"{year} is the only weather year, so there is no history to bound it")
    stack = np.stack([_day_shaped(y, per_year(y)) for y in years])
    if extreme == "max":
        hist = maximum_filter1d(stack.max(axis=0), size=2 * window_days + 1, axis=1, mode="nearest")
    else:
        hist = minimum_filter1d(stack.min(axis=0), size=2 * window_days + 1, axis=1, mode="nearest")
    return _restore_leap(year, hist)


@lru_cache(maxsize=16)       # one per weather year; the capacity search walks all of them
def pv_upper_bound(year: int, window_days: int = WINDOW_DAYS) -> np.ndarray:
    """Per-kWp AC output (C, H) no higher than the same fortnight of any other year reached."""
    return _window_extreme(year, load_ac_per_kwp, "max", window_days)


_LOAD_BOUND: dict[tuple, np.ndarray] = {}


def load_lower_bound(year: int, hp: np.ndarray, ev: np.ndarray,
                     window_days: int = WINDOW_DAYS) -> np.ndarray:
    """Demand (C, H) no lower than the same fortnight of any other year came down to.

    The composition has to match the deployment being planned, because a heat pump changes
    the shape of the floor and not just its level, so the masks are applied before the
    minimum is taken rather than after. Cached on the masks, because the capacity search
    asks for the same floor once per candidate and it costs ten years of profiles to build.
    """
    key = (year, window_days, hp.tobytes(), ev.tobytes())
    if key not in _LOAD_BOUND:
        if len(_LOAD_BOUND) > 24:
            _LOAD_BOUND.clear()
        _LOAD_BOUND[key] = _window_extreme(year, lambda y: _total_load(y, hp, ev), "min", window_days)
    return _LOAD_BOUND[key]


def _total_load(year: int, hp: np.ndarray, ev: np.ndarray) -> np.ndarray:
    prof = year_profiles(year)
    return (prof["base"] * A("household_load_factor")
            + np.where(hp[:, None], prof["hp"], 0.0)
            + np.where(ev[:, None], prof["ev"], 0.0)).astype(np.float32)


def exceedance_audit(year: int, hp: np.ndarray | None = None, ev: np.ndarray | None = None,
                     window_days: int = WINDOW_DAYS) -> dict:
    """How often the year beat its own history, which is the one way this bound can fail.

    Both sides are audited on the neighbourhood total, because that is what the transformer
    sees and the transformer is what binds on this grid.
    """
    cust = customers()
    kwp = cust["kwp"].to_numpy()
    actual = load_ac_per_kwp(year)
    bound = pv_upper_bound(year, window_days)
    agg_a = (actual * kwp[:, None]).sum(axis=0)
    agg_b = (bound * kwp[:, None]).sum(axis=0)
    over = agg_a > agg_b + 1e-6
    day = agg_b > 0.5
    out = {
        "year": year, "window_days": window_days,
        "pv_bound_over_measured_energy": float(bound.sum() / actual.sum()),
        "pv_customer_hours_over": int((actual > bound + 1e-6).sum()),
        "pv_aggregate_hours_over": int(over.sum()),
        "pv_worst_aggregate_ratio": float((agg_a[day] / agg_b[day]).max()) if day.any() else 0.0,
        "pv_worst_aggregate_kw": float((agg_a - agg_b)[over].max()) if over.any() else 0.0,
    }
    if hp is None or ev is None:
        return out
    la, lb = _total_load(year, hp, ev), load_lower_bound(year, hp, ev, window_days)
    sa, sb = la.sum(axis=0), lb.sum(axis=0)
    under = sa < sb - 1e-6
    out.update({
        "load_bound_under_measured_energy": float(lb.sum() / la.sum()),
        "load_customer_hours_under": int((la < lb - 1e-6).sum()),
        "load_aggregate_hours_under": int(under.sum()),
        "load_worst_aggregate_kw": float((sb - sa)[under].max()) if under.any() else 0.0,
    })
    return out


# ------------------------------------------------------------------------- planning states

def _arrays(pv_kw: np.ndarray, load_kw: np.ndarray) -> dict[str, np.ndarray]:
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    return {"load": load_kw.astype(np.float32), "pv": pv_kw.astype(np.float32),
            "p_net": (pv_kw - load_kw).astype(np.float32),
            "q": (-load_kw * tanphi).astype(np.float32)}


def _inverter_capped(pv_mask: np.ndarray, per_kwp: np.ndarray) -> np.ndarray:
    """The same cap `Scenario.arrays` applies, so two states differ only in the weather."""
    cust = customers()
    kwp = cust["kwp"].to_numpy()
    ilr = A("pv_dc_ac_ratio")
    inv = np.minimum(kwp / ilr, cust["fuse_kva"].to_numpy())
    per = np.minimum(per_kwp, 1.0 / ilr)
    return np.where(pv_mask[:, None], np.minimum(per * kwp[:, None], inv[:, None]), 0.0)


def climatological(year: int, pv_mask: np.ndarray, hp: np.ndarray, ev: np.ndarray,
                   window_days: int = WINDOW_DAYS) -> dict:
    """A dominating state: both sides at the extreme the same fortnight has reached before.

    This differs from the static worst case in exactly one respect: it follows the calendar
    instead of assuming nameplate generation against minimum load all year round. That
    difference is what the time dimension is worth, with no weather forecast in it.
    """
    pv = _inverter_capped(pv_mask, pv_upper_bound(year, window_days))
    load = load_lower_bound(year, hp, ev, window_days)
    return _arrays(pv, load)


def persistence(year: int, pv_mask: np.ndarray, hp: np.ndarray, ev: np.ndarray,
                lag_hours: int = 24) -> dict:
    """Yesterday at this hour, as the forecast for today at this hour.

    Not a bound, and not meant to be: it is the strawman that shows what domination buys.
    The first day has no day before it, so it forecasts itself — one day of perfect foresight
    in 365, stated here rather than hidden.
    """
    pv = _inverter_capped(pv_mask, year_profiles(year)["pv_per_kwp"])
    load = _total_load(year, hp, ev)
    out = []
    for a in (pv, load):
        s = np.empty_like(a, dtype=np.float32)
        s[:, :lag_hours] = a[:, :lag_hours]
        s[:, lag_hours:] = a[:, :-lag_hours]
        out.append(s)
    return _arrays(*out)


def planning_state(scheme: str, year: int, pv_mask: np.ndarray, hp: np.ndarray, ev: np.ndarray,
                   actual: dict[str, np.ndarray], window_days: int = WINDOW_DAYS) -> dict:
    """The state the envelope is allocated against, for one rung of the ladder.

    The static rung has no planning state — it issues no envelope — so it is handled by the
    callers that need it rather than faked with one here.
    """
    if scheme == "climatological":
        return climatological(year, pv_mask, hp, ev, window_days)
    if scheme == "persistence":
        return persistence(year, pv_mask, hp, ev)
    if scheme == "foresight":
        return actual
    raise ValueError(f"{scheme!r} issues no envelope; expected climatological, persistence "
                     f"or foresight")


def forecast_error(actual: dict[str, np.ndarray], predicted: dict[str, np.ndarray]) -> dict:
    """How wrong a point forecast is, in the terms that matter for an envelope."""
    a = np.asarray(actual["p_net"], dtype=float).sum(axis=0)
    p = np.asarray(predicted["p_net"], dtype=float).sum(axis=0)
    err = p - a                                     # positive: the forecast over-predicted export
    export = a > 0
    return {
        "mae_kw": float(np.abs(err).mean()),
        "rmse_kw": float(np.sqrt((err ** 2).mean())),
        "bias_kw": float(err.mean()),
        "p95_underprediction_kw": float(np.percentile(-err[export], 95)) if export.any() else 0.0,
        "hours_underpredicted": int((err[export] < 0).sum()),
    }


# --------------------------------------------------------------------------------- issuing

def issue(model: NetModel, planning: dict[str, np.ndarray], actual: dict[str, np.ndarray],
          nonfirm: np.ndarray, scheme: str, policy: str = "maxmin") -> Issued:
    """Allocate against the planning state, publish, then check the result against reality.

    Outside the hours the planner called critical the published envelope is the customer's
    own inverter rating, which is what makes domination the whole game: under a dominating
    state those hours cannot be critical in reality either, and under a point forecast they
    very much can.
    """
    r = doe.compute(model, planning, nonfirm, policy=policy)
    H = actual["p_net"].shape[1]
    nonfirm = np.asarray(nonfirm, dtype=int)
    published = np.repeat(r.cap[:, None], H, axis=1)
    if len(r.hours):
        published[:, r.hours] = r.E

    p = np.asarray(actual["p_net"], dtype=float).copy()
    desired = np.clip(p[nonfirm], 0.0, None)
    granted = np.minimum(desired, published)
    p[nonfirm] = np.where(desired > 0, granted, p[nonfirm])
    hours = model.export_critical_hours(p, np.asarray(actual["q"], dtype=float))

    curtailed = desired - granted
    export = float(desired.sum())
    return Issued(
        scheme=scheme,
        critical_hours=int(len(r.hours)),
        violation_hours=int(len(hours)),
        curtailed_kwh=float(curtailed.sum()),
        curtailed_share_pct=float(curtailed.sum() / export * 100.0) if export else 0.0,
        export_kwh=export,
        hours_limited=int((curtailed.sum(axis=0) > 1e-3).sum()),
    )


# ------------------------------------------------------------------- capacity under a scheme

def _pv_mask(order: np.ndarray, k: int, C: int) -> np.ndarray:
    m = np.zeros(C, dtype=bool)
    m[order[:k]] = True
    return m


def multiyear(model: NetModel, order: np.ndarray, ms: int, k: int, years, hp: np.ndarray,
              ev: np.ndarray, scheme: str, *, window_days: int = WINDOW_DAYS,
              policy: str = "maxmin", check_safety: bool = False) -> dict:
    """Curtailment of the non-firm adopters `order[ms:k]` under one scheme, over `years`.

    The denominator is the one the published capacity search uses — the annual PV energy of
    those customers, not their export — so the result can be compared against `z_star_pct`
    without converting anything.
    """
    C = model.C
    nf = order[ms:k]
    pvm = _pv_mask(order, k, C)
    curt = pot = 0.0
    violations = 0
    per_year = []
    for y in years:
        actual = Scenario(year=y, pv=pvm, hp=hp, ev=ev).arrays()
        pot_y = float(actual["pv"][nf].sum())
        if len(nf) == 0:
            per_year.append({"year": y, "curtailed_kwh": 0.0, "potential_kwh": pot_y,
                             "violation_hours": 0})
            continue
        if scheme == "static":
            p_s, q_s = _static_snapshot(order, ms, C)
            kappa, _ = doe.fixed_cap(model, actual, nf, p_s, q_s)
            cap = doe.customer_caps()[nf]
            c_y = float(np.maximum(np.clip(actual["p_net"][nf], 0, None) - kappa * cap[:, None], 0).sum())
            v_y = 0                     # worst-case feasible by construction
        else:
            plan = planning_state(scheme, y, pvm, hp, ev, actual, window_days)
            r = issue(model, plan, actual, nf, scheme, policy=policy)
            c_y, v_y = r.curtailed_kwh, r.violation_hours
        curt += c_y
        pot += pot_y
        violations += v_y
        per_year.append({"year": y, "curtailed_kwh": c_y, "potential_kwh": pot_y,
                         "violation_hours": v_y})
    return {"scheme": scheme, "adopters": int(k), "non_firm": int(len(nf)),
            "curtailed_kwh": curt, "potential_kwh": pot,
            "z_pct": 100.0 * curt / pot if pot > 0 else 0.0,
            "violation_hours": violations if (check_safety or scheme != "static") else 0,
            "per_year": per_year}


def _static_snapshot(order: np.ndarray, ms: int, C: int):
    from .hosting import summer_snapshot
    return summer_snapshot(_pv_mask(order, ms, C))


def adopters(model: NetModel, order: np.ndarray, ms: int, lo: int, years, hp: np.ndarray,
             ev: np.ndarray, scheme: str, z_star: float, *, window_days: int = WINDOW_DAYS,
             policy: str = "maxmin", hi: int | None = None) -> tuple[int, dict]:
    """The most adopters this scheme carries inside the curtailment budget, and the cost there.

    `lo` is a count already known to be acceptable — the published search starts the DOE case
    from the time-series capacity — so the bisection never has to prove the trivial end.

    `hi` saves the expensive end the same way. A dominating state is critical in every hour
    the real one is, and allocates from less headroom when it is, so its envelope is never
    above the perfect-foresight envelope and its capacity is never above the published one.
    Passing that as the ceiling keeps the bisection out of a region it cannot reach, which on
    this grid is most of the search.
    """
    def ok(k: int) -> bool:
        return multiyear(model, order, ms, k, years, hp, ev, scheme,
                         window_days=window_days, policy=policy)["z_pct"] <= z_star

    hi = model.C if hi is None else min(int(hi), model.C)
    if hi <= lo:
        return lo, multiyear(model, order, ms, lo, years, hp, ev, scheme,
                             window_days=window_days, policy=policy, check_safety=True)
    k = hi if ok(hi) else lo
    if k != hi:
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if ok(mid):
                lo = mid
            else:
                hi = mid
        k = lo
    return k, multiyear(model, order, ms, k, years, hp, ev, scheme,
                        window_days=window_days, policy=policy, check_safety=True)


# ------------------------------------------------------------------------------- reporting

def ladder(model: NetModel, show: dict, window_days: int = WINDOW_DAYS,
           policy: str = "maxmin") -> list[Issued]:
    """Every rung on the published showcase deployment, in one reference year.

    The static rung is the alternative on offer today: no envelope, just the share of each
    inverter that survives the worst case. On a grid already at its static limit that share
    is close to nothing, which is the reason any of this exists.
    """
    from .hosting import summer_snapshot
    from .run import background_masks

    year = show["year"]
    C = model.C
    pv = np.zeros(C, dtype=bool)
    pv[show["pv_cids"]] = True
    firm = np.zeros(C, dtype=bool)
    firm[show["firm_cids"]] = True
    nonfirm = np.asarray(show["nonfirm_cids"], dtype=int)
    hp, ev = background_masks(C)
    actual = Scenario(year=year, pv=pv, hp=hp, ev=ev).arrays()

    p0, q0 = summer_snapshot(firm)
    kappa, _ = doe.fixed_cap(model, actual, nonfirm, p0, q0)
    desired = np.clip(actual["p_net"][nonfirm].astype(float), 0.0, None)
    cut = np.maximum(desired - (doe.customer_caps()[nonfirm] * kappa)[:, None], 0.0)
    export = float(desired.sum())
    rows = [Issued(scheme="static", critical_hours=0, violation_hours=0,
                   curtailed_kwh=float(cut.sum()),
                   curtailed_share_pct=float(cut.sum() / export * 100.0) if export else 0.0,
                   export_kwh=export,
                   hours_limited=int((cut.sum(axis=0) > 1e-3).sum()))]
    for scheme in ("climatological", "persistence", "foresight"):
        plan = planning_state(scheme, year, pv, hp, ev, actual, window_days)
        rows.append(issue(model, plan, actual, nonfirm, scheme, policy=policy))
    return rows


LADDER_LABEL = {
    "static": "static worst case, no envelope",
    "climatological": "day ahead, no forecast",
    "persistence": "day ahead, yesterday's weather",
    "foresight": "perfect foresight (published)",
}


def ladder_text(rows: list[Issued], year: int, capacity: dict | None = None) -> str:
    """The same figures as a page a person can read."""
    lines = [
        f"Envelopes issued before the fact, reference year {year}",
        "=" * 64,
        "What the published figures owe to knowing the weather in advance.",
        "",
        f"   {'scheme':<32}{'issued h':>9}{'violations':>11}{'kWh cut':>10}{'% export':>10}",
    ]
    for r in rows:
        issued = "-" if r.scheme == "static" else f"{r.critical_hours}"
        lines.append(f"   {LADDER_LABEL.get(r.scheme, r.scheme):<32}{issued:>9}"
                     f"{r.violation_hours:>11}{r.curtailed_kwh:>10,.0f}{r.curtailed_share_pct:>10.2f}")
    lines += [
        "",
        "Only a planning state that assumes at least as much generation and at most as much",
        "load as actually occurs keeps every hour inside the limits. Yesterday's weather is a",
        "forecast, not a bound: it curtails less than the published envelopes and breaks the",
        "limits while doing it, because the hours it fails to call critical get no envelope",
        "at all and the customer runs to its inverter rating.",
    ]
    if capacity:
        kw = capacity["median_kw"]
        lines += [
            "",
            f"Capacity at the same {capacity['z_star_pct']} % curtailment budget, median of "
            f"{capacity['n_orders']} adoption orders:",
            f"   {'static':<32}{kw['static']:>9.0f} kW",
            f"   {'day ahead, no forecast':<32}{kw['climatological']:>9.0f} kW",
            f"   {'perfect foresight (published)':<32}{kw['doe']:>9.0f} kW",
            "",
            f"So {capacity['foresight_premium_kw']:.0f} kW of the published capacity is what a "
            f"forecast buys, and the rest",
            "survives without one. A real day-ahead forecast lands between the two.",
        ]
    return "\n".join(lines)
