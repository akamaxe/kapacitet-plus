"""Live connection request: static verdict vs a verified dynamic envelope, in about a second.

Baselines (precomputed grid states for every weather year):
  static — the neighbourhood as approved today: the adopters up to the static hosting
           capacity, all firm (the grid is "full" for the conservative method);
  doe    — the DOE scenario of the showcase (firm + non-firm adopters with envelopes).

For an applicant (house, PV kWp, optional EV charger) the envelope is the *residual* headroom
left by the existing customers (first come, first served, so neighbours are not affected):

    E_j(t) = min( min_n [V_max − V_n(t)] / S_nj ,  min_{l ∋ j} [P̄_l − P_l(t)] ,  P̄_T − P_T(t) ),  0 ≤ E_j ≤ cap_j

computed in closed form for every hour of every weather year, then verified with the
nonlinear power flow on the hours where it binds (reference year). Import envelopes for a new
EV charger follow the same rule with voltage drop and forward flows.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from ..config import A, settings
from ..paths import processed
from ..profiles.ev import EvSessions
from ..timeindex import hour_of_day, local_standard, year_index
from . import economics, hosting
from .netmodel import NetModel
from .scenario import Scenario, customers, year_profiles

log = logging.getLogger(__name__)
BASELINES = ("static", "doe")


# ------------------------------------------------------------------------------ precomputation

def baseline_definition(name: str) -> dict:
    sc = json.loads(processed("results", "showcase.json").read_text(encoding="utf-8"))
    if name == "static":
        firm = sc["firm_cids"]
        return {"name": name, "pv_cids": firm, "firm_cids": firm, "nonfirm_cids": []}
    return {"name": name, "pv_cids": sc["pv_cids"], "firm_cids": sc["firm_cids"], "nonfirm_cids": sc["nonfirm_cids"]}


def precompute(model: NetModel, name: str, years, hp: np.ndarray, ev: np.ndarray) -> None:
    """Grid state after all existing customers, for every weather year (compact float16)."""
    from . import doe

    base = baseline_definition(name)
    C = model.C
    pvm = np.zeros(C, dtype=bool)
    pvm[base["pv_cids"]] = True
    nonfirm = np.array(base["nonfirm_cids"], dtype=int)
    for y in years:
        arr = Scenario(year=y, pv=pvm, hp=hp, ev=ev).arrays()
        p = arr["p_net"].astype(float).copy()
        if len(nonfirm):
            r = doe.compute(model, arr, nonfirm, policy="maxmin")
            H = p.shape[1]
            p[nonfirm] = np.minimum(p[nonfirm], 0.0) + np.minimum(np.clip(arr["p_net"][nonfirm], 0, None),
                                                                  r.envelope(H))
        q = arr["q"].astype(float)
        res = model.solve(p, q)
        vdev = (np.abs(res.v[model.check_rows]) - 1.0).astype(np.float16)
        np.savez(processed("request", f"{name}_{y}.npz"), vdev=vdev,
                 pl=(model.phi @ p).astype(np.float16), ql=(model.phi @ q).astype(np.float16),
                 pt=p.sum(axis=0).astype(np.float32), qt=q.sum(axis=0).astype(np.float32),
                 trafo_loading=res.trafo_loading.astype(np.float16))
    processed("request", f"{name}.json").write_text(json.dumps(base), encoding="utf-8")


# ------------------------------------------------------------------------------ live evaluation

@dataclass
class Request:
    cid: int
    pv_kwp: float = 0.0
    ev_kw: float = 0.0
    baseline: str = "static"


@lru_cache(maxsize=32)
def _state(name: str, year: int) -> dict[str, np.ndarray]:
    d = np.load(processed("request", f"{name}_{year}.npz"))
    return {k: d[k] for k in d.files}


@lru_cache(maxsize=2)
def _baseline(name: str) -> dict:
    return json.loads(processed("request", f"{name}.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=16)
def _sessions(year: int) -> EvSessions:
    d = np.load(processed("ev", f"ev_sessions_{year}.npz"))
    return EvSessions(d["customer"], d["start"], d["end"], d["energy_kwh"])


def _applicant_profiles(req: Request, year: int, hp: np.ndarray, ev: np.ndarray) -> dict:
    cust = customers()
    prof = year_profiles(year)
    j = req.cid
    ilr = A("pv_dc_ac_ratio")
    cap = min(req.pv_kwp / ilr, float(cust.loc[j, "fuse_kva"])) if req.pv_kwp > 0 else 0.0
    pv = np.minimum(np.minimum(prof["pv_per_kwp"][j], 1.0 / ilr) * req.pv_kwp, cap)
    load = (prof["base"][j] * A("household_load_factor") + (prof["hp"][j] if hp[j] else 0.0)
            + (prof["ev"][j] if ev[j] else 0.0))
    return {"pv": pv.astype(float), "load": np.asarray(load, dtype=float), "cap": cap}


def _export_envelope(model: NetModel, st: dict, j: int, cap: float, released_kw: np.ndarray) -> np.ndarray:
    """Residual export headroom for customer j (kW per hour), before verification."""
    s = model.dv_dp[:, j]                                        # pu per kW
    vhead = model.v_max - (1.0 + st["vdev"].astype(float))      # (Kv, H)
    with np.errstate(divide="ignore", invalid="ignore"):
        ev_v = np.where(s[:, None] > 1e-9, vhead / s[:, None], np.inf).min(axis=0)
    lines = np.flatnonzero(model.phi[:, j] > 0)
    pl, ql = st["pl"][lines].astype(float), st["ql"][lines].astype(float)
    pbar = np.sqrt(np.maximum(model.line_s_max_kw[lines, None] ** 2 - ql ** 2, 0.0))
    ev_l = (pbar - pl).min(axis=0) if len(lines) else np.full(vhead.shape[1], np.inf)
    pbar_t = np.sqrt(np.maximum(model.trafo_s_max_kw ** 2 - st["qt"].astype(float) ** 2, 0.0))
    ev_t = pbar_t - st["pt"].astype(float)
    # export space the house already uses in the baseline is released for the new system
    env = np.minimum(np.minimum(ev_v, ev_l), ev_t) + released_kw
    return np.clip(env, 0.0, cap)


def _import_envelope(model: NetModel, st: dict, j: int, charger: float) -> np.ndarray:
    s = model.dv_dp[:, j]
    vhead = (1.0 + st["vdev"].astype(float)) - model.v_min
    with np.errstate(divide="ignore", invalid="ignore"):
        ev_v = np.where(s[:, None] > 1e-9, vhead / s[:, None], np.inf).min(axis=0)
    lines = np.flatnonzero(model.phi[:, j] > 0)
    pl, ql = st["pl"][lines].astype(float), st["ql"][lines].astype(float)
    pbar = np.sqrt(np.maximum(model.line_s_max_kw[lines, None] ** 2 - ql ** 2, 0.0))
    ev_l = (pbar + pl).min(axis=0) if len(lines) else np.full(vhead.shape[1], np.inf)
    pbar_t = np.sqrt(np.maximum(model.trafo_s_max_kw ** 2 - st["qt"].astype(float) ** 2, 0.0))
    ev_t = pbar_t + st["pt"].astype(float)
    return np.clip(np.minimum(np.minimum(ev_v, ev_l), ev_t) * 0.98, 0.0, charger)


_DOE_CACHE: dict = {}


def _cached_doe(baseline: str, year: int, model: NetModel, hp, ev) -> np.ndarray:
    """Envelopes of the baseline's non-firm customers (reference year), cached per process."""
    key = (baseline, year)
    if key not in _DOE_CACHE:
        from . import doe
        base = _baseline(baseline)
        pvm = np.zeros(model.C, dtype=bool)
        pvm[base["pv_cids"]] = True
        arr = Scenario(year=year, pv=pvm, hp=hp, ev=ev).arrays()
        nf = np.array(base["nonfirm_cids"], dtype=int)
        r = doe.compute(model, arr, nf, policy="maxmin")
        _DOE_CACHE[key] = r.envelope(arr["p_net"].shape[1])
    return _DOE_CACHE[key]


