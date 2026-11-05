"""Live connection request: static verdict vs a verified dynamic envelope, in about a second.

Baselines (precomputed grid states for every weather year):
  static — the neighbourhood as approved today: the adopters up to the static hosting
           capacity, all firm (the grid is "full" for the conservative method);
  doe    — the DOE scenario of the showcase (firm + non-firm adopters with envelopes).
A queue of approved requests (engine/queue.py) can sit on top of either; it is passed in as a
`Context` whose grid states include the approved houses.

For an applicant (house; PV kWp, an EV charger, a heat pump, a battery) the envelope is the
*residual* headroom left by the existing customers (first come, first served, so neighbours are
not affected):

    E_j(t) = min( min_n [V_max − V_n(t)] / S_nj ,  min_{l ∋ j} [P̄_l − P_l(t)] ,  P̄_T − P_T(t) ),  0 ≤ E_j ≤ cap_j

computed in closed form for every hour of every weather year, then verified with the
nonlinear power flow on the hours where it binds (the reference year; every year when a request
is approved into a queue). Import envelopes for a new EV charger or heat pump follow the same rule
with voltage drop and forward flows, and are verified the same way. A battery stores only the
energy the export envelope would curtail and gives it back to the house's own load, so the house
never exports more than its verified envelope; its discharge hours are checked too.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from ..atomic import savez_atomic, write_text_atomic
from ..cache import memo, on_clear
from ..config import A, settings
from ..paths import DATA, processed
from ..profiles.ev import EvSessions
from ..timeindex import hour_of_day, local_standard, year_index
from . import economics, hosting
from .netmodel import NetModel
from .scenario import Scenario, customers, year_profiles

log = logging.getLogger(__name__)
# The grid states a request can be evaluated against: the two published with the demo's results
# (`PUBLISHED`, from showcase.json) and "existing", what a station has today by its equipment
# records with nobody added, which a study precomputes for each station.
BASELINES = {
    "static": "the firm customers of the showcase",
    "doe": "the showcase's customers, the non-firm ones held to their envelopes",
    "existing": "today's grid: the existing PV of the equipment records, firm, and nobody added",
}
PUBLISHED = ("static", "doe")


# ------------------------------------------------------------------------------ grid states

def baseline_definition(name: str) -> dict:
    if name == "existing":
        from .scenario import existing

        pv = [int(c) for c in np.flatnonzero(existing()["has_pv"])]
        return {"name": name, "pv_cids": pv, "firm_cids": pv, "nonfirm_cids": []}
    if name not in BASELINES:
        raise ValueError(f"no baseline {name!r}: one of {sorted(BASELINES)}")
    sc = json.loads(processed("results", "showcase.json").read_text(encoding="utf-8"))
    if name == "static":
        firm = sc["firm_cids"]
        return {"name": name, "pv_cids": firm, "firm_cids": firm, "nonfirm_cids": []}
    return {"name": name, "pv_cids": sc["pv_cids"], "firm_cids": sc["firm_cids"], "nonfirm_cids": sc["nonfirm_cids"]}


def base_injection(model: NetModel, base: dict, year: int, hp: np.ndarray, ev: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Net injection (C, H) of a baseline's customers, the non-firm ones held to their envelopes."""
    from . import doe

    pvm = np.zeros(model.C, dtype=bool)
    pvm[base["pv_cids"]] = True
    arr = Scenario(year=year, pv=pvm, hp=hp, ev=ev).arrays()
    p = arr["p_net"].astype(float).copy()
    nonfirm = np.array(base["nonfirm_cids"], dtype=int)
    if len(nonfirm):
        # verified in every hour (doe.compute's default), since every hour of it is a baseline
        r = doe.compute(model, arr, nonfirm, policy="maxmin")
        doe.require_verified(r.full_year_violations, f"request baseline {base['name']}, {year}")
        p = doe.held_injection(arr, r)
    return p, arr["q"].astype(float)


def build_state(model: NetModel, p: np.ndarray, q: np.ndarray) -> dict[str, np.ndarray]:
    """What a request needs to know about the grid in every hour (compact float16)."""
    res = model.solve(p, q)
    return {"vdev": (np.abs(res.v[model.check_rows]) - 1.0).astype(np.float16),
            "pl": (model.phi @ p).astype(np.float16), "ql": (model.phi @ q).astype(np.float16),
            "pt": p.sum(axis=0).astype(np.float32), "qt": q.sum(axis=0).astype(np.float32),
            "trafo_loading": res.trafo_loading.astype(np.float16)}


def precompute(model: NetModel, name: str, years, hp: np.ndarray, ev: np.ndarray) -> None:
    """Grid state after all existing customers, for every weather year."""
    base = baseline_definition(name)
    for y in years:
        p, q = base_injection(model, base, y, hp, ev)
        savez_atomic(processed("request", f"{name}_{y}.npz"), **build_state(model, p, q))
    write_text_atomic(processed("request", f"{name}.json"), json.dumps(base))


@memo(maxsize=32)
def _state(name: str, year: int) -> dict[str, np.ndarray]:
    d = np.load(processed("request", f"{name}_{year}.npz"))
    return {k: d[k] for k in d.files}


@memo(maxsize=2)
def _baseline(name: str) -> dict:
    return json.loads(processed("request", f"{name}.json").read_text(encoding="utf-8"))


_PQ_CACHE: dict[tuple[str, int], tuple[np.ndarray, np.ndarray]] = {}
on_clear(_PQ_CACHE.clear)


