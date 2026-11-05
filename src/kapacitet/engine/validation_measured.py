"""The model against what the DSO measured: a station's metered hours replayed through the power
flow, and its static results compared with ours (results/validation_measured.json).

`replay` puts each metered customer's own net injection (kwh_export − kwh_import of
processed/meters/hourly.parquet, at load_power_factor) into today's grid, the others keeping their
modelled load and the existing PV its modelled production, for the hours in which every metered
customer has a reading. Those hours are solved with NetModel.solve at the measured MV voltage of
each hour when the DSO gave one (inputs/mv/<sid>.csv), or else at both ends of the study's band.

`compare` sets the solution against the measurements: each customer's voltage (the mean of its
meter's phase voltages over the hour, over 230 V) and the transformer's loading (the station
meter's P and Q over its rating), as bias (model − measured), MAE, RMSE, p95 and max of the
absolute error. `compare_snapshot` does the same for a dpPower load-flow snapshot, element by
element, matched by mRID, and `calibrate_static` sets the DSO's own static capacity per customer
against the static capacity of this model (hosting.nodal_pv_hc and nodal_import_hc).

Input files read by the study's layer (study/station.validate):

    inputs/mv/<sid>.csv            start (ISO 8601 with its offset), vm_pu; optional trafo_p_kw,
                                   trafo_q_kvar (the station meter)
    inputs/dppower/snapshot.csv    start, mrid, kind (bus, line or transformer), vm_pu, loading_pct
    inputs/dppower/static.csv      customer_id, gen_kw and/or load_kw (the DSO's static results)
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from ..config import A
from ..timeindex import year_index
from .netmodel import NetModel
from .scenario import Scenario, customers

NOMINAL_V = 230.0
STATS = ("n", "bias", "mae", "rmse", "p95", "max")


def stats(err) -> dict:
    """bias (the mean), MAE, RMSE, and the p95 and max of |err|, over the finite values."""
    e = np.asarray(err, dtype=float).ravel()
    e = e[np.isfinite(e)]
    if not len(e):
        return {"n": 0, "bias": None, "mae": None, "rmse": None, "p95": None, "max": None}
    a = np.abs(e)
    return {"n": int(len(e)), "bias": float(e.mean()), "mae": float(a.mean()), "rmse": float(np.sqrt((e ** 2).mean())),
            "p95": float(np.percentile(a, 95)), "max": float(a.max())}


def compare(model: NetModel, p: np.ndarray, q: np.ndarray, vm, measured_v: np.ndarray | None = None,
            measured_trafo_pct: np.ndarray | None = None) -> dict:
    """The solution of (p, q) at the MV voltage `vm` (a scalar or one per hour) against the measured
    customer voltages (C, H, pu, NaN where none) and transformer loading (H,, %, NaN where none)."""
    res = model.solve(np.asarray(p, dtype=float), np.asarray(q, dtype=float), vm)
    v = np.abs(res.v[model.cust_bus_row])
    out = {"hours": int(np.shape(p)[1]),
           "voltage_pu": stats(v - measured_v) if measured_v is not None else stats([]),
           "transformer_pct": (stats(res.trafo_loading - measured_trafo_pct) if measured_trafo_pct is not None
                               else stats([])),
           "nonconverged_hours": int((~np.asarray(res.converged, dtype=bool)).sum()) if res.converged is not None else 0}
    return out


# ---------------------------------------------------------------- the inputs, on the year's hours

def cid_of() -> dict[str, int]:
    """The contract's customer_id -> the engine's customer row."""
    return {str(c): i for i, c in enumerate(customers()["osm_id"].astype(str))}


def replay(year: int, meters: pd.DataFrame, hp, ev) -> dict:
    """Today's grid of `year` with each metered customer's own net injection in its hours.
    {"p", "q" (C, H), "metered" (C,) bool, "complete" (H,) bool: every metered customer read}."""
    C = len(customers())
    arr = Scenario(year=year, pv=np.zeros(C, dtype=bool), hp=hp, ev=ev).arrays()
    p, q = arr["p_net"].astype(float), arr["q"].astype(float)
    H = p.shape[1]
    ids = cid_of()
    m = meters[(meters["year"] == year) & meters["customer_id"].astype(str).isin(ids)]
    metered = np.zeros(C, dtype=bool)
    seen = np.zeros((C, H), dtype=bool)
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    for cid, g in m.groupby(m["customer_id"].astype(str)):
        i = ids[cid]
        h = g["hour"].to_numpy(dtype=int)
        ok = (h >= 0) & (h < H)
        h = h[ok]
        imp = g["kwh_import"].to_numpy(dtype=float)[ok]
        exp = np.nan_to_num(g["kwh_export"].to_numpy(dtype=float)[ok]) if "kwh_export" in g else np.zeros(len(h))
        p[i, h] = exp - imp
        q[i, h] = -imp * tanphi
        metered[i] = True
        seen[i, h] = True
    complete = seen[metered].all(axis=0) if metered.any() else np.zeros(H, dtype=bool)
    return {"p": p, "q": q, "metered": metered, "complete": complete}


def _hours(start: pd.Series, year: int) -> np.ndarray:
    first = year_index(year)[0]
    return ((pd.DatetimeIndex(start) - first) // pd.Timedelta(hours=1)).to_numpy(dtype=int)


def measured_voltage(volt: pd.DataFrame | None, year: int) -> np.ndarray | None:
    """(C, H) pu: each customer's mean phase voltage over the hour (voltage_15min.parquet), NaN
    where its meter gave none."""
    if volt is None or not len(volt):
        return None
    ids = cid_of()
    C, H = len(ids), len(year_index(year))
    cols = [c for c in ("v_l1", "v_l2", "v_l3") if c in volt.columns]
    v = volt[volt["customer_id"].astype(str).isin(ids)].copy()
    start = pd.to_datetime(v["start"], utc=True).dt.floor("h")
    v["hour"] = _hours(start, year)
    v["v"] = v[cols].astype(float).mean(axis=1) / NOMINAL_V
    v = v[(v["hour"] >= 0) & (v["hour"] < H)]
    out = np.full((C, H), np.nan)
    if len(v):
        g = v.groupby([v["customer_id"].astype(str), "hour"])["v"].mean()
        rows = np.array([ids[c] for c in g.index.get_level_values(0)], dtype=int)
        out[rows, g.index.get_level_values(1).to_numpy(dtype=int)] = g.to_numpy()
    return out


_OFFSET = re.compile(r"(Z|[+-]\d{2}(:?\d{2})?)\s*$", re.I)


def require_offset(times: pd.Series, path) -> None:
    """Refuse a time without a UTC offset: on the night the clocks go back it names two instants."""
    bad = [str(x) for x in times.dropna() if str(x).strip() and not _OFFSET.search(str(x))]
    if bad:
        raise ValueError(f"{path}: {len(bad)} times have no UTC offset (first: {bad[0]!r}); "
                         "write them as 2025-03-30T03:00:00+02:00 or with Z")


def read_mv(path) -> pd.DataFrame:
    """inputs/mv/<sid>.csv as hourly means on the time index: start (UTC), vm_pu and, when given,
    trafo_p_kw and trafo_q_kvar. The separator is sniffed and a decimal comma read as a point."""
    df = pd.read_csv(path, sep=None, engine="python", dtype=str, encoding="utf-8-sig")
    df.columns = [c.strip().lower() for c in df.columns]
    if "start" not in df.columns or "vm_pu" not in df.columns:
        raise ValueError(f"{path}: needs the columns start and vm_pu")
    require_offset(df["start"], path)
    start = pd.to_datetime(df["start"], utc=True, errors="coerce", format="ISO8601")
    num = {c: pd.to_numeric(df[c].str.replace(",", ".", regex=False), errors="coerce")
           for c in ("vm_pu", "trafo_p_kw", "trafo_q_kvar") if c in df.columns}
    out = pd.DataFrame({"start": start.dt.floor("h"), **num}).dropna(subset=["start", "vm_pu"])
    return out.groupby("start", as_index=False).mean()


def mv_on(mv: pd.DataFrame | None, year: int) -> tuple[np.ndarray, np.ndarray | None]:
    """(H,) the measured MV of each hour of `year` (NaN where none) and the transformer's (P, Q)
    in kW, kvar ((2, H), or None without a station meter)."""
    H = len(year_index(year))
    vm = np.full(H, np.nan)
    if mv is None or not len(mv):
        return vm, None
    h = _hours(mv["start"], year)
    ok = (h >= 0) & (h < H)
    vm[h[ok]] = mv["vm_pu"].to_numpy(dtype=float)[ok]
    if "trafo_p_kw" not in mv.columns:
        return vm, None
    pq = np.full((2, H), np.nan)
    pq[0, h[ok]] = mv["trafo_p_kw"].to_numpy(dtype=float)[ok]
    pq[1, h[ok]] = (mv["trafo_q_kvar"].to_numpy(dtype=float)[ok] if "trafo_q_kvar" in mv.columns else 0.0)
    return vm, pq


def validate(model_at, meters: pd.DataFrame, years, hp, ev, band: tuple[float, float],
             volt: pd.DataFrame | None = None, mv: pd.DataFrame | None = None, trafo_kva: float | None = None) -> dict:
    """The metered hours of every year replayed and compared. `model_at(vm)` gives the model at an MV
    voltage (engine.netmodel.get_model). With a measured MV the hours that have one are solved at
    it ("measured"); without, every complete hour at the band's two ends ("band_low", "band_high")."""
    from .netmodel import get_model

    bases: dict[str, dict] = {}
    errs: dict[str, dict[str, list]] = {}
    hours_used = 0
    for y in years:
        r = replay(y, meters, hp, ev)
        if not r["metered"].any():
            continue
        vm_h, pq = mv_on(mv, y)
        mv_ok = np.isfinite(vm_h)
        mv_meas = mv_ok.any()
        v_meas = measured_voltage(volt, y)
        t_meas = (np.hypot(pq[0], pq[1]) / trafo_kva * 100.0) if pq is not None and trafo_kva else None
        runs = ({"measured": (r["complete"] & mv_ok, None)} if mv_meas else
                {"band_low": (r["complete"], band[0]), "band_high": (r["complete"], band[1])})
        for name, (hours, vm) in runs.items():
            h = np.flatnonzero(hours)
            if not len(h):
                continue
            m = get_model() if vm is None else model_at(vm)
            res = m.solve(r["p"][:, h], r["q"][:, h], vm_h[h] if vm is None else None)
            v = np.abs(res.v[m.cust_bus_row])
            e = errs.setdefault(name, {"v": [], "t": [], "nonconverged": 0, "hours": 0})
            if v_meas is not None:
                e["v"].append((v - v_meas[:, h])[r["metered"]])
            if t_meas is not None:
                e["t"].append(res.trafo_loading - t_meas[h])
            e["nonconverged"] += int((~np.asarray(res.converged, dtype=bool)).sum()) if res.converged is not None else 0
            e["hours"] += int(len(h))
        hours_used += int(r["complete"].sum())
    for name, e in errs.items():
        bases[name] = {"hours": e["hours"], "nonconverged_hours": e["nonconverged"],
                       "voltage_pu": stats(np.concatenate([x.ravel() for x in e["v"]]) if e["v"] else []),
                       "transformer_pct": stats(np.concatenate(e["t"]) if e["t"] else [])}
    return {"hours_complete": hours_used, "mv": "measured" if "measured" in bases else "band",
            "band_pu": [float(band[0]), float(band[1])], "bases": bases,
            "metered_customers": len(set(meters["customer_id"].astype(str)) & set(cid_of()))}


