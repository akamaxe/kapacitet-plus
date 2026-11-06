"""Probabilistic intraday forecasts of solar and load: LightGBM quantile models (Module 3).

The worst-change plan of `engine/intraday.py` takes the largest rise of solar (and the largest
fall of load) the other weather years ever showed over a horizon. That bound is so wide it barely
moves the day-ahead plan. This module forecasts the same two quantities as a quantile instead:

- **solar**, the AC output per kWp of each roof, at a high quantile (`nowcast_pv_quantile`);
- **load**, each house's consumption with the deployment's heat pumps and chargers, at a low
  quantile (`nowcast_load_quantile`).

Each has one LightGBM model with `objective="quantile"` for every horizon from 1 to 12 h (the
horizon is a feature). A gate only knows what was measured before it, so the features are:

- the horizon, the target hour of the day and the target day of the year (sin and cos);
- the last measured value and its recent trend (the change over the hour and the three hours
  before it);
- the clear-sky output per kWp of the house's roof at the target hour (`pvlib`'s simplified
  Solis model through the roof's own orientation; it depends only on the sun's position);
- the last measured air temperature.

**Leave one year out.** The model that plans year `y` is trained on the other weather years
only, so the replay never reads ahead (the same leave-one-out as the day-ahead bound). The rows
are a seeded sample of (house, hour, horizon) of each training year, and LightGBM runs in its
deterministic mode with a fixed thread count: the same inputs give the same forecast.

**What it is not.** A quantile forecast is empirical: at 0.995 the year beats it in about one
customer-hour of two hundred, by design, and nothing caps what it beats it by. It is audited,
never a guarantee. `calibration` reports the realised exceedance rate next to the nominal one.

**Where it holds.** A solar forecast is made for daylight hours only (clear-sky output above
zero) and a load forecast for every hour, in both cases only for hours an intraday gate plans
(`intraday.horizons`) and within the trained horizons; everywhere else the forecast is NaN and
the day-ahead plan holds.

Trained models are kept as text files in `data/processed/nowcast/`, named by a digest of
everything they depend on (the inputs of every weather year, the quantile, the parameters), so
the capacity search's worker processes load what the parent trained instead of training again.
"""

from __future__ import annotations

import hashlib
import json
import os

import numpy as np

from ..cache import memo, on_clear
from ..config import A, settings
from ..paths import processed
from ..profiles.pv import ac_per_kwp, load_ac_per_kwp
from ..profiles.weather import load_weather
from ..timeindex import day_of_year, hour_of_day, year_index

KINDS = ("pv", "load")
HORIZONS = tuple(range(1, 13))                      # hours from the last measurement
FEATURES = ("horizon_h", "hour", "doy_sin", "doy_cos", "last", "trend_1h", "trend_3h",
            "clear_sky_per_kwp", "t_air_last")
TRAIN_ROWS_PER_YEAR = 60_000                         # sampled (house, hour, horizon) rows per year
SEED = 20261106
VERSION = 1                                          # bump when the features or the sampling change
CLEAR_SKY_T_AIR_C = 20.0                             # the clear-sky output is a geometry feature
DAYLIGHT_PER_KWP = 1e-4                              # clear-sky output above this: the sun is up
EXCEED_TOL = 1e-4                                    # kW (per kWp for solar): float32 rounding
PARAMS = {
    "objective": "quantile", "learning_rate": 0.05, "num_leaves": 31,
    "max_bin": 255, "feature_fraction": 1.0, "bagging_fraction": 1.0,
    "deterministic": True, "force_row_wise": True, "num_threads": 4, "seed": SEED, "verbose": -1,
}
ROUNDS = 300
TAIL_ROWS_PER_LEAF = 10                              # rows beyond the quantile each leaf can count


