"""What the envelopes cost once you stop knowing the future.

Every published number in this repository is computed with perfect foresight: the envelope
for 11:00 is allocated knowing exactly what the sun and the houses will do at 11:00. A real
envelope is issued the day before, and the gap between the two is the caveat that most
limits what the figures mean. This module measures the gap instead of hedging about it.

The measurement rests on one property. Call a planning state **dominating** if, hour by
hour, it assumes at least as much generation and at most as much load as actually occurs.
Every export-side constraint is monotone in those two quantities, so an envelope feasible
against a dominating state is feasible against what happens — including in the hours the
planner never flagged as critical, which is where a point forecast fails. That implication
is exact. Whether a state issued the day before does dominate is not, and cannot be known
when it is issued: what it costs is how loose the bound is, and what it risks is how often
the year beats it.

Two planning states are measured, and they bracket any real operator.

**Climatological** (`climatological`): the extremes the same fortnight reached in the other
weather years — generation at its highest, demand at its lowest, hour of the day by hour of
the day — widened by two margins, `dayahead_pv_margin_pct` above the generation and
`dayahead_load_margin_pct` below the demand. It is weather-blind — a clock and eleven years
of history, no forecast — so it is the pessimistic end of the bracket. It is an empirical
bound, not a guarantee: a year can beat the other ten by more than the margin. So every
issue audits itself against what happened (`plan_exceedance`, in `issue`), and
`exceedance_audit` does the same for the neighbourhood's record; both counts are published,
never assumed to be zero.

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

from dataclasses import asdict, dataclass, field

import numpy as np
from scipy.ndimage import maximum_filter1d, minimum_filter1d

from ..cache import memo, on_clear
from ..config import A, settings
from ..profiles.pv import load_ac_per_kwp
from ..timeindex import local_standard, year_index
from . import doe
from .delivery import share_pct
from .netmodel import NetModel
from .scenario import Scenario, customers, year_profiles

WINDOW_DAYS = 14        # calendar half-window of the climatological envelope
SCHEMES = ("static", "climatological", "persistence", "foresight")
EXCEED_TOL_KW = 1e-4         # one customer-hour beats its plan above this: float32 rounding is below
EXCEED_AGG_TOL_KW = 1e-3     # the neighbourhood total, summed over every house
FIRST_HOURS = 5              # how many of the first exceeding hours an audit names


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
    exceedance: dict | None = None   # where what happened beat the plan (plan_exceedance)
    # hours the envelopes broke a limit beyond the firm state against the planning state they
    # were allocated on, every hour of the year solved (doe.verify_full_year); None unchecked
    planning_full_year_violations: int | None = None
    # limited hours in which the real state with nobody curtailed breaks no export-side limit
    # (a full nonlinear solve of those hours); None unchecked
    unnecessary_hours: int | None = None
    # (N,) kWh curtailed per non-firm customer, in the order issued; not part of the payload
    curtailed_by_customer: np.ndarray | None = field(default=None, repr=False, compare=False)

    @property
    def safe(self) -> bool:
        return self.violation_hours == 0


# ----------------------------------------------------------------- the climatological bound

def _leap_day(year: int) -> np.ndarray:
    """The hours of 29 February on the study's own calendar (UTC+1), not on UTC's.

    The index is in UTC, and UTC's 29 February starts an hour later than the study's. Masked
    there, the leap day lost 01:00 to 00:00 of 1 March and was bounded an hour late, so at
    sunrise its bound was the record of the hour before sunrise.
    """
    local = local_standard(year_index(year))
    return np.asarray((local.month == 2) & (local.day == 29))


def _day_shaped(year: int, a: np.ndarray) -> np.ndarray:
    """(C, 365, 24), with 29 February dropped so every year lines up on the calendar."""
    a = a[:, ~_leap_day(year)]
    return a.reshape(a.shape[0], -1, 24)


def _restore_leap(year: int, days: np.ndarray) -> np.ndarray:
    """Flatten (C, 365, 24) back onto the year's own index, repeating 28 February if needed."""
    leap = _leap_day(year)
    flat = days.reshape(days.shape[0], -1)
    if not leap.any():
        return flat
    out = np.empty((days.shape[0], len(leap)), dtype=flat.dtype)
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