def baseline_pq(model: NetModel, name: str, year: int, hp: np.ndarray, ev: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The published baseline's injection for one year, kept for the last few years asked for.

    Recomputing it means the non-firm customers' envelopes for that year, a few seconds each, so a
    copy is also kept in data/requests/cache, tied to the baseline file it was computed from. The
    copy is written atomically, and one that cannot be read or does not fit the model is computed
    again (`_cached_pq`).
    """
    key = (name, year)
    if key not in _PQ_CACHE:
        if len(_PQ_CACHE) >= 4:
            _PQ_CACHE.pop(next(iter(_PQ_CACHE)))
        stamp = processed("request", f"{name}.json").stat().st_mtime
        path = DATA / "requests" / "cache" / f"{name}_{year}.npz"
        pq = _cached_pq(path, stamp, model.C)
        if pq is None:
            # rounded as the cache stores it, so a fresh run and a cached one give the same answer
            pq = tuple(x.astype(np.float32).astype(float) for x in base_injection(model, _baseline(name), year, hp, ev))
            try:
                savez_atomic(path, p=pq[0].astype(np.float32), q=pq[1].astype(np.float32), stamp=stamp)
            except OSError:   # a read-only data folder only costs the next request some seconds
                log.warning("could not cache %s", path)
        _PQ_CACHE[key] = pq
    return _PQ_CACHE[key]


def _cached_pq(path, stamp: float, customers: int) -> tuple[np.ndarray, np.ndarray] | None:
    """The kept injection when it is whole, of this baseline file and of the model's size; None
    otherwise. A file that cannot be read (cut short before the writes were atomic, a full disk)
    is not an error: it only means computing the year again."""
    if not path.exists():
        return None
    try:
        with np.load(path) as d:
            if float(d["stamp"]) != stamp:
                return None
            p, q = d["p"].astype(float), d["q"].astype(float)
    except Exception as exc:  # noqa: BLE001 - whatever is wrong with the copy, the year is recomputed
        log.warning("ignoring the unreadable cache %s: %s", path, exc)
        return None
    if p.ndim != 2 or p.shape != q.shape or p.shape[0] != customers \
            or not (np.isfinite(p).all() and np.isfinite(q).all()):
        return None
    return p, q


def _no_check() -> None:
    """A Context's check when nobody can stop the evaluation (the queue's approvals, the CLI)."""


@dataclass
class Context:
    """The grid a request is evaluated against: a published baseline, and anything approved after it.

    `overrides` holds the houses approved into a queue: per house, its net injection for every
    weather year ("p": {year: (H,)}), its reactive power ("q"), and what the static study must
    count for it ("pv_kw", "ev_kw", "hp"). `states` replaces the published grid states with ones
    that include those houses.

    `check` is called at every stage boundary and in every per-year loop of `run`; the API's job
    pool (api/jobs.py) passes one that raises once the evaluation is cancelled or out of time.
    """

    name: str
    overrides: dict[int, dict] = field(default_factory=dict)
    states: dict[int, dict] | None = None
    label: dict | None = None
    check: Callable[[], None] = field(default=_no_check, repr=False, compare=False)

    def state(self, year: int) -> dict[str, np.ndarray]:
        return self.states[year] if self.states is not None else _state(self.name, year)

    def years(self, weather_years) -> list[int]:
        if self.states is not None:
            return [y for y in weather_years if y in self.states]
        return [y for y in weather_years if processed("request", f"{self.name}_{y}.npz").exists()]

    def pq(self, model: NetModel, year: int, hp: np.ndarray, ev: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        p, q = baseline_pq(model, self.name, year, hp, ev)
        if not self.overrides:
            return p, q
        p, q = p.copy(), q.copy()
        for c, ov in self.overrides.items():
            p[c] = ov["p"][year]
            q[c] = ov["q"][year]
        return p, q


# ------------------------------------------------------------------------------ live evaluation

@dataclass
class Request:
    cid: int
    pv_kwp: float = 0.0
    ev_kw: float = 0.0
    hp: bool = False
    bat_kwh: float = 0.0
    baseline: str = "static"
    ev_phases: int = 0          # 1 or 3; 0 = the usual (the small charger single-phase, larger three-phase)


def ev_single_phase(req: Request) -> bool:
    """Whether the requested charger is single-phase."""
    if req.ev_kw <= 0:
        return False
    if req.ev_phases in (1, 3):
        return req.ev_phases == 1
    return req.ev_kw <= A("ev_charger_single_phase_kw") + 1e-9


@memo(maxsize=16)
def _sessions(year: int) -> EvSessions:
    d = np.load(processed("ev", f"ev_sessions_{year}.npz"))
    return EvSessions(d["customer"], d["start"], d["end"], d["energy_kwh"])


def _applicant_profiles(req: Request, year: int, hp: np.ndarray, ev: np.ndarray, hp_new: bool) -> dict:
    cust = customers()
    prof = year_profiles(year)
    j = req.cid
    ilr = A("pv_dc_ac_ratio")
    cap = min(req.pv_kwp / ilr, float(cust.loc[j, "fuse_kva"])) if req.pv_kwp > 0 else 0.0
    pv = np.minimum(np.minimum(prof["pv_per_kwp"][j], 1.0 / ilr) * req.pv_kwp, cap)
    load = (prof["base"][j] * A("household_load_factor") + (prof["hp"][j] if hp[j] else 0.0)
            + (prof["ev"][j] if ev[j] else 0.0))
    hp_kw = np.asarray(prof["hp"][j], dtype=float) if hp_new else np.zeros(len(pv))
    return {"pv": pv.astype(float), "load": np.asarray(load, dtype=float), "cap": cap, "hp": hp_kw}


def export_headroom(model: NetModel, st: dict, j: int) -> np.ndarray:
    """Residual export headroom for customer j (kW per hour), unclipped and before verification."""
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
    return np.minimum(np.minimum(ev_v, ev_l), ev_t)


def import_headroom(model: NetModel, st: dict, j: int) -> np.ndarray:
    """Residual import headroom for customer j (kW per hour, ≥ 0), with a 2 % margin."""
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
    return np.clip(np.minimum(np.minimum(ev_v, ev_l), ev_t) * IMPORT_HEADROOM_SHARE, 0.0, None)


IMPORT_HEADROOM_SHARE = 0.98       # import_headroom's 2 % margin
_export_headroom, _import_headroom = export_headroom, import_headroom


def _headroom_all(model: NetModel, st: dict, side: str, chunk: int) -> np.ndarray:
    """(C, H): `export_headroom` (side "export") or `import_headroom` ("import") of every customer
    at once, the same expressions vectorised over customers, a block of hours at a time."""
    s = model.dv_dp                                              # (Kv, C) pu per kW
    vdev = st["vdev"].astype(float)
    pl, ql = st["pl"].astype(float), st["ql"].astype(float)
    pt, qt = st["pt"].astype(float), st["qt"].astype(float)
    sign = -1.0 if side == "export" else 1.0
    vhead = (model.v_max - (1.0 + vdev)) if side == "export" else ((1.0 + vdev) - model.v_min)
    pbar = np.sqrt(np.maximum(model.line_s_max_kw[:, None] ** 2 - ql ** 2, 0.0)) + sign * pl     # (L, H)
    room_t = np.sqrt(np.maximum(model.trafo_s_max_kw ** 2 - qt ** 2, 0.0)) + sign * pt            # (H,)
    path = model.phi > 0                                         # (L, C)
    sens = s > 1e-9
    C, H = s.shape[1], vdev.shape[1]
    out = np.empty((C, H))
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = np.where(sens, 1.0 / np.where(sens, s, 1.0), 0.0)  # (Kv, C), 0 where insensitive
        for a in range(0, H, chunk):
            b = min(H, a + chunk)
            ratio = np.where(sens[:, :, None], vhead[:, None, a:b] * inv[:, :, None], np.inf).min(axis=0)
            lines = np.where(path[:, :, None], pbar[:, None, a:b], np.inf).min(axis=0)
            out[:, a:b] = np.minimum(np.minimum(ratio, lines), room_t[None, a:b])
    if side == "import":
        out = np.clip(out * IMPORT_HEADROOM_SHARE, 0.0, None)
    return out


def export_headroom_all(model: NetModel, st: dict, chunk: int = 256) -> np.ndarray:
    """(C, H) residual export headroom of every customer: `export_headroom` for each, at once."""
    return _headroom_all(model, st, "export", chunk)


def import_headroom_all(model: NetModel, st: dict, chunk: int = 256) -> np.ndarray:
    """(C, H) residual import headroom of every customer (≥ 0, the 2 % margin): `import_headroom`."""
    return _headroom_all(model, st, "import", chunk)


def _serve_ev(room: np.ndarray, sessions: EvSessions, j: int, kw: float, H: int) -> dict:
    """Charge each of the house's EV sessions as fast as the charger and the envelope allow."""
    served = np.zeros(H)
    need = unmet = 0.0
    binding = 0
    sel = sessions.customer == j
    for a, b, e in zip(sessions.start[sel], sessions.end[sel], sessions.energy_kwh[sel]):
        rem = float(e)
        need += rem
        for h in range(int(a), min(int(b), H)):
            if rem <= 1e-9:
                break
            if room[h] < min(kw, rem) - 1e-6:
                binding += 1
            x = max(0.0, min(kw, room[h], rem))
            served[h] += x
            rem -= x
        unmet += rem
    return {"served": served, "need": need, "unmet": unmet, "binding": binding}