def quantile(kind: str) -> float:
    """The quantile each kind is forecast at, from the assumptions, validated."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
    q = float(A("nowcast_pv_quantile" if kind == "pv" else "nowcast_load_quantile"))
    if not 0.0 < q < 1.0:
        raise ValueError(f"the {kind} quantile must lie strictly between 0 and 1: {q}")
    if (kind == "pv" and q < 0.5) or (kind == "load" and q > 0.5):
        raise ValueError(f"a {kind} plan needs the {'upper' if kind == 'pv' else 'lower'} side: {q}")
    return q


def nominal_exceedance(kind: str) -> float:
    """The share of hours the year should beat the forecast in: 1 − q for solar, q for load."""
    q = quantile(kind)
    return 1.0 - q if kind == "pv" else q


def min_leaf(kind: str) -> int:
    """The fewest rows a leaf may hold: enough for `TAIL_ROWS_PER_LEAF` of them to lie beyond the
    quantile, so a leaf's estimate of a tail is never read off a handful of rows (2000 at 0.995).
    A rule from the quantile alone, not fitted to any result."""
    tail = nominal_exceedance(kind)
    return max(100, int(np.ceil(TAIL_ROWS_PER_LEAF / tail)))


def training_years(year: int) -> list[int]:
    """The weather years the model for `year` learns from: every one but `year`."""
    years = [y for y in settings().weather_years if y != year]
    if not years:
        raise ValueError(f"{year} is the only weather year, so there is nothing to train on")
    return years


# ------------------------------------------------------------------------------ inputs

@memo(maxsize=16)
def clear_sky_per_kwp(year: int) -> np.ndarray:
    """(C, H) clear-sky AC output per kWp of each house's roof, through the same PV chain as
    `profiles.pv` at a fixed air temperature: known for any hour in advance."""
    import pvlib

    from .scenario import customers

    w = load_weather(year)
    elevation = 90.0 - w["zenith"].to_numpy(dtype=float)
    cs = pvlib.clearsky.simplified_solis(elevation)
    sky = w[["zenith", "azimuth"]].astype(float).copy()
    for col in ("ghi", "dni", "dhi"):
        sky[col] = np.clip(np.nan_to_num(np.asarray(cs[col], dtype=float)), 0.0, None)
    sky.loc[elevation <= 0.0, ["ghi", "dni", "dhi"]] = 0.0
    sky["t_air"] = CLEAR_SKY_T_AIR_C
    cust = customers()
    out = np.empty((len(cust), len(w)), dtype=np.float32)
    cache: dict[tuple, np.ndarray] = {}
    for i, r in enumerate(cust.itertuples()):
        key = (round(float(r.azimuth)), float(r.tilt), bool(r.east_west))
        if key not in cache:
            cache[key] = ac_per_kwp(sky, *key)
        out[i] = cache[key]
    return out


def series(kind: str, year: int, hp: np.ndarray | None = None, ev: np.ndarray | None = None) -> np.ndarray:
    """(C, H) what is measured and forecast: solar per kWp, or each house's load in kW."""
    if kind == "pv":
        return load_ac_per_kwp(year).astype(np.float32)
    if hp is None or ev is None:
        raise ValueError("a load forecast needs the deployment's heat pumps and chargers")
    from . import forecast
    return forecast._total_load(year, hp, ev)