@memo(maxsize=16)            # one per weather year; the capacity search walks all of them
def pv_upper_bound(year: int, window_days: int = WINDOW_DAYS) -> np.ndarray:
    """Per-kWp AC output (C, H) no higher than the same fortnight of any other year reached."""
    return _window_extreme(year, load_ac_per_kwp, "max", window_days)


_LOAD_BOUND: dict[tuple, np.ndarray] = {}
on_clear(_LOAD_BOUND.clear)


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



def load_upper_bound(year: int, hp: np.ndarray, ev: np.ndarray,
                     window_days: int = WINDOW_DAYS) -> np.ndarray:
    """Demand (C, H) no higher than the same fortnight of any other year reached: the ceiling an
    import envelope is planned against, as the floor is for export. Cached like the floor."""
    key = ("max", year, window_days, hp.tobytes(), ev.tobytes())
    if key not in _LOAD_BOUND:
        if len(_LOAD_BOUND) > 24:
            _LOAD_BOUND.clear()
        _LOAD_BOUND[key] = _window_extreme(year, lambda y: _total_load(y, hp, ev), "max", window_days)
    return _LOAD_BOUND[key]

def _total_load(year: int, hp: np.ndarray, ev: np.ndarray) -> np.ndarray:
    prof = year_profiles(year)
    return (prof["base"] * A("household_load_factor")
            + np.where(hp[:, None], prof["hp"], 0.0)
            + np.where(ev[:, None], prof["ev"], 0.0)).astype(np.float32)


def day_ahead_margins(pv_margin: float | None = None,
                      load_margin: float | None = None) -> tuple[float, float]:
    """The two margins of the day-ahead bound, as fractions (0.03 is 3 %).

    Left out, each comes from the assumptions (`dayahead_pv_margin_pct`,
    `dayahead_load_margin_pct`); passing 0 gives the bare record of the other years.
    """
    pv = A("dayahead_pv_margin_pct") / 100.0 if pv_margin is None else float(pv_margin)
    load = A("dayahead_load_margin_pct") / 100.0 if load_margin is None else float(load_margin)
    if pv < 0 or not 0 <= load < 1:
        raise ValueError(f"day-ahead margins out of range: pv {pv}, load {load} (fractions)")
    return pv, load