def _net(d: dict) -> np.ndarray:
    """The applicant's net injection before the export envelope: production minus every load served."""
    return d["pv"] - d["load"] - d["hp_s"] - d["ev_s"]


def _injection(d: dict) -> np.ndarray:
    """The applicant's net injection in the grid: exports held to the envelope, imports as served."""
    net = _net(d)
    if "env" not in d:
        return net
    out = np.where(net > 0, np.minimum(net, d["env"]), net)
    if "bat_dis" in d:
        out = out + d["bat_dis"]
    return out


def _reactive(d: dict) -> np.ndarray:
    return -(d["load"] + d["hp_s"] + d["ev_s"]) * np.tan(np.arccos(A("load_power_factor")))


def _verify_import(model: NetModel, p0: np.ndarray, q0: np.ndarray, j: int, d: dict) -> dict:
    """Re-check the hours where the import envelope binds; scale the served import down where it fails."""
    demand = d["hp"] + d["ev_want"]
    served = d["hp_s"] + d["ev_s"]
    hrs = np.flatnonzero((demand > 1e-6) & (d["head"] < demand - 1e-6))
    if not len(hrs):
        return {"hours_checked": 0, "hours_tightened": 0, "violations_after": 0}
    p, q = p0[:, hrs].copy(), q0[:, hrs].copy()
    p[j], q[j] = _injection(d)[hrs], _reactive(d)[hrs]
    bad = model.violations(model.solve(p, q), p).import_side
    n_tight = int(bad.sum())
    if bad.any():
        cols = np.flatnonzero(bad)
        hb = hrs[cols]
        base_inj = _injection(d)[hb] + served[hb]          # injection with none of the new import
        lo, hi = np.zeros(len(hb)), np.ones(len(hb))
        for _ in range(16):
            mid = (lo + hi) / 2
            pc, qc = p[:, cols].copy(), q[:, cols].copy()
            pc[j] = base_inj - served[hb] * mid
            qc[j] = -(d["load"][hb] + served[hb] * mid) * np.tan(np.arccos(A("load_power_factor")))
            okk = ~model.violations(model.solve(pc, qc), pc).import_side
            lo = np.where(okk, mid, lo)
            hi = np.where(okk, hi, mid)
        d["hp_s"][hb] *= lo
        d["ev_s"][hb] *= lo
        p[j], q[j] = _injection(d)[hrs], _reactive(d)[hrs]
        bad = model.violations(model.solve(p, q), p).import_side
    return {"hours_checked": int(len(hrs)), "hours_tightened": n_tight, "violations_after": int(bad.sum())}