@memo(maxsize=16)
def _calendar(year: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(H,) hour of the day, sin and cos of the day of the year, and the measured air temperature."""
    idx = year_index(year)
    ang = 2.0 * np.pi * day_of_year(idx) / (366.0 if len(idx) > 8760 else 365.0)
    t_air = load_weather(year)["t_air"].to_numpy(dtype=np.float32)
    return hour_of_day(idx), np.sin(ang).astype(np.float32), np.cos(ang).astype(np.float32), t_air


def features(year: int, a: np.ndarray, cs: np.ndarray, c: np.ndarray, t: np.ndarray,
             h: np.ndarray) -> np.ndarray:
    """(N, len(FEATURES)) the features of forecasting `a[c, t]` from the gate whose last measured
    hour is `t − h`. Nothing at or after `t − h + 1` is read, except the clear-sky output and the
    calendar of the target hour, which are known in advance."""
    hod, s, co, t_air = _calendar(year)
    m = t - h
    last = a[c, m]
    return np.column_stack([
        h.astype(np.float32), hod[t].astype(np.float32), s[t], co[t], last,
        last - a[c, np.maximum(m - 1, 0)], last - a[c, np.maximum(m - 3, 0)],
        cs[c, t], t_air[m],
    ]).astype(np.float32)


def _sample(kind: str, year: int, hp, ev) -> tuple[np.ndarray, np.ndarray]:
    """The seeded training rows of one year: features and target. Solar rows are daylight only."""
    a = series(kind, year, hp, ev)
    cs = clear_sky_per_kwp(year)
    C, H = a.shape
    rng = np.random.default_rng([SEED, KINDS.index(kind), year])
    n = TRAIN_ROWS_PER_YEAR * (3 if kind == "pv" else 1)     # about half the solar rows are night
    h = rng.choice(np.asarray(HORIZONS), size=n)
    t = h + 3 + rng.integers(0, H - h - 3)
    c = rng.integers(0, C, size=n)
    if kind == "pv":
        keep = cs[c, t] > DAYLIGHT_PER_KWP
        c, t, h = c[keep][:TRAIN_ROWS_PER_YEAR], t[keep][:TRAIN_ROWS_PER_YEAR], h[keep][:TRAIN_ROWS_PER_YEAR]
    return features(year, a, cs, c, t, h), a[c, t].astype(np.float32)


# ------------------------------------------------------------------------------ models

def _digest_arrays(arrays) -> str:
    sha = hashlib.sha256()
    for a in arrays:
        a = np.ascontiguousarray(a)
        sha.update(str(a.shape).encode())
        sha.update(a.tobytes())
    return sha.hexdigest()


_DIGESTS: dict[tuple, str] = {}
on_clear(_DIGESTS.clear)


def _input_digest(kind: str, hp, ev) -> str:
    """A digest of every input a model of `kind` can read, in every weather year."""
    key = (kind, None if hp is None else hp.tobytes(), None if ev is None else ev.tobytes())
    if key not in _DIGESTS:
        from .scenario import customers
        roofs = customers()[["azimuth", "tilt", "east_west"]].to_numpy(dtype=float)
        parts = [roofs]
        for y in settings().weather_years:
            w = load_weather(y)
            parts += [series(kind, y, hp, ev), w[["zenith", "azimuth", "t_air"]].to_numpy(dtype=np.float32)]
        _DIGESTS[key] = _digest_arrays(parts)
    return _DIGESTS[key]


def model_key(kind: str, year: int, hp=None, ev=None) -> str:
    """The name the model for `year` is kept under: a digest of what it depends on."""
    spec = {"version": VERSION, "kind": kind, "year": year, "train": training_years(year),
            "quantile": quantile(kind), "params": PARAMS, "min_leaf": min_leaf(kind), "rounds": ROUNDS, "rows": TRAIN_ROWS_PER_YEAR,
            "features": FEATURES, "horizons": HORIZONS, "inputs": _input_digest(kind, hp, ev)}
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]


_MODELS: dict[tuple, object] = {}
on_clear(_MODELS.clear)


def model(kind: str, year: int, hp=None, ev=None):
    """The LightGBM booster that forecasts `kind` for `year`, trained on the other years.

    Kept on disk by `model_key`, so a second process (or a second run on the same inputs) loads
    it rather than training again.
    """
    import lightgbm as lgb

    key = model_key(kind, year, hp, ev)
    if (kind, key) in _MODELS:
        return _MODELS[(kind, key)]
    path = processed("nowcast", f"{kind}_{year}_{key}.txt")
    if path.exists():
        booster = lgb.Booster(model_str=path.read_text(encoding="utf-8"))
    else:
        rows = [_sample(kind, y, hp, ev) for y in training_years(year)]
        X = np.concatenate([r[0] for r in rows])
        y_ = np.concatenate([r[1] for r in rows])
        params = {**PARAMS, "alpha": quantile(kind), "min_data_in_leaf": min_leaf(kind)}
        booster = lgb.train(params, lgb.Dataset(X, y_, feature_name=list(FEATURES), free_raw_data=True),
                            num_boost_round=ROUNDS)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(booster.model_to_string(), encoding="utf-8")
        os.replace(tmp, path)
    _MODELS[(kind, key)] = booster
    return booster


_FORECASTS: dict[tuple, np.ndarray] = {}
on_clear(_FORECASTS.clear)


def quantile_forecast(kind: str, year: int, gates: tuple[int, ...], lag: int,
                      hp: np.ndarray | None = None, ev: np.ndarray | None = None) -> np.ndarray:
    """(C, H) the quantile forecast each hour's governing gate makes, solar per kWp or load in kW,
    never below zero; NaN where no gate plans the hour, beyond the trained horizons, or (solar)
    after dark."""
    from .intraday import _per_hour

    key = (kind, year, gates, lag, None if hp is None else hp.tobytes(), None if ev is None else ev.tobytes(),
           quantile(kind))
    if key in _FORECASTS:
        return _FORECASTS[key]
    a = series(kind, year, hp, ev)
    cs = clear_sky_per_kwp(year)
    C, H = a.shape
    per = _per_hour(year, gates, lag)
    t_all = np.arange(H)
    hours = np.flatnonzero((per >= HORIZONS[0]) & (per <= HORIZONS[-1]) & (t_all - per >= 0))
    out = np.full((C, H), np.nan, dtype=np.float32)
    if len(hours):
        c = np.repeat(np.arange(C), len(hours))
        t = np.tile(hours, C)
        if kind == "pv":
            day = cs[c, t] > DAYLIGHT_PER_KWP
            c, t = c[day], t[day]
        if len(t):
            pred = model(kind, year, hp, ev).predict(features(year, a, cs, c, t, per[t]))
            out[c, t] = np.clip(pred, 0.0, None)
    if len(_FORECASTS) > 24:
        _FORECASTS.clear()
    _FORECASTS[key] = out
    return out


def prepare(years, hp: np.ndarray, ev: np.ndarray) -> None:
    """Train (or load) every year's two models in this process, so worker processes find them on disk."""
    for y in years:
        model("pv", y)
        model("load", y, hp, ev)


# ------------------------------------------------------------------------------ the audit

def calibration(years, gates: tuple[int, ...], lag: int, hp: np.ndarray, ev: np.ndarray) -> dict:
    """How often each year beat its forecast, against the nominal rate.

    Per kind, over every customer-hour a forecast was made for (solar: daylight only): the
    nominal exceedance (1 − q for solar, q for load), the realised rate pooled over the years and
    year by year, and the worst miss. A solar exceedance is an hour the roof produced more per
    kWp than forecast, a load one an hour the house used less.
    """
    out: dict = {}
    for kind in KINDS:
        per_year, n_all, over_all, worst = [], 0, 0, 0.0
        for y in years:
            f = quantile_forecast(kind, y, gates, lag, None if kind == "pv" else hp, None if kind == "pv" else ev)
            a = series(kind, y, hp, ev)
            ok = np.isfinite(f)
            miss = (a - f) if kind == "pv" else (f - a)
            over = ok & (miss > EXCEED_TOL)
            n, k = int(ok.sum()), int(over.sum())
            w = float(miss[over].max()) if k else 0.0
            per_year.append({"year": int(y), "customer_hours": n, "exceeded": k,
                             "realised_exceedance": k / n if n else None, "worst_miss": w})
            n_all, over_all, worst = n_all + n, over_all + k, max(worst, w)
        out[kind] = {
            "quantile": quantile(kind), "nominal_exceedance": nominal_exceedance(kind),
            "customer_hours": n_all, "exceeded": over_all,
            "realised_exceedance": over_all / n_all if n_all else None,
            "worst_miss": worst, "worst_miss_unit": "kW per kWp" if kind == "pv" else "kW",
            "per_year": per_year,
        }
    return out