def exceedance_audit(year: int, hp: np.ndarray | None = None, ev: np.ndarray | None = None,
                     window_days: int = WINDOW_DAYS, *, pv_margin: float | None = None,
                     load_margin: float | None = None) -> dict:
    """How often the year beat the bound it is planned against, the one way this bound fails.

    The bound is the record of the other years widened by the same margins `climatological`
    applies (the assumptions unless given). Both sides are audited on the neighbourhood total,
    because that is what the transformer sees and the transformer is what binds on this grid.
    `pv_worst_aggregate_ratio_unmargined` keeps the ratio against the bare record, which is
    the evidence the PV margin is sized on.
    """
    pm, lm = day_ahead_margins(pv_margin, load_margin)
    cust = customers()
    kwp = cust["kwp"].to_numpy()
    actual = load_ac_per_kwp(year)
    record = pv_upper_bound(year, window_days)
    bound = record * (1.0 + pm)
    agg_a = (actual * kwp[:, None]).sum(axis=0)
    agg_r = (record * kwp[:, None]).sum(axis=0)
    agg_b = agg_r * (1.0 + pm)
    over = agg_a > agg_b + 1e-6
    day = agg_r > 0.5
    out = {
        "year": year, "window_days": window_days,
        "pv_margin_pct": round(pm * 100.0, 6), "load_margin_pct": round(lm * 100.0, 6),
        "pv_bound_over_measured_energy": float(bound.sum() / actual.sum()),
        # per kWp against the per-kWp bound, before any inverter cap: not plan_exceedance's
        # pv_customer_hours_over, which compares capped kW with the plan an issue was made on
        "pv_customer_hours_over_per_kwp": int((actual > bound + 1e-6).sum()),
        "pv_aggregate_hours_over": int(over.sum()),
        "pv_worst_aggregate_ratio": float((agg_a[day] / agg_b[day]).max()) if day.any() else 0.0,
        "pv_worst_aggregate_ratio_unmargined": float((agg_a[day] / agg_r[day]).max()) if day.any() else 0.0,
        "pv_worst_aggregate_kw": float((agg_a - agg_b)[over].max()) if over.any() else 0.0,
    }
    if hp is None or ev is None:
        return out
    la = _total_load(year, hp, ev)
    lb = load_lower_bound(year, hp, ev, window_days) * (1.0 - lm)
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
                   window_days: int = WINDOW_DAYS, *, pv_margin: float | None = None,
                   load_margin: float | None = None) -> dict:
    """A state meant to dominate: both sides past the extreme the same fortnight has reached.

    This differs from the static worst case in exactly one respect: it follows the calendar
    instead of assuming nameplate generation against minimum load all year round. That
    difference is what the time dimension is worth, with no weather forecast in it.

    The margins (fractions; the assumptions unless given, see `day_ahead_margins`) widen the
    record: the PV one before the inverter cap, so the plan never assumes more than a house
    can inject, the load one on the floor. The cached bounds stay the bare record.
    """
    pm, lm = day_ahead_margins(pv_margin, load_margin)
    pv = _inverter_capped(pv_mask, pv_upper_bound(year, window_days) * (1.0 + pm))
    load = load_lower_bound(year, hp, ev, window_days) * (1.0 - lm)
    return _arrays(pv, load)



def import_planning_state(year: int, hp: np.ndarray, ev: np.ndarray,
                          window_days: int = WINDOW_DAYS, *, load_margin: float | None = None) -> dict:
    """The state an import envelope issued the day before is allocated against: the mirror of
    `climatological`. No generation at all, which no real hour can undercut, and demand at the
    ceiling the same fortnight of the other years reached, widened by the day-ahead load margin
    (`dayahead_load_margin_pct`, applied above the record here as it is below it there).

    `ev` names the chargers counted in the demand: the flexible ones are left out, since their
    draw is what the envelope sets."""
    _, lm = day_ahead_margins(load_margin=load_margin)
    load = load_upper_bound(year, hp, ev, window_days) * (1.0 + lm)
    return _arrays(np.zeros_like(load), load)

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
    if scheme == "intraday":
        from . import intraday
        return intraday.planning_state(year, pv_mask, hp, ev, window_days)
    if scheme == "nowcast":
        from . import intraday
        return intraday.forecast_planning_state(year, pv_mask, hp, ev, window_days)
    raise ValueError(f"{scheme!r} issues no envelope; expected climatological, persistence, "
                     f"intraday, nowcast or foresight")


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


def _beaten(plan: dict[str, np.ndarray], actual: dict[str, np.ndarray]):
    """(C, H) kW the actual PV ran above the plan and the actual load below it, and (H,) kW the
    neighbourhood's net injection ran above the plan's. Positive is where the plan was beaten."""
    g = lambda d, k: np.asarray(d[k], dtype=float)  # noqa: E731
    pv = g(actual, "pv") - g(plan, "pv")
    load = g(plan, "load") - g(actual, "load")
    net = g(actual, "p_net").sum(axis=0) - g(plan, "p_net").sum(axis=0)
    return pv, load, net