# ---------------------------------------------------------------- dpPower and the static results

def mrid_key(value) -> str:
    """An mRID as it is matched: no blanks, no leading '#' or '_', lower case (importer/cim.py's)."""
    from ..importer.cim import mrid_key as key

    return key(value)


def compare_snapshot(model: NetModel, snapshot: pd.DataFrame, buses: dict[str, int], lines: list[str],
                     trafo: str | None, p: np.ndarray, q: np.ndarray, vm) -> dict:
    """A dpPower load-flow snapshot (mrid, kind, vm_pu, loading_pct) against this model solving the
    same hour's injection (p, q of (C,)): matched by mRID to the contract's bus_id and line_id
    (`buses`: bus_id -> bus row, `lines`: line_id in row order) and the transformer's mRID."""
    res = model.solve(np.asarray(p, dtype=float)[:, None], np.asarray(q, dtype=float)[:, None], vm)
    bus_rows = {mrid_key(k): v for k, v in buses.items()}
    line_rows = {mrid_key(k): i for i, k in enumerate(lines)}
    t_key = mrid_key(trafo) if trafo else None
    dv, dl, worst, unmatched = [], [], [], []
    for r in snapshot.itertuples():
        key, kind = mrid_key(r.mrid), str(r.kind).strip().lower()
        if kind == "bus" and key in bus_rows:
            ours, theirs = float(np.abs(res.v[bus_rows[key], 0])), float(r.vm_pu)
            dv.append(ours - theirs)
        elif kind == "line" and key in line_rows:
            ours, theirs = float(res.line_loading[line_rows[key], 0]), float(r.loading_pct)
            dl.append(ours - theirs)
        elif kind == "transformer" and (key == t_key or t_key is None):
            ours, theirs = float(res.trafo_loading[0]), float(r.loading_pct)
            dl.append(ours - theirs)
        else:
            unmatched.append(str(r.mrid))
            continue
        worst.append({"mrid": str(r.mrid), "kind": kind, "model": ours, "dppower": theirs, "diff": ours - theirs})
    worst.sort(key=lambda w: -abs(w["diff"]) if np.isfinite(w["diff"]) else 0.0)
    return {"rows": int(len(snapshot)), "matched": int(len(snapshot) - len(unmatched)), "unmatched": unmatched[:20],
            "unmatched_count": len(unmatched), "voltage_pu": stats(dv), "loading_pct": stats(dl), "worst": worst[:20]}