def _verify_export(model: NetModel, p0: np.ndarray, q0: np.ndarray, j: int, d: dict) -> dict:
    """Re-check the hours where the export envelope binds; bisect the envelope down where it fails."""
    p, q = p0.copy(), q0.copy()
    p[j], q[j] = _injection(d), _reactive(d)
    net_j = _net(d)
    # hours where the applicant exports and its envelope is below the inverter capacity
    hrs = np.flatnonzero((d["desired"] > 0) & (d["env"] < d["cap"] - 1e-6))
    n_bad = n_tightened = n_baseline = 0
    if len(hrs):
        qh = q[:, hrs]
        res = model.solve(p[:, hrs], qh)
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
                okk = ~model.violations(model.solve(pc, qh[:, cols]), pc).export_side
                lo = np.where(okk, mid, lo)
                hi = np.where(okk, hi, mid)
            d["env"][hb] = env0 * lo
            p[j, hb] = np.where(net_j[hb] > 0, np.minimum(net_j[hb], d["env"][hb]), net_j[hb])
            res = model.solve(p[:, hrs], qh)
            bad = model.violations(res, p[:, hrs]).export_side
            # hours that still fail with zero export from the applicant are limits of the
            # baseline itself (existing customers exactly at their own envelopes)
            n_baseline = int(bad.sum())
        n_bad = int(bad.sum())
        d["curt"] = np.maximum(d["desired"] - d["env"], 0.0)
    return {"hours_checked": int(len(hrs)), "hours_tightened": n_tightened,
            "violations_after": n_bad - n_baseline, "baseline_limited_hours": n_baseline}


def battery_params(bat_kwh: float) -> dict:
    """Charge and discharge efficiencies (AC side) and power of a home battery (assumptions.toml)."""
    dc = float(np.sqrt(A("battery_dc_eff")))
    return {"kwh": float(bat_kwh), "kw": float(bat_kwh) * A("battery_kw_per_kwh"),
            "eta_in": A("battery_ac2bat_eff") * dc, "eta_out": dc * A("battery_bat2ac_eff")}


def _battery_dispatch(d: dict, bp: dict, blocked: np.ndarray | None = None) -> None:
    """Store what the envelope would curtail; give it back to the house's own load later.

    The battery charges only from curtailed energy, so the export it leaves is the envelope
    exactly; it discharges only while the house imports, never into the grid, and no more than
    the export headroom left in that hour (`blocked` closes hours the power flow refused).
    """
    H = len(d["pv"])
    curt = d["curt"]
    need = np.maximum(-_net(d), 0.0)
    room = np.maximum(d["env_raw"] - np.maximum(d["pv"], 0.0), 0.0)
    if blocked is not None:
        room = np.where(blocked, 0.0, room)
    kwh, kw, eta_in, eta_out = bp["kwh"], bp["kw"], bp["eta_in"], bp["eta_out"]
    ch, dis, soc = np.zeros(H), np.zeros(H), np.zeros(H)
    e = 0.0
    for t in range(H):
        c = min(curt[t], kw, (kwh - e) / eta_in)
        if c > 1e-9:
            e += c * eta_in
            ch[t] = c
        if need[t] > 1e-9 and e > 1e-9:
            x = min(need[t], kw, e * eta_out, room[t])
            if x > 1e-9:
                e -= x / eta_out
                dis[t] = x
        soc[t] = e
    d.update(bat_ch=ch, bat_dis=dis, bat_soc=soc)


def _verify_battery(model: NetModel, p0: np.ndarray, q0: np.ndarray, j: int, d: dict, bp: dict) -> dict:
    """Re-check every hour the battery discharges; close the hours that fail and dispatch again."""
    checked = 0
    blocked = np.zeros(len(d["pv"]), dtype=bool)
    for _ in range(3):
        hrs = np.flatnonzero(d["bat_dis"] > 1e-9)
        checked = max(checked, len(hrs))
        if not len(hrs):
            return {"hours_checked": checked, "hours_closed": int(blocked.sum()), "violations_after": 0}
        p, q = p0[:, hrs].copy(), q0[:, hrs].copy()
        p[j], q[j] = _injection(d)[hrs], _reactive(d)[hrs]
        bad = model.violations(model.solve(p, q), p).export_side
        if not bad.any():
            return {"hours_checked": checked, "hours_closed": int(blocked.sum()), "violations_after": 0}
        blocked[hrs[bad]] = True
        _battery_dispatch(d, bp, blocked)
    hrs = np.flatnonzero(d["bat_dis"] > 1e-9)
    p, q = p0[:, hrs].copy(), q0[:, hrs].copy()
    p[j], q[j] = _injection(d)[hrs], _reactive(d)[hrs]
    bad = model.violations(model.solve(p, q), p).export_side
    return {"hours_checked": checked, "hours_closed": int(blocked.sum()), "violations_after": int(bad.sum())}


