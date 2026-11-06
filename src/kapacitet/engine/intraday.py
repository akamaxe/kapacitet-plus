"""Envelopes re-issued during the day with the measurements, and the after-the-fact replica.

The day-ahead basis (`forecast.climatological`) is weather-blind: the record of the same
fortnight in the other weather years, issued the day before. On this grid that bound is loose
enough that the issuable envelope carries no more than the hourly study without envelopes does.
This module measures what the morning's measurements buy on top of it.

**Gates.** At each gate `g` of `intraday_gates_h` (local standard time, UTC+1, as `timeindex`)
the rest of the day is planned again. The last hour measured at a gate is
`g − 1 − intraday_measurement_lag_h`, so the horizon to hour `t` is `t − (g − 1 − lag)`. A gate
sets the envelopes from its own hour to the end of the day, the envelope that holds in an hour
is the one of the latest gate at or before it, and before the first gate of the day the
day-ahead envelope holds.

**The planning state at a gate**, per customer and hour, keeps the day-ahead one as its limit:

- solar: the lower of the day-ahead bound and the last measured output per kWp plus the largest
  rise per kWp over the same horizon, hour of the day and fortnight the other weather years
  showed, both widened by `dayahead_pv_margin_pct`, then capped as `forecast.climatological`
  caps it;
- load: the higher of the day-ahead floor and the last measured load less the largest fall over
  the same horizon in the other years, both lowered by `dayahead_load_margin_pct`.

The changes are additive, in kW per kWp and kW, not ratios: a ratio of two dawn hours with
almost no sun is unstable, a difference is not. The tables of changes leave the replayed year
out, so the replay never reads ahead of what was measured (the same leave-one-out as the
day-ahead bound). The state is meant to dominate what happens, and like the day-ahead one that
is an empirical bet: every issue audits where it lost (`forecast.plan_exceedance`).

**One state, many gates.** `doe.compute` allocates and verifies every hour on its own, so the
envelopes of several gates, each taken where it holds, are the envelopes of a single state that
holds each hour's plan from its governing gate. `planning_state` builds that state, and
`forecast.issue` allocates, verifies and replays it as it does the day-ahead one.

**Two variants.** `planning_state` is the worst-change plan above. `forecast_planning_state`
replaces the largest change with LightGBM quantile forecasts (`engine/nowcast.py`): solar at a
high quantile, load at a low one, with the same day-ahead limits and margins. It is beaten by
design in some hours, so the stage (`run_extra.intraday`) publishes both and leads with the
forecast variant only when its replay broke no limit (`VARIANTS` maps each to its scheme).

**What the measurements see.** The measured solar is the available output per kWp of the
replayed year, the uncurtailed value: a house held to its envelope still reports what it could
have produced (an inverter's available power, or the firm neighbours on the same roof
orientation). A replica that only saw curtailed output would plan lower, so would curtail more.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import maximum_filter1d, minimum_filter1d

from ..cache import memo, on_clear
from ..config import A, settings
from ..profiles.pv import load_ac_per_kwp
from ..timeindex import hour_of_day, year_index
from . import forecast

# What the replica has to show (the roadmap's value proof). Never tuned to the results.
REPLICA_THRESHOLDS = {
    "violation_hours": 0,                 # hours the reconstructed real state breaks a limit
    "agreements_within_pct": 100.0,       # agreements inside the budget, per installation
    "agreements_within_pct_justified": 90.0,   # the floor the roadmap accepts with a justification
    "gain_over_hourly_pct": 15.0,         # capacity over the hourly study without envelopes
}


def gates_and_lag() -> tuple[tuple[int, ...], int]:
    """The gates (hours of the day, sorted) and the measurement lag, from the assumptions."""
    gates = tuple(sorted({int(g) for g in A("intraday_gates_h")}))
    lag = int(A("intraday_measurement_lag_h"))
    if not gates or gates[0] < 0 or gates[-1] > 23:
        raise ValueError(f"intraday gates must be hours of the day, 0 to 23: {gates}")
    if lag < 0:
        raise ValueError(f"the measurement lag cannot be negative: {lag}")
    return gates, lag


def horizons(gates: tuple[int, ...], lag: int) -> np.ndarray:
    """(24,) hours from the last measured hour to each hour of the day, under its governing gate;
    0 for the hours before the first gate, where the day-ahead envelope holds."""
    out = np.zeros(24, dtype=int)
    for t in range(24):
        g = [x for x in gates if x <= t]
        if g:
            out[t] = t - (g[-1] - 1 - lag)
    return out


def _per_hour(year: int, gates: tuple[int, ...], lag: int) -> np.ndarray:
    """(H,) the horizon of every hour of `year` (0: no intraday plan)."""
    return horizons(gates, lag)[hour_of_day(year_index(year))]


def _year_changes(a: np.ndarray, h: np.ndarray, fill: float) -> np.ndarray:
    """(C, H) the change over each hour's horizon within one year, `fill` where it has none."""
    out = np.full(a.shape, fill, dtype=np.float32)
    for k in np.unique(h[h > 0]):
        cols = np.flatnonzero(h == k)
        cols = cols[cols >= k]
        out[:, cols] = a[:, cols] - a[:, cols - k]
    return out