def calibrate_static(dso: pd.DataFrame, gen_kw: np.ndarray | None, load_kw: np.ndarray | None) -> dict:
    """The DSO's static capacity per customer (customer_id, gen_kw and/or load_kw) against this
    model's (`gen_kw`, `load_kw`: (C,) kW): model − DSO, per column, and the largest differences."""
    ids = cid_of()
    d = dso.copy()
    d.columns = [c.strip().lower() for c in d.columns]
    d["customer_id"] = d["customer_id"].astype(str).str.strip()
    known = d[d["customer_id"].isin(ids)]
    out = {"rows": int(len(d)), "matched": int(len(known)), "unmatched": int(len(d) - len(known))}
    for col, ours in (("gen_kw", gen_kw), ("load_kw", load_kw)):
        if col not in d.columns or ours is None:
            continue
        theirs = pd.to_numeric(known[col].astype(str).str.replace(",", ".", regex=False), errors="coerce")
        rows = np.array([ids[c] for c in known["customer_id"]], dtype=int)
        diff = ours[rows] - theirs.to_numpy(dtype=float)
        order = np.argsort(-np.nan_to_num(np.abs(diff), nan=-1.0))[:10]
        out[col] = {**stats(diff),
                    "largest": [{"customer_id": str(known["customer_id"].iloc[i]), "model": float(ours[rows[i]]),
                                 "dso": float(theirs.iloc[i]), "diff": float(diff[i])} for i in order
                                if np.isfinite(diff[i])]}
    return out