def evaluate(model: NetModel, req: Request, hp: np.ndarray, ev: np.ndarray, emit=None,
             ctx: Context | None = None) -> dict:
    """Full evaluation; `emit(stage, payload)` is called as each stage finishes (for SSE)."""
    out, _ = run(model, req, hp, ev, emit=emit, ctx=ctx)
    return out


def run(model: NetModel, req: Request, hp: np.ndarray, ev: np.ndarray, emit=None, ctx: Context | None = None,
        verify_years: list[int] | None = None) -> tuple[dict, dict]:
    """The evaluation and, per weather year, the hourly arrays behind it (the queue keeps those)."""
    emit = emit or (lambda *_: None)
    ctx = ctx or Context(req.baseline)
    check = ctx.check
    check()
    cfg = settings()
    t_all = time.perf_counter()
    cust = customers()
    j = int(req.cid)
    base = _baseline(ctx.name)
    had_pv = j in set(base["pv_cids"])
    hp_new = bool(req.hp) and not bool(hp[j])
    want_import = req.ev_kw > 0 or hp_new
    bp = battery_params(req.bat_kwh) if (req.bat_kwh > 0 and req.pv_kwp > 0) else None
    ref = cfg.reference_year
    years = ctx.years(cfg.weather_years)
    verify_years = [y for y in (verify_years or [ref]) if y in years]
    out: dict = {"request": dict(req.__dict__), "baseline": req.baseline, "customer": {
        "cid": j, "address": str(cust.loc[j, "address"]), "fuse_a": int(cust.loc[j, "fuse_a"]),
        "fuse_kva": float(cust.loc[j, "fuse_kva"]), "roof_kwp": float(cust.loc[j, "kwp"]),
        "azimuth": float(cust.loc[j, "azimuth"]), "tilt": float(cust.loc[j, "tilt"]),
        "east_west": bool(cust.loc[j, "east_west"]), "dist_m": float(cust.loc[j, "dist_m"]),
        "feeder": int(cust.loc[j, "feeder"]), "had_pv_in_baseline": had_pv,
        "has_hp": bool(hp[j]), "has_ev": bool(ev[j])}}
    if ctx.label:
        out["queue"] = ctx.label

    # ---- 1) static method -------------------------------------------------------------------
    t0 = time.perf_counter()
    out["static"] = _static(model, req, base, hp, ev, ctx, hp_new)
    # the main fuse is the house's own limit, apart from the grid's: a key of its own, so the
    # queue's "firm only if every static study approves" keeps meaning the grid
    if want_import:
        out["fuse"] = _fuse_check(req, hp, ev, hp_new)
    # a single-phase device on each phase, on the unbalanced model (a key of its own, like the fuse)
    phases = _phase_check(req, base, hp, ev, ctx, hp_new)
    if phases is not None:
        out["phases"] = phases
    emit("static", {"static": out["static"], "ms": 1000 * (time.perf_counter() - t0)})
    check()

    # ---- 2) dynamic envelopes, every weather year (closed form) ------------------------------
    t0 = time.perf_counter()
    detail: dict[int, dict] = {}
    for y in years:
        check()
        st = ctx.state(y)
        prof = _applicant_profiles(req, y, hp, ev, hp_new)
        H = len(prof["pv"])
        d: dict = {"pv": prof["pv"], "load": prof["load"], "cap": prof["cap"], "hp": prof["hp"],
                   "hp_s": np.zeros(H), "ev_s": np.zeros(H), "ev_want": np.zeros(H)}
        if want_import:
            d["head"] = _import_headroom(model, st, j)
            d["hp_s"] = np.minimum(d["hp"], d["head"])          # the heat pump is served first
            if req.ev_kw > 0:
                room = np.minimum(np.maximum(d["head"] - d["hp_s"], 0.0), req.ev_kw)
                ev_full = _serve_ev(np.full(H, req.ev_kw), _sessions(y), j, req.ev_kw, H)
                ev_env = _serve_ev(room, _sessions(y), j, req.ev_kw, H)
                d["ev_want"], d["ev_s"] = ev_full["served"], ev_env["served"]
                d["ev_need"], d["ev_unmet"], d["ev_binding"] = ev_env["need"], ev_env["unmet"], ev_env["binding"]
        if req.pv_kwp > 0:
            released = np.zeros(H)
            if had_pv:
                old_kwp = float(cust.loc[j, "kwp"])
                old_cap = min(old_kwp / A("pv_dc_ac_ratio"), float(cust.loc[j, "fuse_kva"]))
                old_pv = np.minimum(np.minimum(year_profiles(y)["pv_per_kwp"][j], 1.0 / A("pv_dc_ac_ratio")) * old_kwp,
                                    old_cap)
                released = np.clip(old_pv - prof["load"], 0, None)
            # export space the house already uses in the baseline is released for the new system
            d["env_raw"] = _export_headroom(model, st, j) + released
            d["env"] = np.clip(d["env_raw"], 0.0, prof["cap"])
            d["desired"] = np.clip(_net(d), 0.0, None)
            d["curt"] = np.maximum(d["desired"] - d["env"], 0.0)
        detail[y] = d
    emit("envelope", {"years": len(years), "ms": 1000 * (time.perf_counter() - t0)})
    check()

    # ---- 3) verification with the nonlinear power flow ---------------------------------------
    t0 = time.perf_counter()
    checks: dict[int, dict] = {}
    for y in verify_years:
        check()
        d = detail[y]
        p0, q0 = ctx.pq(model, y, hp, ev)
        c: dict = {}
        if want_import:
            c["import"] = _verify_import(model, p0, q0, j, d)
            if req.pv_kwp > 0:      # less import served can mean more to export
                d["desired"] = np.clip(_net(d), 0.0, None)
                d["curt"] = np.maximum(d["desired"] - d["env"], 0.0)
        if req.pv_kwp > 0:
            c["export"] = _verify_export(model, p0, q0, j, d)
        if bp is not None:
            _battery_dispatch(d, bp)
            c["battery"] = _verify_battery(model, p0, q0, j, d, bp)
        checks[y] = c
    for y in years:
        if bp is not None and y not in checks:
            _battery_dispatch(detail[y], bp)
    method = "Z-bus nonlinear power flow on every hour where an envelope binds"
    verify: dict = {}
    if ref in checks:
        cr = checks[ref]
        if "export" in cr:
            verify.update(cr["export"])
        verify.update(year=ref, method=method)
        if "import" in cr:
            verify["import"] = cr["import"]
        if "battery" in cr:
            verify["battery"] = cr["battery"]
    verify["years_verified"] = sorted(checks)
    if len(checks) > 1:
        verify["all_years"] = {
            "export_violations_after": sum(c.get("export", {}).get("violations_after", 0) for c in checks.values()),
            "import_violations_after": sum(c.get("import", {}).get("violations_after", 0) for c in checks.values()),
            "battery_violations_after": sum(c.get("battery", {}).get("violations_after", 0) for c in checks.values())}
    out["verification"] = verify
    emit("verify", {"verification": verify, "ms": 1000 * (time.perf_counter() - t0)})
    check()

    # ---- per-year figures ----------------------------------------------------------------------
    per_year = []
    for y in years:
        d = detail[y]
        e: dict = {"year": y}
        if "curt" in d:
            e.update(pv_kwh=float(d["pv"].sum()), export_kwh=float(d["desired"].sum()),
                     curtailed_kwh=float(d["curt"].sum()), hours_limited=int((d["curt"] > 1e-3).sum()))
        if req.ev_kw > 0:
            e.update(ev_requested_kwh=float(d["ev_need"]), ev_unmet_kwh=float(d["ev_need"] - d["ev_s"].sum()),
                     ev_hours_limited=int(d["ev_binding"]))
        if hp_new:
            gap = d["hp"] - d["hp_s"]
            e.update(hp_kwh=float(d["hp"].sum()), hp_unmet_kwh=float(gap.sum()),
                     hp_hours_limited=int((gap > 1e-3).sum()))
        if bp is not None:
            e.update(bat_charged_kwh=float(d["bat_ch"].sum()), bat_discharged_kwh=float(d["bat_dis"].sum()),
                     curtailed_after_battery_kwh=float((d["curt"] - d["bat_ch"]).sum()))
        per_year.append(e)
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
    check()

    # ---- 5) economics at the real spot price -------------------------------------------------
    t0 = time.perf_counter()
    econ: dict = {}
    if req.pv_kwp > 0:
        rows = []
        for y in economics.price_years():
            if y not in detail:
                continue
            check()
            d = detail[y]
            self_use = np.minimum(d["pv"], d["load"] + d["hp_s"] + d["ev_s"])
            v_c = economics.value_sek(d["curt"], y)
            v_e = economics.value_sek(d["desired"] - d["curt"], y)
            v_s = economics.value_sek(self_use, y)
            row = {"year": y, "curtailed_kwh": float(d["curt"].sum()),
                   "curtailed_sek_gross": v_c["gross_sek"], "curtailed_sek_net": v_c["net_sek"],
                   "export_kwh": float((d["desired"] - d["curt"]).sum()), "export_sek": v_e["net_sek"],
                   "self_use_kwh": float(self_use.sum()), "self_use_spot_sek": v_s["net_sek"],
                   "negative_price_kwh_curtailed": v_c["negative_price_kwh"]}
            if bp is not None:
                v_b = economics.value_sek(d["bat_dis"], y)
                row.update(battery_discharged_kwh=float(d["bat_dis"].sum()), battery_sek=v_b["net_sek"])
            rows.append(row)
        if rows:
            econ = {"by_year": rows,
                    "curtailed_sek_mean": float(np.mean([r["curtailed_sek_gross"] for r in rows])),
                    "export_sek_mean": float(np.mean([r["export_sek"] for r in rows])),
                    "self_use_spot_sek_mean": float(np.mean([r["self_use_spot_sek"] for r in rows])),
                    "note": "Valued at the day-ahead spot price only (no grid fees, energy tax or compensation)."}
            if bp is not None:
                econ["battery_sek_mean"] = float(np.mean([r["battery_sek"] for r in rows]))
    out["economics"] = econ
    emit("economics", {"economics": econ, "ms": 1000 * (time.perf_counter() - t0)})
    check()

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
        if want_import:
            cap_i = (req.ev_kw if req.ev_kw > 0 else 0.0) + (float(d["hp"].max()) if hp_new else 0.0)
            prof_out["import_envelope_kw"] = np.round(np.minimum(d["head"], cap_i), 2).tolist()
        if hp_new:
            prof_out.update(hp_kw=np.round(d["hp"], 2).tolist(), hp_unmet_kw=np.round(d["hp"] - d["hp_s"], 2).tolist())
        if bp is not None:
            prof_out.update(battery_charge_kw=np.round(d["bat_ch"], 2).tolist(),
                            battery_discharge_kw=np.round(d["bat_dis"], 2).tolist(),
                            battery_soc_kwh=np.round(d["bat_soc"], 2).tolist(),
                            battery={"kwh": bp["kwh"], "kw": bp["kw"],
                                     "round_trip": bp["eta_in"] * bp["eta_out"]})
        out["profiles"] = prof_out
    out["summary"] = _summary(out, req, hp_new, bp is not None)
    out["seconds"] = time.perf_counter() - t_all
    emit("done", {"summary": out["summary"], "seconds": out["seconds"]})
    return out, detail