def exceedance_hours(plan: dict[str, np.ndarray], actual: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Hour by hour (H,), what `plan_exceedance` counts: the houses whose PV beat the plan, the
    houses whose load came in under it, and whether the neighbourhood total was not dominated."""
    pv, load, net = _beaten(plan, actual)
    return {"pv_customers": (pv > EXCEED_TOL_KW).sum(axis=0),
            "load_customers": (load > EXCEED_TOL_KW).sum(axis=0),
            "pv_aggregate": pv.sum(axis=0) > EXCEED_AGG_TOL_KW,
            "load_aggregate": load.sum(axis=0) > EXCEED_AGG_TOL_KW,
            "net_aggregate": net > EXCEED_AGG_TOL_KW}


def plan_exceedance(plan: dict[str, np.ndarray], actual: dict[str, np.ndarray],
                    nonfirm: np.ndarray | None = None) -> dict:
    """Where what happened beat the plan an envelope was issued against: the audit of one issue.

    Domination is what makes an envelope issued ahead safe, and for a state issued the day
    before it is a bet on the record, so every issue counts where it lost: customer-hours with
    more PV than planned or less load, the same on the neighbourhood total, and hours whose
    total net injection the plan did not dominate (the one the transformer sees), with the
    worst kW of each and the first hours (indices into the year, interval start in UTC).
    With `nonfirm`, the customer-hour counts are also given for the houses holding envelopes.
    A perfect-foresight plan counts zero everywhere.
    """
    pv, load, net = _beaten(plan, actual)
    pv_c, load_c = pv > EXCEED_TOL_KW, load > EXCEED_TOL_KW
    pv_a, load_a = pv.sum(axis=0), load.sum(axis=0)
    pv_ah, load_ah, net_h = pv_a > EXCEED_AGG_TOL_KW, load_a > EXCEED_AGG_TOL_KW, net > EXCEED_AGG_TOL_KW

    def worst(x: np.ndarray, mask: np.ndarray) -> float:
        return round(float(x[mask].max()), 4) if mask.any() else 0.0

    def first(mask: np.ndarray) -> list[int]:
        return np.flatnonzero(mask)[:FIRST_HOURS].tolist()

    out = {
        "pv_customer_hours_over": int(pv_c.sum()),
        "pv_aggregate_hours_over": int(pv_ah.sum()),
        "pv_worst_customer_kw": worst(pv, pv_c),
        "pv_worst_aggregate_kw": worst(pv_a, pv_ah),
        "load_customer_hours_under": int(load_c.sum()),
        "load_aggregate_hours_under": int(load_ah.sum()),
        "load_worst_customer_kw": worst(load, load_c),
        "load_worst_aggregate_kw": worst(load_a, load_ah),
        "net_aggregate_hours_not_dominated": int(net_h.sum()),
        "net_worst_aggregate_kw": worst(net, net_h),
        "first_hours": {"pv": first(pv_c.any(axis=0)), "load": first(load_c.any(axis=0)),
                        "net": first(net_h)},
    }
    if nonfirm is not None:
        nf = np.asarray(nonfirm, dtype=int)
        out["nonfirm_pv_customer_hours_over"] = int(pv_c[nf].sum())
        out["nonfirm_load_customer_hours_under"] = int(load_c[nf].sum())
    return out


def total_exceedance(audits: list[dict | None]) -> dict | None:
    """Several audits as one: counts added, worst kW the worst of them, the year and the first
    hours dropped (they index different years). None when there was nothing to audit."""
    rows = [a for a in audits if a]
    if not rows:
        return None
    out: dict = {}
    for key in rows[0]:
        if key in ("first_hours", "year"):
            continue
        vals = [r.get(key, 0) for r in rows]
        out[key] = max(vals) if key.endswith("_kw") else int(sum(vals))
    out["audits"] = int(sum(r.get("audits", 1) for r in rows))
    return out


# --------------------------------------------------------------------------------- issuing

def issue(model: NetModel, planning: dict[str, np.ndarray], actual: dict[str, np.ndarray],
          nonfirm: np.ndarray, scheme: str, policy: str = "maxmin", check_safety: bool = True) -> Issued:
    """Allocate against the planning state, publish, then check the result against reality.

    Outside the hours the planner called critical the published envelope is the customer's
    own inverter rating, which is what makes domination the whole game: under a dominating
    state those hours cannot be critical in reality either, and under a point forecast they
    very much can.

    With `check_safety` (the default) the check against reality is a full nonlinear solve of
    the year and the envelopes are verified in full against the planning state too. A capacity
    search, which only reads the curtailment, passes False: the envelopes are verified on their
    critical hours and reality is checked through the linear screen.

    Either way the issue audits its own premise: `exceedance` (`plan_exceedance`) counts where
    what happened beat the planning state, which is how a state issued ahead can fail. With
    `check_safety` it also counts the activations nothing needed (`unnecessary_hours`): hours
    somebody was cut although the real state, nobody cut, breaks no export-side limit.
    """
    r = doe.compute(model, planning, nonfirm, policy=policy, verify="full" if check_safety else "critical")
    H = actual["p_net"].shape[1]
    nonfirm = np.asarray(nonfirm, dtype=int)
    published = np.repeat(r.cap[:, None], H, axis=1)
    if len(r.hours):
        published[:, r.hours] = r.E

    p = np.asarray(actual["p_net"], dtype=float).copy()
    desired = np.clip(p[nonfirm], 0.0, None)
    granted = np.minimum(desired, published)
    if check_safety:
        # every export-side violation of the real year with the envelopes held, the firm ones too
        violation_hours = doe.verify_full_year(model, actual, r, every_hour=True)["export_violation_hours"]
    else:
        p[nonfirm] = np.where(desired > 0, granted, p[nonfirm])
        violation_hours = len(model.export_critical_hours(p, np.asarray(actual["q"], dtype=float)))

    curtailed = desired - granted
    export = float(desired.sum())
    limited = curtailed.sum(axis=0) > 1e-3
    unnecessary = None
    if check_safety:
        unnecessary = 0
        hrs = np.flatnonzero(limited)
        if len(hrs):
            pa = np.asarray(actual["p_net"], dtype=float)[:, hrs]
            v = model.violations(model.solve(pa, np.asarray(actual["q"], dtype=float)[:, hrs]), pa)
            unnecessary = int((~v.export_side & ~v.nonconverged).sum())
    return Issued(
        scheme=scheme,
        critical_hours=int(len(r.hours)),
        violation_hours=int(violation_hours),
        curtailed_kwh=float(curtailed.sum()),
        curtailed_share_pct=float(curtailed.sum() / export * 100.0) if export else 0.0,
        export_kwh=export,
        hours_limited=int(limited.sum()),
        exceedance=plan_exceedance(planning, actual, nonfirm),
        planning_full_year_violations=r.full_year_violations,
        unnecessary_hours=unnecessary,
        curtailed_by_customer=curtailed.sum(axis=1),
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

    `check_safety` goes to `issue`: off inside the capacity search, on for the point it
    publishes, whose violation hours then come from a full nonlinear solve of every year, and
    whose `planning_full_year_violations` sum the full-year check of the envelopes against the
    planning states they were allocated on (None without it).

    Every year issued against a planning state carries its audit (`plan_exceedance`) in
    `per_year`, and `exceedance` adds them up; the static rung issues nothing, so has none.

    `hours_limited` adds up the hours somebody was cut; with `check_safety`, `unnecessary_hours`
    the ones among them the real state did not need (`Issued.unnecessary_hours`), else None.

    Both bases of the budget come from the same run, as in `run.curtailment`: `z_pct` is the
    aggregate share, `z_customer_max_pct` the highest share of one installation over the years
    and `z_customer_year_max_pct` its highest in one year, with the energies in `per_customer`.
    """
    C = model.C
    nf = order[ms:k]
    pvm = _pv_mask(order, k, C)
    curt = pot = 0.0
    violations = 0
    planning = 0 if check_safety else None
    limited = 0
    unnecessary = 0 if check_safety and scheme != "static" else None
    per_year = []
    cust_curt, cust_pot, year_max = np.zeros(len(nf)), np.zeros(len(nf)), np.zeros(len(nf))
    for y in years:
        actual = Scenario(year=y, pv=pvm, hp=hp, ev=ev).arrays()
        pot_y = float(actual["pv"][nf].sum())
        if len(nf) == 0:
            per_year.append({"year": y, "curtailed_kwh": 0.0, "potential_kwh": pot_y,
                             "violation_hours": 0, "exceedance": None,
                             "hours_limited": 0, "unnecessary_hours": 0 if unnecessary is not None else None})
            continue
        if scheme == "static":
            p_s, q_s = _static_snapshot(order, ms, C)
            kappa, _ = doe.fixed_cap(model, actual, nf, p_s, q_s)
            cap = doe.customer_caps()[nf]
            cut = np.maximum(np.clip(actual["p_net"][nf], 0, None) - kappa * cap[:, None], 0)
            c_y, c_c = float(cut.sum()), cut.astype(float).sum(axis=1)
            v_y = 0                     # worst-case feasible by construction
            ex_y = None
            l_y, u_y = int((cut.sum(axis=0) > 1e-3).sum()), None
        else:
            plan = planning_state(scheme, y, pvm, hp, ev, actual, window_days)
            r = issue(model, plan, actual, nf, scheme, policy=policy, check_safety=check_safety)
            c_y, v_y, ex_y = r.curtailed_kwh, r.violation_hours, r.exceedance
            l_y, u_y = r.hours_limited, r.unnecessary_hours
            c_c = np.asarray(r.curtailed_by_customer, dtype=float)
            if planning is not None:
                planning += r.planning_full_year_violations
            if unnecessary is not None:
                unnecessary += u_y
        p_c = actual["pv"][nf].astype(float).sum(axis=1)
        year_max = np.maximum(year_max, share_pct(c_c, p_c))
        cust_curt += c_c
        cust_pot += p_c
        curt += c_y
        pot += pot_y
        violations += v_y
        limited += l_y
        per_year.append({"year": y, "curtailed_kwh": c_y, "potential_kwh": pot_y,
                         "violation_hours": v_y, "exceedance": ex_y,
                         "hours_limited": l_y, "unnecessary_hours": u_y})
    cust_share = share_pct(cust_curt, cust_pot)
    return {"scheme": scheme, "adopters": int(k), "non_firm": int(len(nf)),
            "curtailed_kwh": curt, "potential_kwh": pot,
            "z_pct": 100.0 * curt / pot if pot > 0 else 0.0,
            "z_customer_max_pct": float(cust_share.max()) if len(nf) else 0.0,
            "z_customer_year_max_pct": float(year_max.max()) if len(nf) else 0.0,
            "violation_hours": violations if (check_safety or scheme != "static") else 0,
            "planning_full_year_violations": planning,
            "hours_limited": limited, "unnecessary_hours": unnecessary,
            "exceedance": total_exceedance([p["exceedance"] for p in per_year]),
            "per_year": per_year,
            "per_customer": {"curtailed_kwh": cust_curt, "potential_kwh": cust_pot, "share_pct": cust_share,
                             "year_max_pct": year_max}}


def _static_snapshot(order: np.ndarray, ms: int, C: int):
    from .hosting import summer_snapshot
    return summer_snapshot(_pv_mask(order, ms, C))


BUDGETS = ("aggregate", "installation")
_SHARE = {"aggregate": "z_pct", "installation": "z_customer_max_pct"}


def adopters(model: NetModel, order: np.ndarray, ms: int, lo: int, years, hp: np.ndarray,
             ev: np.ndarray, scheme: str, z_star: float, *, window_days: int = WINDOW_DAYS,
             policy: str = "maxmin", hi: int | None = None, budget: str = "aggregate",
             memo: dict[tuple, dict] | None = None) -> tuple[int, dict]:
    """The most adopters this scheme carries inside the curtailment budget, and the cost there.

    `budget` is its basis: "aggregate" holds the share of all the non-firm customers' solar
    energy to `z_star`, "installation" each one's own share over the years. Every count the
    search runs goes into `memo` (count -> `multiyear`), which carries both shares, so a second
    search on the other basis, given the same memo, pays only for the counts the first did not
    measure. The memo is keyed by (scheme, window_days, policy, count), and for "intraday" by its
    gates and lag as well (for "nowcast" its quantiles too), so a memo passed to searches of another configuration never hands one
    of them the other's runs.

    `lo` is where the search starts: the published one passes the time-series capacity, the
    count that needs no curtailment with perfect foresight. A planning state issued ahead can
    curtail there, and on this grid often more than the budget, so `lo` is tested first rather
    than assumed. If it fails, the search goes down instead, towards `ms`, where nobody holds
    an envelope and the curtailment is zero by definition; so the count returned is always
    one the search measured inside the budget, or `ms`.

    `hi` saves the expensive end. A dominating state is critical in every hour the real one
    is, and allocates from less headroom when it is, so its envelope is never above the
    perfect-foresight envelope and its capacity is never above the published one. Passing
    that as the ceiling keeps the bisection out of a region it cannot reach, which on this
    grid is most of the search. Each count is evaluated once.
    """
    if budget not in BUDGETS:
        raise ValueError(f"budget must be one of {BUDGETS}, not {budget!r}")
    share = _SHARE[budget]
    runs = {} if memo is None else memo
    if scheme in ("intraday", "nowcast"):
        from . import intraday
        extra = intraday.memo_key(scheme)
    else:
        extra = ()

    def ok(k: int) -> bool:
        if k == int(ms):
            return True                 # nobody holds an envelope, so nobody is curtailed
        key = (scheme, int(window_days), policy, *extra, int(k))
        if key not in runs:
            runs[key] = multiyear(model, order, ms, k, years, hp, ev, scheme,
                                  window_days=window_days, policy=policy)
        return runs[key][share] <= z_star

    def largest(good: int, bad: int) -> int:
        """The largest count that passes, between one that does and one that does not."""
        while bad - good > 1:
            mid = (good + bad) // 2
            if ok(mid):
                good = mid
            else:
                bad = mid
        return good

    lo = max(int(lo), int(ms))
    hi = model.C if hi is None else min(int(hi), model.C)
    if not ok(lo):
        k = largest(int(ms), lo)
    elif hi <= lo:
        k = lo
    elif ok(hi):
        k = hi
    else:
        k = largest(lo, hi)
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


def ladder_payload(rows: list[Issued], year: int, window_days: int = WINDOW_DAYS) -> dict:
    """The ladder as data, written to results/forecast.json by `kapacitet forecast`."""
    def row(r: Issued) -> dict:
        return {k: v for k, v in asdict(r).items() if k != "curtailed_by_customer"}

    return {"year": year, "window_days": window_days,
            "rows": [{**row(r), "label": LADDER_LABEL.get(r.scheme, r.scheme), "safe": r.safe}
                     for r in rows]}


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


def write() -> tuple[list, dict, dict | None]:
    """The ladder of day-ahead schemes for the published showcase, written to results/forecast.json.
    Returns the rows, the showcase and the capacity under each scheme (foresight.json) if computed."""
    import json

    from ..atomic import write_json_atomic
    from ..paths import processed
    from .netmodel import get_model

    show = json.loads(processed("results", "showcase.json").read_text(encoding="utf-8"))
    cap_path = processed("results", "foresight.json")
    capacity = json.loads(cap_path.read_text(encoding="utf-8")) if cap_path.exists() else None
    rows = ladder(get_model(), show)
    from .stamp import stamped

    write_json_atomic(processed("results", "forecast.json"), stamped(ladder_payload(rows, show["year"])), indent=1)
    return rows, show, capacity