def _static(model: NetModel, req: Request, base: dict, hp, ev) -> dict:
    cust = customers()
    j = req.cid
    static = {}
    firm_mask = np.zeros(model.C, dtype=bool)
    firm_mask[base["pv_cids"]] = True     # the static study treats every connected system as firm
    if req.pv_kwp > 0:
        cap = min(req.pv_kwp / A("pv_dc_ac_ratio"), float(cust.loc[j, "fuse_kva"]))
        pv_kw = hosting.cap_kw().copy()
        pv_kw[j] = cap
        m = firm_mask.copy()
        m[j] = True
        p_s, q_s = hosting.summer_snapshot(m, pv_kw)
        res = model.solve(p_s[:, None], q_s[:, None])
        v = model.violations(res, p_s[:, None])
        static["pv"] = {"approved": bool(~v.export_side[0]), "trafo_loading_pct": float(res.trafo_loading[0]),
                        "max_voltage_pu": float(v.vmax[0]), "max_line_loading_pct": float(v.max_line_loading[0]),
                        **_reason(v, res, 0, "export")}
    if req.ev_kw > 0:
        extra = np.zeros(model.C)
        extra[j] = req.ev_kw
        p_w, q_w = hosting.winter_snapshot(hp, ev, extra)
        res = model.solve(p_w[:, None], q_w[:, None])
        v = model.violations(res, p_w[:, None])
        static["ev"] = {"approved": bool(~v.import_side[0]), "trafo_loading_pct": float(res.trafo_loading[0]),
                        "min_voltage_pu": float(v.vmin[0]), "max_line_loading_pct": float(v.max_line_loading[0]),
                        **_reason(v, res, 0, "import")}
    return static