def fuse_verdict(house_peak_kva: float, three_phase_kw: float, single_phase_kw: float, fuse_a: float,
                 sizes: list[float], volts: float = 230.0) -> dict:
    """Whether a house's main fuse carries its own peak hour plus what a request adds.

    Three-phase load shares the three phases evenly; a single-phase device puts its whole current
    on one of them. The fuse holds when the busiest phase stays within its rating; when it does
    not, the smallest subscription in `sizes` that does is named (None above the largest).
    """
    per_phase_kva = (house_peak_kva + three_phase_kw) / 3.0 + single_phase_kw
    current = per_phase_kva * 1000.0 / volts
    ok = current <= fuse_a + 1e-9
    needed = None if ok else next((float(s) for s in sorted(sizes) if s + 1e-9 >= current), None)
    return {"fuse_a": float(fuse_a), "fuse_kva": 3.0 * volts * fuse_a / 1000.0,
            "house_peak_kva": float(house_peak_kva), "added_kw": float(three_phase_kw + single_phase_kw),
            "single_phase_kw": float(single_phase_kw), "phase_current_a": float(current), "ok": bool(ok),
            "fuse_needed_a": needed, "largest_a": float(max(sizes)) if len(sizes) else None}


def _fuse_check(req: Request, hp: np.ndarray, ev: np.ndarray, hp_new: bool) -> dict:
    """The import side of the main fuse: the house's peak hour in the reference year (its own use,
    any heat pump or car it already has, and the new heat pump), plus the new charger at full power."""
    cust = customers()
    j = int(req.cid)
    prof = _applicant_profiles(req, settings().reference_year, hp, ev, hp_new)
    house = (prof["load"] + prof["hp"]) / A("load_power_factor")
    single = req.ev_kw if ev_single_phase(req) else 0.0
    return fuse_verdict(float(house.max()), float(req.ev_kw - single), float(single),
                        float(cust.loc[j, "fuse_a"]), list(A("main_fuse_sizes_a")))