def _window_change(year: int, per_year, extreme: str, window_days: int,
                   gates: tuple[int, ...], lag: int) -> np.ndarray:
    """The largest rise (or fall) the other weather years showed over each hour's horizon, in the
    same hour of the day within ±`window_days`; NaN where the history has none (the day-ahead
    plan then holds). The replayed year is never in it."""
    years = [y for y in settings().weather_years if y != year]
    if not years:
        raise ValueError(f"{year} is the only weather year, so there is no history of changes")
    fill = -np.inf if extreme == "max" else np.inf
    stack = np.stack([forecast._day_shaped(y, _year_changes(per_year(y), _per_hour(y, gates, lag), fill))
                      for y in years])
    if extreme == "max":
        hist = maximum_filter1d(stack.max(axis=0), size=2 * window_days + 1, axis=1, mode="nearest")
    else:
        hist = minimum_filter1d(stack.min(axis=0), size=2 * window_days + 1, axis=1, mode="nearest")
    out = forecast._restore_leap(year, hist).astype(np.float32)
    out[~np.isfinite(out)] = np.nan
    out[:, _per_hour(year, gates, lag) == 0] = np.nan
    return out


@memo(maxsize=16)            # one per weather year and set of gates
def pv_rise(year: int, gates: tuple[int, ...], lag: int,
            window_days: int = forecast.WINDOW_DAYS) -> np.ndarray:
    """(C, H) the largest rise of AC output per kWp over each hour's horizon in the other years."""
    return _window_change(year, load_ac_per_kwp, "max", window_days, gates, lag)


_LOAD_FALL: dict[tuple, np.ndarray] = {}
on_clear(_LOAD_FALL.clear)


def load_fall(year: int, hp: np.ndarray, ev: np.ndarray, gates: tuple[int, ...], lag: int,
              window_days: int = forecast.WINDOW_DAYS) -> np.ndarray:
    """(C, H) the largest fall (negative) of each house's load over each hour's horizon in the other
    years, with the deployment's heat pumps and chargers in it (`forecast.load_lower_bound`)."""
    key = (year, window_days, gates, lag, hp.tobytes(), ev.tobytes())
    if key not in _LOAD_FALL:
        if len(_LOAD_FALL) > 24:
            _LOAD_FALL.clear()
        _LOAD_FALL[key] = _window_change(year, lambda y: forecast._total_load(y, hp, ev), "min",
                                         window_days, gates, lag)
    return _LOAD_FALL[key]


def measured(a: np.ndarray, h: np.ndarray) -> np.ndarray:
    """(C, H) the last measured value seen by each hour's gate, `a[t − h(t)]`; NaN where the hour
    has no intraday plan or the measurement would fall before the year."""
    out = np.full(a.shape, np.nan, dtype=np.float32)
    t = np.arange(a.shape[1])
    ok = (h > 0) & (t - h >= 0)
    out[:, ok] = a[:, (t - h)[ok]]
    return out


def planning_state(year: int, pv_mask: np.ndarray, hp: np.ndarray, ev: np.ndarray,
                   window_days: int = forecast.WINDOW_DAYS, *, pv_margin: float | None = None,
                   load_margin: float | None = None) -> dict:
    """The state each hour's envelope is allocated on: the plan of its governing gate, or the
    day-ahead one before the first gate (see the module's docstring)."""
    gates, lag = gates_and_lag()
    pm, lm = forecast.day_ahead_margins(pv_margin, load_margin)
    h = _per_hour(year, gates, lag)
    da_pv = forecast.pv_upper_bound(year, window_days)
    pv_in = np.clip(measured(load_ac_per_kwp(year), h) + pv_rise(year, gates, lag, window_days), 0.0, None)
    per = np.where(np.isnan(pv_in), da_pv, np.fmin(da_pv, pv_in)) * (1.0 + pm)
    pv = forecast._inverter_capped(pv_mask, per)
    da_load = forecast.load_lower_bound(year, hp, ev, window_days)
    load_in = np.clip(measured(forecast._total_load(year, hp, ev), h)
                      + load_fall(year, hp, ev, gates, lag, window_days), 0.0, None)
    load = np.where(np.isnan(load_in), da_load, np.fmax(da_load, load_in)) * (1.0 - lm)
    return forecast._arrays(pv, load)