def evaluate(model: NetModel, req: Request, hp: np.ndarray, ev: np.ndarray, emit=None) -> dict:
    """Full evaluation; `emit(stage, payload)` is called as each stage finishes (for SSE)."""
    emit = emit or (lambda *_: None)
    cfg = settings()
    t_all = time.perf_counter()
    cust = customers()
    j = int(req.cid)
    base = _baseline(req.baseline)
    had_pv = j in set(base["pv_cids"])
    ref = cfg.reference_year
    years = [y for y in cfg.weather_years if processed("request", f"{req.baseline}_{y}.npz").exists()]
    out: dict = {"request": dict(req.__dict__), "baseline": req.baseline, "customer": {
        "cid": j, "address": str(cust.loc[j, "address"]), "fuse_a": int(cust.loc[j, "fuse_a"]),
        "fuse_kva": float(cust.loc[j, "fuse_kva"]), "roof_kwp": float(cust.loc[j, "kwp"]),
        "azimuth": float(cust.loc[j, "azimuth"]), "tilt": float(cust.loc[j, "tilt"]),
        "east_west": bool(cust.loc[j, "east_west"]), "dist_m": float(cust.loc[j, "dist_m"]),
        "feeder": int(cust.loc[j, "feeder"]), "had_pv_in_baseline": had_pv,
        "has_hp": bool(hp[j]), "has_ev": bool(ev[j])}}

    # ---- 1) static method -------------------------------------------------------------------
    t0 = time.perf_counter()
    out["static"] = _static(model, req, base, hp, ev)
    emit("static", {"static": out["static"], "ms": 1000 * (time.perf_counter() - t0)})

    # ---- 2) dynamic envelopes, every weather year --------------------------------------------
    t0 = time.perf_counter()
    per_year, detail = [], {}
    for y in years:
        st = _state(req.baseline, y)
        prof = _applicant_profiles(req, y, hp, ev)
        H = len(prof["pv"])
        entry: dict = {"year": y}
        d: dict = {"pv": prof["pv"], "load": prof["load"], "cap": prof["cap"]}
        if req.pv_kwp > 0:
            released = np.zeros(H)
            if had_pv:
                old_kwp = float(cust.loc[j, "kwp"])
                old_cap = min(old_kwp / A("pv_dc_ac_ratio"), float(cust.loc[j, "fuse_kva"]))
                old_pv = np.minimum(np.minimum(year_profiles(y)["pv_per_kwp"][j], 1.0 / A("pv_dc_ac_ratio")) * old_kwp,
                                    old_cap)
                released = np.clip(old_pv - prof["load"], 0, None)
            d["env"] = _export_envelope(model, st, j, prof["cap"], released)
            d["desired"] = np.clip(prof["pv"] - prof["load"], 0.0, None)
            d["curt"] = np.maximum(d["desired"] - d["env"], 0.0)
        if req.ev_kw > 0:
            env_i = _import_envelope(model, st, j, req.ev_kw)
            s = _sessions(y)
            sel = s.customer == j
            need = unmet = 0.0
            binding = 0
            for a, b, e in zip(s.start[sel], s.end[sel], s.energy_kwh[sel]):
                rem = float(e)
                need += rem
                for h in range(int(a), min(int(b), H)):
                    if rem <= 1e-9:
                        break
                    if env_i[h] < min(req.ev_kw, rem) - 1e-6:
                        binding += 1
                    rem -= min(req.ev_kw, env_i[h], rem)
                unmet += rem
            d["env_import"] = env_i
            entry.update(ev_requested_kwh=need, ev_unmet_kwh=unmet, ev_hours_limited=binding)
        detail[y] = d
        per_year.append(entry)
    emit("envelope", {"years": len(years), "ms": 1000 * (time.perf_counter() - t0)})

    # ---- 3) verification with the nonlinear power flow (reference year) ---------------------
    t0 = time.perf_counter()
    verify: dict = {}
    if req.pv_kwp > 0 and ref in detail:
        d = detail[ref]
        pvm = np.zeros(model.C, dtype=bool)
        pvm[base["pv_cids"]] = True
        arr = Scenario(year=ref, pv=pvm, hp=hp, ev=ev).arrays()
        p = arr["p_net"].astype(float).copy()
        if base["nonfirm_cids"]:
            nf = np.array(base["nonfirm_cids"], dtype=int)
            env_nf = _cached_doe(req.baseline, ref, model, hp, ev)
            p[nf] = np.minimum(p[nf], 0.0) + np.minimum(np.clip(arr["p_net"][nf], 0, None), env_nf)
        net_j = d["pv"] - d["load"]
        p[j] = np.where(net_j > 0, np.minimum(net_j, d["env"]), net_j)
        # hours where the applicant exports and its envelope is below the inverter capacity
        hrs = np.flatnonzero((d["desired"] > 0) & (d["env"] < d["cap"] - 1e-6))
        n_bad = n_tightened = n_baseline = 0
        if len(hrs):
            q = arr["q"][:, hrs]
            res = model.solve(p[:, hrs], q)
            bad = model.violations(res, p[:, hrs]).export_side
            if bad.any():
                # bisection on the applicant's envelope in the failing hours (μ ∈ [0, 1])
                hb = hrs[bad]
                n_tightened = len(hb)
                env0 = d["env"][hb].copy()
                lo, hi = np.zeros(len(hb)), np.ones(len(hb))
                cols = np.flatnonzero(bad)
                for _ in range(16):
                    mid = (lo + hi) / 2
                    pc = p[:, hrs[cols]].copy()
                    pc[j] = np.where(net_j[hb] > 0, np.minimum(net_j[hb], env0 * mid), net_j[hb])
                    okk = ~model.violations(model.solve(pc, q[:, cols]), pc).export_side
                    lo = np.where(okk, mid, lo)
                    hi = np.where(okk, hi, mid)
                d["env"][hb] = env0 * lo
                p[j, hb] = np.where(net_j[hb] > 0, np.minimum(net_j[hb], d["env"][hb]), net_j[hb])
                res = model.solve(p[:, hrs], q)
                bad = model.violations(res, p[:, hrs]).export_side
                # hours that still fail with zero export from the applicant are limits of the
                # baseline itself (existing customers exactly at their own envelopes)
                n_baseline = int(bad.sum())
            n_bad = int(bad.sum())
            d["curt"] = np.maximum(d["desired"] - d["env"], 0.0)
        verify = {"hours_checked": int(len(hrs)), "hours_tightened": n_tightened,
                  "violations_after": n_bad - n_baseline, "baseline_limited_hours": n_baseline, "year": ref,
                  "method": "Z-bus nonlinear power flow on every hour where the envelope is below capacity"}
    out["verification"] = verify
    emit("verify", {"verification": verify, "ms": 1000 * (time.perf_counter() - t0)})

    for e in per_year:
        d = detail[e["year"]]
        if "curt" in d:
            e.update(pv_kwh=float(d["pv"].sum()), export_kwh=float(d["desired"].sum()),
                     curtailed_kwh=float(d["curt"].sum()), hours_limited=int((d["curt"] > 1e-3).sum()))
    out["per_year"] = per_year

    # ---- 4) risk over weather years (weekly block bootstrap) ---------------------------------
    t0 = time.perf_counter()
    risk: dict = {}
    if req.pv_kwp > 0 and per_year:
        z_year = np.array([100 * e["curtailed_kwh"] / e["pv_kwh"] if e["pv_kwh"] else 0.0 for e in per_year])
        cw, pw = [], []
        for e in per_year:
            d = detail[e["year"]]
            week = (np.arange(len(d["pv"])) // 168).clip(max=51)
            cw.append(np.bincount(week, d["curt"], 52))
            pw.append(np.bincount(week, d["pv"], 52))
        cw, pw = np.array(cw), np.array(pw)
        rng = np.random.default_rng(7)
        pick = rng.integers(0, len(per_year), size=(4000, 52))
        zb = 100 * cw[pick, np.arange(52)].sum(axis=1) / np.maximum(pw[pick, np.arange(52)].sum(axis=1), 1e-9)
        tail = np.sort(zb)[-max(1, int(len(zb) * 0.05)):]
        hist, edges = np.histogram(zb, bins=30)
        risk = {"by_year": [{"year": e["year"], "z_pct": float(z)} for e, z in zip(per_year, z_year)],
                "mean_pct": float(zb.mean()), "p90_pct": float(np.percentile(zb, 90)),
                "cvar95_pct": float(tail.mean()), "worst_year_pct": float(z_year.max()),
                "best_year_pct": float(z_year.min()),
                "histogram": {"counts": hist.tolist(), "edges": np.round(edges, 4).tolist()},
                "method": f"Weekly block bootstrap over {len(per_year)} weather years (4 000 resampled years)"}
    out["risk"] = risk
    emit("risk", {"risk": risk, "ms": 1000 * (time.perf_counter() - t0)})

    # ---- 5) economics at the real SE3 spot price ----------------------------------------------
    t0 = time.perf_counter()
    econ: dict = {}
    if req.pv_kwp > 0:
        rows = []
        for y in economics.price_years():
            if y not in detail:
                continue
            d = detail[y]
            self_use = np.minimum(d["pv"], d["load"])
            v_c = economics.value_sek(d["curt"], y)
            v_e = economics.value_sek(d["desired"] - d["curt"], y)
            v_s = economics.value_sek(self_use, y)
            rows.append({"year": y, "curtailed_kwh": float(d["curt"].sum()),
                         "curtailed_sek_gross": v_c["gross_sek"], "curtailed_sek_net": v_c["net_sek"],
                         "export_kwh": float((d["desired"] - d["curt"]).sum()), "export_sek": v_e["net_sek"],
                         "self_use_kwh": float(self_use.sum()), "self_use_spot_sek": v_s["net_sek"],
                         "negative_price_kwh_curtailed": v_c["negative_price_kwh"]})
        if rows:
            econ = {"by_year": rows,
                    "curtailed_sek_mean": float(np.mean([r["curtailed_sek_gross"] for r in rows])),
                    "export_sek_mean": float(np.mean([r["export_sek"] for r in rows])),
                    "self_use_spot_sek_mean": float(np.mean([r["self_use_spot_sek"] for r in rows])),
                    "note": "Valued at the SE3 day-ahead spot price only (no grid fees, energy tax or compensation)."}
    out["economics"] = econ
    emit("economics", {"economics": econ, "ms": 1000 * (time.perf_counter() - t0)})

    # ---- 6) profiles for the charts (reference year) ----------------------------------------
    if ref in detail:
        d = detail[ref]
        idx = year_index(ref)
        hod = hour_of_day(idx)
        months = local_standard(idx).month.to_numpy()
        prof_out: dict = {"year": ref}
        if "env" in d:
            grid = np.zeros((24, 12))
            for mo in range(1, 13):
                for h in range(24):
                    grid[h, mo - 1] = d["env"][(months == mo) & (hod == h)].min()
            prof_out.update(envelope_kw=np.round(d["env"], 2).tolist(), desired_kw=np.round(d["desired"], 2).tolist(),
                            pv_kw=np.round(d["pv"], 2).tolist(), curtailed_kw=np.round(d["curt"], 2).tolist(),
                            envelope_min_month_hour=np.round(grid, 2).tolist(), cap_kw=float(d["cap"]))
        if "env_import" in d:
            prof_out["import_envelope_kw"] = np.round(d["env_import"], 2).tolist()
        out["profiles"] = prof_out
    out["summary"] = _summary(out, req)
    out["seconds"] = time.perf_counter() - t_all
    emit("done", {"summary": out["summary"], "seconds": out["seconds"]})
    return out


_REASONS = {
    "trafo_rev": "Transformer reverse loading {v:.0f} % in the worst-case summer hour",
    "over_v": "Overvoltage {v:.3f} pu in the worst-case summer hour",
    "line_rev": "Cable overload {v:.0f} % in the worst-case summer hour",
    "ok_summer": "Within limits in the worst-case summer hour",
    "trafo": "Transformer loading {v:.0f} % at the winter peak hour",
    "under_v": "Undervoltage {v:.3f} pu at the winter peak hour",
    "line": "Cable overload {v:.0f} % at the winter peak hour",
    "ok_winter": "Within limits at the winter peak hour",
}


def _reason(v, res, h: int, side: str) -> dict:
    """Why the static study approves or rejects: English text plus a code and value the UI can translate."""
    if side == "export":
        if v.trafo_rev[h]:
            code, val = "trafo_rev", float(res.trafo_loading[h])
        elif v.over_v[h]:
            code, val = "over_v", float(v.vmax[h])
        elif v.line_rev[h]:
            code, val = "line_rev", float(v.max_line_loading[h])
        else:
            code, val = "ok_summer", None
    elif v.trafo[h] and not v.trafo_rev[h]:
        code, val = "trafo", float(res.trafo_loading[h])
    elif v.under_v[h]:
        code, val = "under_v", float(v.vmin[h])
    elif v.line[h]:
        code, val = "line", float(v.max_line_loading[h])
    else:
        code, val = "ok_winter", None
    return {"reason": _REASONS[code].format(v=val if val is not None else 0.0),
            "reason_code": code, "reason_value": val}


def _summary(out: dict, req: Request) -> dict:
    s: dict = {}
    ref = settings().reference_year
    e_ref = next((e for e in out["per_year"] if e["year"] == ref), {})
    if req.pv_kwp > 0:
        s["pv_static_approved"] = out["static"]["pv"]["approved"]
        s["pv_doe_approved"] = True
        s["pv_curtailment_ref_pct"] = 100 * e_ref.get("curtailed_kwh", 0) / max(e_ref.get("pv_kwh", 1), 1e-9)
        s["pv_hours_limited_ref"] = e_ref.get("hours_limited", 0)
        s["pv_risk_mean_pct"] = out.get("risk", {}).get("mean_pct")
        s["pv_risk_cvar95_pct"] = out.get("risk", {}).get("cvar95_pct")
    if req.ev_kw > 0:
        s["ev_static_approved"] = out["static"]["ev"]["approved"]
        req_kwh = sum(e.get("ev_requested_kwh", 0) for e in out["per_year"])
        unmet = sum(e.get("ev_unmet_kwh", 0) for e in out["per_year"])
        s["ev_unmet_pct"] = 100 * unmet / max(req_kwh, 1e-9)
        s["ev_hours_limited_ref"] = e_ref.get("ev_hours_limited", 0)
    return s