PHASE_LABELS = ("L1", "L2", "L3")


def solve_with_device(model3, p_kw: np.ndarray, q_kvar: np.ndarray, j: int, device_kw: float, phase: int,
                      customer_phase: np.ndarray | None = None):
    """The unbalanced model with one extra single-phase device at customer j's bus, on `phase`.

    The house's own row keeps its three-phase load; the device is a row of its own on the same
    bus. Z depends only on the network, so a shallow copy of the solver with one more customer row
    gives exactly what a customer of that size on that phase would.
    """
    import copy

    pf = copy.copy(model3.pf)
    pf.customer_ppc = np.append(model3.pf.customer_ppc, model3.pf.customer_ppc[j])
    phases = np.append(model3.phase if customer_phase is None else customer_phase, phase)
    p = np.append(np.asarray(p_kw, dtype=float), device_kw)[:, None]
    q = np.append(np.asarray(q_kvar, dtype=float), 0.0)[:, None]
    return pf.solve(p, q, phases, model3.vm_slack, flows=True)


def _phase_check(req: Request, base: dict, hp: np.ndarray, ev: np.ndarray, ctx: Context, hp_new: bool) -> dict | None:
    """A single-phase charger or inverter on each of the three phases, at the static study's
    worst-case hour, on the unbalanced model: the unbalance it causes and the voltages it leaves.

    The envelope's verification stays on the balanced model; this says which phase to connect to.
    """
    from .netmodel3ph import get_model3ph
    from .phases import THREE_PHASE

    cust = customers()
    j = int(req.cid)
    devices = []
    pv_cap = min(req.pv_kwp / A("pv_dc_ac_ratio"), float(cust.loc[j, "fuse_kva"])) if req.pv_kwp > 0 else 0.0
    if 0 < pv_cap <= A("pv_single_phase_max_kva") + 1e-9:
        devices.append(("pv", pv_cap))
    if ev_single_phase(req):
        devices.append(("ev", req.ev_kw))
    if not devices:
        return None
    m3 = get_model3ph()
    # the house's own row stays three-phase and the device is a row apart; a local copy, because
    # the model is shared by every evaluation running at the same time
    customer_phase = m3.phase.copy()
    customer_phase[j] = THREE_PHASE
    firm_mask = np.zeros(m3.C, dtype=bool)
    firm_mask[base["pv_cids"]] = True
    pv_kw = hosting.cap_kw().copy()
    hm = hp.copy()
    extra = np.zeros(m3.C)
    for c, ov in ctx.overrides.items():     # houses approved earlier in the queue are connected
        if ov.get("pv_kw", 0) > 0:
            firm_mask[c] = True
            pv_kw[c] = ov["pv_kw"]
        if ov.get("hp"):
            hm[c] = True
        extra[c] += ov.get("ev_kw", 0.0)
    if hp_new:
        hm[j] = True
    out = []
    for kind, kw in devices:
        if kind == "pv":
            # the summer snapshot without the applicant's panels; they go on one phase
            p, q = hosting.summer_snapshot(firm_mask, pv_kw)
            device = kw * A("static_pv_factor")     # the snapshot's share of the rating, as for the others
            season = "summer"
        else:
            p, q = hosting.winter_snapshot(hm, ev, extra)
            device = -kw
            season = "winter"
        rows = []
        for ph in range(3):
            res = solve_with_device(m3, p, q, j, device, ph, customer_phase)
            buses = m3.check_buses
            vuf = float(res.unbalance_pct[buses, 0].max())
            vm = res.vm[m3.check_rows, 0]
            rows.append({"phase": PHASE_LABELS[ph], "vuf_pct": vuf, "v_max_pu": float(vm.max()),
                         "v_min_pu": float(vm.min()), "neutral_a": float(res.i_neutral_ka[:, 0].max() * 1000.0)})
        limit = float(A("vuf_limit_pct"))
        vmax, vmin = A("v_max_pu") - A("v_margin_pu"), A("v_min_pu") + A("v_margin_pu")
        ok = [r["vuf_pct"] <= limit and vmin <= r["v_min_pu"] and r["v_max_pu"] <= vmax for r in rows]
        order = sorted(range(3), key=lambda i: (not ok[i], rows[i]["vuf_pct"]))
        best, worst = order[0], max(range(3), key=lambda i: (rows[i]["vuf_pct"], not ok[i]))
        out.append({"device": kind, "kw": float(kw), "season": season, "by_phase": rows,
                    "best": PHASE_LABELS[best], "worst": PHASE_LABELS[worst],
                    "ok_best": bool(ok[best]), "ok_all": bool(all(ok))})
    return {"devices": out, "limit_pct": float(A("vuf_limit_pct")),
            "ok": all(d["ok_best"] for d in out),
            "assumption": "existing single-phase systems on their phases by " + str(A("phase_allocation"))}