def forecast_planning_state(year: int, pv_mask: np.ndarray, hp: np.ndarray, ev: np.ndarray,
                            window_days: int = forecast.WINDOW_DAYS, *, pv_margin: float | None = None,
                            load_margin: float | None = None) -> dict:
    """The forecast variant (`engine/nowcast.py`): each gate plans the solar at the lower of the
    day-ahead bound and the high-quantile forecast, the load at the higher of the day-ahead floor
    and the low-quantile forecast, with the day-ahead margins on both, as `planning_state` does
    with the largest change instead. Where no forecast is made the day-ahead plan holds.

    Unlike the worst-change plan this one is beaten by design in about 1 - q of the hours it
    forecasts, so the replay against the real state decides whether its envelopes may be the
    headline (`run_extra.intraday`)."""
    from . import nowcast

    gates, lag = gates_and_lag()
    pm, lm = forecast.day_ahead_margins(pv_margin, load_margin)
    da_pv = forecast.pv_upper_bound(year, window_days)
    pv_q = nowcast.quantile_forecast("pv", year, gates, lag)
    per = np.where(np.isnan(pv_q), da_pv, np.fmin(da_pv, pv_q)) * (1.0 + pm)
    pv = forecast._inverter_capped(pv_mask, per)
    da_load = forecast.load_lower_bound(year, hp, ev, window_days)
    load_q = nowcast.quantile_forecast("load", year, gates, lag, hp, ev)
    load = np.where(np.isnan(load_q), da_load, np.fmax(da_load, load_q)) * (1.0 - lm)
    return forecast._arrays(pv, load)


VARIANTS = {"worst_change": "intraday", "forecast": "nowcast"}     # variant -> forecast scheme


def memo_key(scheme: str = "intraday") -> tuple:
    """What a capacity search's memo has to tell apart besides the count: the gates and the lag,
    and for the forecast variant the two quantiles as well."""
    gates, lag = gates_and_lag()
    if scheme == "nowcast":
        from . import nowcast
        return (gates, lag, nowcast.quantile("pv"), nowcast.quantile("load"))
    return (gates, lag)


# ------------------------------------------------------------------------- the scorecard

def scorecard(kw: float, hourly_kw: float, violation_hours: int, within_pct: list[float]) -> dict:
    """The replica against the roadmap's thresholds (REPLICA_THRESHOLDS), for one basis of the budget.

    `within_pct` is each adoption order's share of agreements inside the budget per installation;
    the worst order is the one judged. Each line says pass or fail; the agreements also say
    whether they pass on the justified floor alone.
    """
    th = REPLICA_THRESHOLDS
    gain = (kw / hourly_kw - 1.0) * 100.0 if hourly_kw else None
    worst = float(min(within_pct)) if within_pct else 100.0
    agreements = ("pass" if worst >= th["agreements_within_pct"] - 1e-9 else
                  "pass_if_justified" if worst >= th["agreements_within_pct_justified"] - 1e-9 else "fail")
    out = {
        "violation_hours": {"value": int(violation_hours), "threshold": th["violation_hours"],
                            "result": "pass" if violation_hours <= th["violation_hours"] else "fail"},
        "agreements_within_pct": {"value": worst, "median": float(np.median(within_pct)) if within_pct else 100.0,
                                  "threshold": th["agreements_within_pct"],
                                  "justified_threshold": th["agreements_within_pct_justified"],
                                  "result": agreements},
        "gain_over_hourly_pct": {"value": gain, "kw": kw, "hourly_kw": hourly_kw,
                                 "threshold": th["gain_over_hourly_pct"],
                                 "result": "pass" if gain is not None and gain >= th["gain_over_hourly_pct"] - 1e-9
                                 else "fail"},
    }
    results = [v["result"] for v in out.values()]
    out["result"] = ("fail" if "fail" in results else
                     "pass_if_justified" if "pass_if_justified" in results else "pass")
    return out