def _static(model: NetModel, req: Request, base: dict, hp: np.ndarray, ev: np.ndarray, ctx: Context,
            hp_new: bool) -> dict:
    cust = customers()
    j = req.cid
    static = {}
    firm_mask = np.zeros(model.C, dtype=bool)
    firm_mask[base["pv_cids"]] = True     # the static study treats every connected system as firm
    pv_kw = hosting.cap_kw().copy()
    hp_mask = hp.copy()
    extra = np.zeros(model.C)
    for c, ov in ctx.overrides.items():   # houses approved earlier in the queue count as connected
        if ov.get("pv_kw", 0) > 0:
            firm_mask[c] = True
            pv_kw[c] = ov["pv_kw"]
        if ov.get("hp"):
            hp_mask[c] = True
        extra[c] += ov.get("ev_kw", 0.0)
    if req.pv_kwp > 0:
        cap = min(req.pv_kwp / A("pv_dc_ac_ratio"), float(cust.loc[j, "fuse_kva"]))
        pv_kw[j] = cap
        m = firm_mask.copy()
        m[j] = True
        p_s, q_s = hosting.summer_snapshot(m, pv_kw)
        res = model.solve(p_s[:, None], q_s[:, None])
        v = model.violations(res, p_s[:, None])
        static["pv"] = {"approved": bool(~v.export_side[0]), "trafo_loading_pct": float(res.trafo_loading[0]),
                        "max_voltage_pu": float(v.vmax[0]), "max_line_loading_pct": float(v.max_line_loading[0]),
                        **_reason(v, res, 0, "export")}
    if req.ev_kw > 0 or hp_new:
        # the winter peak with every load the request adds: the charger at full power and the heat
        # pump at its design-hour demand
        x = extra.copy()
        x[j] += req.ev_kw
        hm = hp_mask.copy()
        if hp_new:
            hm[j] = True
        p_w, q_w = hosting.winter_snapshot(hm, ev, x)
        res = model.solve(p_w[:, None], q_w[:, None])
        v = model.violations(res, p_w[:, None])
        verdict = {"approved": bool(~v.import_side[0]), "trafo_loading_pct": float(res.trafo_loading[0]),
                   "min_voltage_pu": float(v.vmin[0]), "max_line_loading_pct": float(v.max_line_loading[0]),
                   **_reason(v, res, 0, "import")}
        if req.ev_kw > 0:
            static["ev"] = verdict
        if hp_new:
            static["hp"] = {**verdict, "design_kw": float(hosting.winter_design_load()["hp"][j])}
    return static


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


def _summary(out: dict, req: Request, hp_new: bool, battery: bool) -> dict:
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
    if "fuse" in out:
        s["fuse_ok"] = out["fuse"]["ok"]
    if "phases" in out:
        s["phases_ok"] = out["phases"]["ok"]
    if hp_new:
        s["hp_static_approved"] = out["static"]["hp"]["approved"]
        hp_kwh = sum(e.get("hp_kwh", 0) for e in out["per_year"])
        unmet = sum(e.get("hp_unmet_kwh", 0) for e in out["per_year"])
        s["hp_unmet_pct"] = 100 * unmet / max(hp_kwh, 1e-9)
        s["hp_hours_limited_ref"] = e_ref.get("hp_hours_limited", 0)
        s["hp_kwh_mean"] = hp_kwh / max(len(out["per_year"]), 1)
    if battery:
        curt = e_ref.get("curtailed_kwh", 0.0)
        s["bat_charged_ref_kwh"] = e_ref.get("bat_charged_kwh", 0.0)
        s["bat_discharged_ref_kwh"] = e_ref.get("bat_discharged_kwh", 0.0)
        s["bat_captured_ref_pct"] = 100 * e_ref.get("bat_charged_kwh", 0.0) / curt if curt > 1e-9 else None
        s["curtailment_after_battery_ref_pct"] = (100 * e_ref.get("curtailed_after_battery_kwh", 0.0)
                                                  / max(e_ref.get("pv_kwh", 1), 1e-9))
    return s
