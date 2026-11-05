"""Module 1 of a study: one station's capacity for new generation and new consumption, and its
weakest connection point, computed on the station's own data folder (results/capacity.json).

Generation (`generation_capacity`). The adoption orders are drawn from the candidates only: the
houses without existing PV and with a roof (`kwp` > 0). For each order, hosting.static_adopters
and hosting.ts_adopters give how many of them can add solar, the existing PV of the equipment
records being firm in every hour (scenario.existing). The station's value is
np.percentile(per_order, p, method="lower"), so it is one actual order's deployment, and that
order is recorded. Capacity is NOT monotone in the MV voltage: at a low MV the transformer binds
(the same power is more current), at a high MV the voltage does. So it is computed at both ends
of the study's band, and the lower of the two is published with the end it came from
(`binding_mv_pu`): the high end was meant as generation's worst case, and taking the minimum
keeps that decision conservative whichever end binds. The binding limit is what the next
adopter of the published order breaks (netmodel.Violations). An envelope (DOE) column is added
only when the DSO has approved the method (`approval_reference`) and the envelopes it gives
verify over the whole record.

Consumption (`consumption_capacity`). hosting.ts_import_adopters, with the study's device, at the
low end of the band, where voltage drop and transformer current are both worst, and the static
winter snapshot's count as a reference. A grid that already breaks an import limit today, before
anyone adds load, has no consumption capacity to publish: it is a finding, with its hours, never
0 kW. The same holds on the export side for a grid whose existing PV already breaks a limit.

The weakest point (`worst_point`). Each customer's residual headroom for a constant addition, by
the linear model (request.export_headroom_all / import_headroom_all) on today's grid in every
hour of the record; the WORST_K lowest are then searched with the nonlinear flow and confirmed on
every hour, and the lowest of them is the station's weakest point.
"""

from __future__ import annotations

import logging

import numpy as np

from ..config import A, settings
from ..study.config import METHOD_VERSION
from . import doe, hosting, request
from .netmodel import NetModel, get_model
from .scenario import Scenario, adoption_order, customers, existing, year_profiles

log = logging.getLogger(__name__)

SCHEMA = "kapacitet.capacity/1"
# study.toml's consumption_device -> the device name of hosting.ts_import_adopters, in one place
DEVICES = {"ev": "ev", "heat_pump": "hp"}
# an odd number of orders, so the 50th percentile ("lower") is the median and an actual order
STUDY_ORDERS = 21
QUICK_ORDERS = 5
WORST_K = 5                  # customers the weakest-point search confirms with the nonlinear flow
WORST_MAX_KW = 250.0
EXPORT_LIMITS = ("voltage", "cable", "transformer", "nonconverged")
IMPORT_LIMITS = EXPORT_LIMITS

__all__ = ["METHOD_VERSION", "SCHEMA", "DEVICES", "engine_device", "candidates", "candidate_order",
           "generation_capacity", "consumption_capacity", "worst_point", "peak_check"]


def engine_device(name: str) -> str:
    """The engine's device for study.toml's consumption_device ("heat_pump" is "hp")."""
    if name not in DEVICES:
        raise ValueError(f"consumption_device must be one of {sorted(DEVICES)}, not {name!r}")
    return DEVICES[name]


def _years(years) -> tuple[int, ...]:
    return tuple(settings().weather_years if years is None else years)


def _masks(C: int, hp, ev) -> tuple[np.ndarray, np.ndarray]:
    if hp is None or ev is None:
        from .run import background_masks
        bh, be = background_masks(C)
        hp = bh if hp is None else hp
        ev = be if ev is None else ev
    return np.asarray(hp, dtype=bool), np.asarray(ev, dtype=bool)


def pick(values, percentile: float) -> tuple[float, int]:
    """The `percentile` of `values` by numpy's "lower" method, an element of them, and the index of
    the first element equal to it."""
    v = np.asarray(values, dtype=float)
    value = float(np.percentile(v, percentile, method="lower"))
    return value, int(np.flatnonzero(v == value)[0])


# ---------------------------------------------------------------- which limit binds

def _classify(v, side: str) -> dict[str, int]:
    if side == "export":
        kinds = {"voltage": v.over_v, "cable": v.line_rev, "transformer": v.trafo_rev}
    else:
        kinds = {"voltage": v.under_v, "cable": v.line & ~v.line_rev, "transformer": v.trafo & ~v.trafo_rev}
    out = {k: int((m & ~v.nonconverged).sum()) for k, m in kinds.items()}
    out["nonconverged"] = int(v.nonconverged.sum())
    return out


def violating_hours(model: NetModel, p: np.ndarray, q: np.ndarray, side: str) -> np.ndarray:
    """The hours of (p, q) that break a limit on `side`, every one solved with the nonlinear flow.
    What a station publishes is checked this way, never by the linear screens alone: near the
    voltage limit at a deep drop the import screen's linear estimate can sit above the flow's by
    more than its margin (seen on the tiny grid at 1.02 pu, 0.91 pu at the far end)."""
    v = model.violations(model.solve(p, q), p)
    return np.flatnonzero(v.export_side if side == "export" else v.import_side)


def limit_of(model: NetModel, states, side: str) -> dict:
    """The limit a deployment breaks: `states` yields (year, p, q) for each year; the first year
    with a violating hour (every hour solved) is classified by netmodel.Violations. {"limit": the
    kind with the most hours, or None, "year", "hours", "by_limit": hours of each kind}."""
    for y, p, q in states:
        crit = violating_hours(model, p, q, side)
        if len(crit):
            v = model.violations(model.solve(p[:, crit], q[:, crit]), p[:, crit])
            by = _classify(v, side)
            limit = max(EXPORT_LIMITS, key=lambda k: (by[k], -EXPORT_LIMITS.index(k)))
            return {"limit": limit, "year": int(y), "hours": int(len(crit)), "by_limit": by}
    return {"limit": None, "year": None, "hours": 0, "by_limit": {}}


def verified_count(model: NetModel, n: int, states_of, side: str) -> tuple[int, dict, int]:
    """The largest count at most `n` whose deployment (`states_of(count)`) verifies on every hour,
    stepping down one at a time from `n` (the searches' answer, which their linear screens found):
    (count, its verification, how many steps it took)."""
    stepped = 0
    while True:
        check = verify(model, states_of(n), side)
        if not check["full_year_violations"] or n == 0:
            return n, check, stepped
        log.warning("%s count %d breaks a limit in %d hours on the full check; one fewer", side, n,
                    check["full_year_violations"])
        n -= 1
        stepped += 1


def verify(model: NetModel, states, side: str) -> dict:
    """Every hour of every year solved with the nonlinear flow: the violating hours on `side` (an
    hour without an answer counts) and the hours that did not converge."""
    bad = nonconv = hours = 0
    for _y, p, q in states:
        v = model.violations(model.solve(p, q), p)
        bad += int((v.export_side if side == "export" else v.import_side).sum())
        nonconv += int(v.nonconverged.sum())
        hours += p.shape[1]
    return {"full_year_violations": bad, "nonconverged_hours": nonconv, "hours_checked": hours}


# ---------------------------------------------------------------- generation

def candidates() -> np.ndarray:
    """(C,) the houses a new PV system can go on: no existing PV and a roof (kwp > 0)."""
    cust = customers()
    return (~existing()["has_pv"]) & (cust["kwp"].to_numpy(dtype=float) > 0)


def candidate_order(seed: int, cand: np.ndarray) -> np.ndarray:
    """Adoption order `seed` (scenario.adoption_order) over the network, keeping the candidates only."""
    full = adoption_order(seed, len(cand))
    return full[cand[full]]


def _pv_states(order: np.ndarray, k: int, C: int, hp, ev, years):
    mask = np.zeros(C, dtype=bool)
    mask[order[:k]] = True
    for y in years:
        arr = Scenario(year=y, pv=mask, hp=hp, ev=ev).arrays()
        yield y, arr["p_net"].astype(float), arr["q"].astype(float)


def export_baseline_violations(model: NetModel, hp, ev, years) -> dict[int, int]:
    """Hours of each year in which today's grid (the existing PV, today's heat pumps and EVs, nobody
    added) already breaks an export-side limit."""
    return {int(y): int(len(violating_hours(model, p, q, "export")))
            for y, p, q in _pv_states(np.zeros(0, dtype=int), 0, model.C, hp, ev, years)}


def _generation_order(model: NetModel, seed: int, order: np.ndarray, hp, ev, years) -> dict:
    cap = doe.customer_caps()
    kwp = customers()["kwp"].to_numpy(dtype=float)
    ms = hosting.static_adopters(model, order, hp, ev)
    static_ok = ms >= 0
    ms = max(ms, 0)
    mts = hosting.ts_adopters(model, order, hp, ev, years, lo=ms)
    return {"order": seed, "candidates": int(len(order)),
            "static_adopters": ms, "static_kw": float(cap[order[:ms]].sum()), "static_baseline_ok": bool(static_ok),
            "adopters": int(mts), "kw": float(cap[order[:mts]].sum()), "kwp": float(kwp[order[:mts]].sum())}


def generation_at(model: NetModel, orders: list[np.ndarray], percentile: float, hp, ev, years) -> dict:
    """The generation capacity of the orders at `model`'s MV voltage: every order's static and
    hourly count, the percentile of the hourly kW, and the order it is."""
    base = export_baseline_violations(model, hp, ev, years)
    if any(base.values()):
        return {"vm_pu": float(model.vm_slack), "status": "baseline_violates", "kw": None,
                "baseline_violation_hours": base}
    rows = [_generation_order(model, o, order, hp, ev, years) for o, order in enumerate(orders)]
    kw, i = pick([r["kw"] for r in rows], percentile)
    static_kw, _ = pick([r["static_kw"] for r in rows], percentile)
    return {"vm_pu": float(model.vm_slack), "status": "ok", "kw": kw, "order": rows[i]["order"],
            "adopters": rows[i]["adopters"], "kwp": rows[i]["kwp"], "static_kw": static_kw, "per_order": rows}


def generation_capacity(band: tuple[float, float], years=None, n_orders: int = STUDY_ORDERS,
                        percentile: float = 50.0, hp=None, ev=None, *, approval_reference: str = "",
                        budget: str = "aggregate", z_star: float | None = None) -> dict:
    """The station's generation capacity at both ends of the MV band, the lower one published (see
    the module's docstring). Returns {"status", "kw", "binding_mv_pu", "order", "adopters",
    "static_kw", "binding" (the limit the next adopter breaks), "by_end", "verification", "doe"}."""
    years = _years(years)
    lo_pu, hi_pu = float(band[0]), float(band[1])
    ends = sorted({lo_pu, hi_pu})
    m0 = get_model(vm_slack=ends[0])
    C = m0.C
    hp, ev = _masks(C, hp, ev)
    cand = candidates()
    orders = [candidate_order(o, cand) for o in range(n_orders)]
    by_end = {v: generation_at(get_model(vm_slack=v), orders, percentile, hp, ev, years) for v in ends}
    out = {"basis": "hourly, every candidate firm and nobody curtailed", "percentile": percentile,
           "candidates": int(cand.sum()), "existing_pv_kw_ac": float(existing()["pv_kw_ac"].sum()),
           "by_end": {f"{v:.2f}": r for v, r in by_end.items()}}
    broken = [v for v in ends if by_end[v]["status"] != "ok"]
    if broken:
        v = broken[-1]
        out.update(status="baseline_violates", kw=None, binding_mv_pu=v,
                   baseline_violation_hours=by_end[v]["baseline_violation_hours"],
                   finding="today's grid already breaks an export-side limit with the existing PV alone")
        return out
    # the lower value; on a tie, the high end (generation's worst case by the owner's decision)
    v = min(reversed(ends), key=lambda e: by_end[e]["kw"])
    r = by_end[v]
    m = get_model(vm_slack=v)
    order = orders[r["order"]]
    k = r["adopters"]
    binding = ({"limit": "candidates", "detail": "every candidate of the order fits"} if k >= len(order)
               else limit_of(m, _pv_states(order, k + 1, C, hp, ev, years), "export"))
    k, check, stepped = verified_count(m, k, lambda n: _pv_states(order, n, C, hp, ev, years), "export")
    if stepped:
        cap = doe.customer_caps()
        r = {**r, "adopters": k, "kw": float(cap[order[:k]].sum()),
             "kwp": float(customers()["kwp"].to_numpy(dtype=float)[order[:k]].sum())}
        binding = limit_of(m, _pv_states(order, k + 1, C, hp, ev, years), "export")
    out.update(status="ok", kw=r["kw"], binding_mv_pu=v, order=r["order"], adopters=k, kwp=r["kwp"],
               stepped_back=stepped,
               static_kw=min(by_end[e]["static_kw"] for e in ends), binding=binding, verification=check,
               cids=[int(c) for c in order[:k]])
    out["doe"] = doe_column(m, order, r, hp, ev, years, approval_reference, budget,
                            A("z_star_pct") if z_star is None else z_star)
    return out


def _doe_run(m: NetModel, order, ms: int, k: int, hp, ev, years, verify_: str) -> dict:
    nf = order[ms:k]
    mask = np.zeros(m.C, dtype=bool)
    mask[order[:k]] = True
    curt = pot = 0.0
    c_c, p_c = np.zeros(len(nf)), np.zeros(len(nf))
    full = 0
    for y in years:
        arr = Scenario(year=y, pv=mask, hp=hp, ev=ev).arrays()
        if len(nf):
            r = doe.compute(m, arr, nf, verify=verify_)
            c_c += r.curtailed.astype(float).sum(axis=1)
            full += int(r.full_year_violations or 0)
        p_c += arr["pv"][nf].astype(float).sum(axis=1)
    curt, pot = float(c_c.sum()), float(p_c.sum())
    share = np.where(p_c > 0, 100.0 * c_c / np.where(p_c > 0, p_c, 1.0), 0.0)
    return {"z_pct": 100.0 * curt / pot if pot > 0 else 0.0,
            "z_customer_max_pct": float(share.max()) if len(nf) else 0.0, "full_year_violations": full}


def doe_column(m: NetModel, order, r: dict, hp, ev, years, approval_reference: str, budget: str,
               z_star: float) -> dict:
    """The envelope capacity of the published order at the binding end, on the study's budget
    basis: only with the DSO's approval of the method, and only when its envelopes verify on every
    hour; otherwise the reason it is not published."""
    if not approval_reference.strip():
        return {"status": "not_approved", "detail": "[method] approval_reference is not set"}
    from .run import largest_true

    key = "z_pct" if budget == "aggregate" else "z_customer_max_pct"
    # the order's static count stays firm, as in the demo's envelopes
    ms = min(r["adopters"], next(x["static_adopters"] for x in r["per_order"] if x["order"] == r["order"]))

    runs: dict[int, dict] = {}

    def z(k: int) -> dict:
        if k not in runs:
            runs[k] = _doe_run(m, order, ms, k, hp, ev, years, "critical")
        return runs[k]

    k = largest_true(lambda kk: z(kk)[key] <= z_star, r["adopters"], len(order))
    full = _doe_run(m, order, ms, k, hp, ev, years, "full")
    if full["full_year_violations"]:
        return {"status": "not_verified", "full_year_violations": full["full_year_violations"]}
    cap = doe.customer_caps()
    return {"status": "ok", "approval_reference": approval_reference, "budget": budget, "z_star_pct": z_star,
            "firm_adopters": ms, "adopters": int(k), "kw": float(cap[order[:k]].sum()), "z_pct": full["z_pct"],
            "z_customer_max_pct": full["z_customer_max_pct"], "full_year_violations": 0}


# ---------------------------------------------------------------- consumption

def device_kw(device: str, years) -> np.ndarray:
    """(C,) what one new device of each house counts for: an EV charger its rating
    (ev_charger_kw), a heat pump its highest hourly electric power over `years`."""
    C = len(customers())
    if device == "ev":
        return np.full(C, float(A("ev_charger_kw")))
    return np.max([year_profiles(y)["hp"].max(axis=1) for y in years], axis=0).astype(float)


def _new_devices(order: np.ndarray, device: str, hp, ev) -> np.ndarray:
    has = {"ev": ev, "hp": hp}[device]
    return np.array([c for c in order if not has[c]], dtype=int)


def _load_states(new: np.ndarray, n: int, device: str, hp, ev, years):
    add = np.zeros(len(hp), dtype=bool)
    add[new[:n]] = True
    h = hp | add if device == "hp" else hp
    e = ev | add if device == "ev" else ev
    for y in years:
        p, q = hosting.consumption_state(y, h, e)
        yield y, p, q


def static_import_adopters(model: NetModel, new: np.ndarray, device: str, hp, ev) -> int:
    """How many of `new`, one after another, the static winter snapshot carries with the device
    added: a charger at its full rating, a heat pump at its own draw in the snapshot's hour."""
    w = hosting.winter_design_load()
    extra = np.full(model.C, float(A("ev_charger_kw"))) if device == "ev" else w["hp"]
    p0, q0 = hosting.winter_snapshot(hp, ev)
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    n = len(new)
    add = np.zeros((model.C, n + 1))
    for m in range(1, n + 1):
        add[new[:m], m] = extra[new[:m]]
    P = p0[:, None] - add
    Q = q0[:, None] - add * tanphi
    bad = np.flatnonzero(model.violations(model.solve(P, Q), P).import_side)
    return int(bad[0] - 1) if len(bad) else n


def consumption_capacity(model: NetModel, device: str = "ev", years=None, n_orders: int = STUDY_ORDERS,
                         percentile: float = 50.0, hp=None, ev=None) -> dict:
    """The station's capacity for new consumption at `model`'s MV voltage (a study passes the low end
    of its band): hosting.ts_import_adopters for every order, its percentile, the static winter
    count as a reference. A grid that already breaks an import limit is a finding with its hours."""
    years = _years(years)
    hp, ev = _masks(model.C, hp, ev)
    out = {"device": device, "mv_pu": float(model.vm_slack), "percentile": percentile,
           "basis": "hourly, no solar (the existing PV left out), today's heat pumps and EVs"}
    # every hour solved (hosting.import_baseline_violations counts them through the screen)
    base = {int(y): int(len(violating_hours(model, p, q, "import")))
            for y, p, q in _load_states(np.zeros(0, dtype=int), 0, device, hp, ev, years)}
    if any(base.values()):
        out.update(status="baseline_violates", kw=None, adopters=None, baseline_violation_hours=base,
                   finding="today's grid already breaks an import-side limit before anyone adds load")
        return out
    kw_each = device_kw(device, years)
    rows = []
    for o in range(n_orders):
        order = adoption_order(o, model.C)
        new = _new_devices(order, device, hp, ev)
        n = hosting.ts_import_adopters(model, order, device, hp, ev, years)
        ns = static_import_adopters(model, new, device, hp, ev)
        rows.append({"order": o, "candidates": int(len(new)), "adopters": int(n), "kw": float(kw_each[new[:n]].sum()),
                     "static_adopters": max(ns, 0), "static_kw": float(kw_each[new[:max(ns, 0)]].sum())})
    kw, i = pick([r["kw"] for r in rows], percentile)
    static_kw, _ = pick([r["static_kw"] for r in rows], percentile)
    r = rows[i]
    new = _new_devices(adoption_order(r["order"], model.C), device, hp, ev)
    n = r["adopters"]
    binding = ({"limit": "candidates", "detail": "every house of the order fits"} if n >= len(new)
               else limit_of(model, _load_states(new, n + 1, device, hp, ev, years), "import"))
    n, check, stepped = verified_count(model, n, lambda c: _load_states(new, c, device, hp, ev, years), "import")
    if stepped:
        kw = float(kw_each[new[:n]].sum())
        binding = limit_of(model, _load_states(new, n + 1, device, hp, ev, years), "import")
    if check["full_year_violations"]:
        raise RuntimeError(f"consumption: today's grid with no device added breaks a limit in "
                           f"{check['full_year_violations']} hours that the screens did not see")
    out.update(status="ok", kw=kw, adopters=n, order=r["order"], static_kw=static_kw, binding=binding,
               verification=check, stepped_back=stepped, per_order=rows, cids=[int(c) for c in new[:n]])
    return out


# ---------------------------------------------------------------- the weakest point

def _today(model: NetModel, side: str, hp, ev, years):
    """(year, p, q) of today's grid: the existing PV producing (export side), or no solar (import)."""
    C = model.C
    for y in years:
        if side == "export":
            arr = Scenario(year=y, pv=np.zeros(C, dtype=bool), hp=hp, ev=ev).arrays()
            yield y, arr["p_net"].astype(float), arr["q"].astype(float)
        else:
            p, q = hosting.consumption_state(y, hp, ev)
            yield y, p, q


def _bisect(model: NetModel, P0, Q0, rows, owner, n: int, side: str, max_kw: float) -> tuple[np.ndarray, np.ndarray]:
    """Per trial, the largest constant addition (export: injection at unity power factor; import:
    load at load_power_factor) at customer rows[c] that keeps its columns inside the limits of
    `side`; 24 halvings of [0, max_kw]. Returns (lo, hi): lo passes, hi fails (or is max_kw)."""
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    cols = np.arange(P0.shape[1])
    lo, hi = np.zeros(n), np.full(n, float(max_kw))
    for _ in range(24):
        mid = (lo + hi) / 2.0
        P, Q = P0.copy(), Q0.copy()
        if side == "export":
            P[rows, cols] += mid[owner]
        else:
            P[rows, cols] -= mid[owner]
            Q[rows, cols] -= mid[owner] * tanphi
        v = model.violations(model.solve(P, Q), P)
        bad = np.bincount(owner, weights=(v.export_side if side == "export" else v.import_side), minlength=n) > 0
        lo = np.where(bad, lo, mid)
        hi = np.where(bad, mid, hi)
    return lo, hi


def add_constant(p: np.ndarray, q: np.ndarray, j: int, kw: float, side: str) -> tuple[np.ndarray, np.ndarray]:
    """(p, q) with `kw` added at customer j in every hour, as the weakest-point search adds it."""
    p, q = p.copy(), q.copy()
    if side == "export":
        p[j] += kw
    else:
        tanphi = np.tan(np.arccos(A("load_power_factor")))
        p[j] -= kw
        q[j] -= kw * tanphi
    return p, q


def worst_point(model: NetModel, side: str = "export", years=None, hp=None, ev=None, k: int = WORST_K,
                top: int = hosting.CANDIDATES_PER_YEAR, max_kw: float = WORST_MAX_KW, max_rounds: int = 20) -> dict:
    """The customer with the least room for a constant addition on `side` ("export": generation,
    "import": consumption) on today's grid, at `model`'s MV voltage.

    The linear headroom of every customer in every hour (request.export_headroom_all or
    import_headroom_all, on request.build_state of today's grid) ranks them; the `k` lowest are
    searched with the nonlinear flow on their `top` tightest hours of each year, confirmed on every
    hour (each one solved), searched again with any hour they missed, and the lowest
    confirmed value is the weakest point: it passes on every hour, and `hi` (a bisection step
    above it) does not."""
    years = _years(years)
    hp, ev = _masks(model.C, hp, ev)
    cust = customers()
    states = list(_today(model, side, hp, ev, years))
    def crit(p, q):
        return violating_hours(model, p, q, side)

    base = {int(y): int(len(crit(p, q))) for y, p, q in states}
    out = {"side": side, "mv_pu": float(model.vm_slack), "k": int(k)}
    if any(base.values()):
        out.update(status="baseline_violates", kw=None, baseline_violation_hours=base)
        return out
    head = {}
    for y, p, q in states:
        st = request.build_state(model, p, q)
        head[y] = (request.export_headroom_all(model, st) if side == "export"
                   else request.import_headroom_all(model, st))
    per_year_min = np.stack([head[y].min(axis=1) for y, _, _ in states])          # (Y, C)
    linear = per_year_min.min(axis=0)
    ranked = np.argsort(linear, kind="stable")[:max(1, min(k, model.C))]
    cand = {int(j): {y: np.argpartition(head[y][j], min(top, head[y].shape[1]) - 1)[:top].astype(int)
                     for y, _, _ in states} for j in ranked}
    value, upper = {}, {}
    todo = [int(j) for j in ranked]
    for _ in range(max_rounds):
        P, Q, rows, owner = [], [], [], []
        for t, j in enumerate(todo):
            for y, p, q in states:
                h = cand[j][y]
                P.append(p[:, h])
                Q.append(q[:, h])
                rows.append(np.full(len(h), j))
                owner.append(np.full(len(h), t))
        lo, hi = _bisect(model, np.concatenate(P, axis=1), np.concatenate(Q, axis=1), np.concatenate(rows),
                         np.concatenate(owner), len(todo), side, max_kw)
        failed = []
        for t, j in enumerate(todo):
            value[j], upper[j] = float(lo[t]), float(hi[t])
            for y, p, q in states:
                missed = crit(*add_constant(p, q, j, value[j], side))
                if len(missed):
                    cand[j][y] = np.union1d(cand[j][y], missed).astype(int)
                    failed.append(j)
        if not failed:
            break
        log.info("worst point: %d customers met a limit outside their candidate hours; searching again",
                 len(set(failed)))
        todo = sorted(set(failed))
    else:
        raise RuntimeError(f"worst_point: {len(todo)} customers not confirmed after {max_rounds} rounds")
    j = min(value, key=lambda c: (value[c], c))
    y_min = int(states[int(np.argmin(per_year_min[:, j]))][0])
    worst = {"limit": None}
    if upper[j] < max_kw:
        worst = limit_of(model, ((y, *add_constant(p, q, j, upper[j], side)) for y, p, q in states), side)
    out.update(status="ok", cid=j, customer_id=str(cust.loc[j, "osm_id"]) if "osm_id" in cust.columns else str(j),
               kw=value[j], above_kw=upper[j], linear_kw=float(linear[j]), year=y_min,
               hour=int(np.argmin(head[y_min][j])), binding=worst,
               checked=[{"cid": int(c), "kw": value[c], "linear_kw": float(linear[c])} for c in ranked])
    return out


# ---------------------------------------------------------------- the peak check

def velander_kw(energy_kwh: float, heated: bool) -> float:
    """Velander's peak (kW) for a group of customers with annual energy W (kWh): k1·W + k2·√W, with
    the constants for houses with electric heating (velander_elvarme_*) or without (velander_k*)."""
    k1, k2 = ((A("velander_elvarme_k1"), A("velander_elvarme_k2")) if heated
              else (A("velander_k1"), A("velander_k2")))
    return float(k1 * energy_kwh + k2 * np.sqrt(max(energy_kwh, 0.0)))


def peak_check(meters: "object | None" = None, years=None, hp=None, ev=None) -> dict:
    """Velander's peak, the measured maximum and the modelled peak of today's load (no solar),
    side by side (peak_check.json). `meters` is processed/meters/hourly.parquet as a DataFrame,
    or None. The constants are those with electric heating when most customers have a heat pump.
    The measured figures are of the metered customers only, so the Velander and modelled figures
    are given for that same group too."""
    years = _years(years)
    C = len(customers())
    hp, ev = _masks(C, hp, ev)
    ref = settings().reference_year if settings().reference_year in years else years[-1]
    heated = bool(hp.mean() >= 0.5) if C else False
    peak, at, energy = -np.inf, None, None
    loads = {}
    for y in years:
        p, _ = hosting.consumption_state(y, hp, ev)
        load = -p
        total = load.sum(axis=0)
        h = int(np.argmax(total))
        if total[h] > peak:
            peak, at = float(total[h]), {"year": int(y), "hour": h}
        if y == ref:
            energy = load.sum(axis=1)
        loads[y] = load
    W = float(energy.sum())
    out = {"customers": C, "heated_share": float(hp.mean()) if C else 0.0,
           "constants": "elvarme" if heated else "ej_elvarme", "annual_kwh": W, "energy_year": int(ref),
           "velander_kw": velander_kw(W, heated),
           "velander_kw_by_constants": {"ej_elvarme": velander_kw(W, False), "elvarme": velander_kw(W, True)},
           "modelled_peak_kw": peak, "modelled_peak_at": at, "measured": None}
    if meters is not None and len(meters):
        cid = {str(c): i for i, c in enumerate(customers()["osm_id"].astype(str))}
        m = meters[meters["customer_id"].astype(str).isin(cid)]
        ids = sorted(set(m["customer_id"].astype(str)))
        if ids:
            wide = m.pivot_table(index="start", columns="customer_id", values="kwh_import", aggfunc="sum")
            complete = wide.dropna()
            rows = [cid[c] for c in ids]
            group_peak = max(float(loads[y][rows].sum(axis=0).max()) for y in years)
            if len(complete):
                total = complete.sum(axis=1)
                w_meas = float(total.mean() * 8760.0)
                out["measured"] = {"customers": len(ids), "hours": int(len(complete)),
                                   "max_kw": float(total.max()), "at": str(total.idxmax()),
                                   "annual_kwh": w_meas, "velander_kw": velander_kw(w_meas, heated),
                                   "modelled_peak_kw": group_peak,
                                   "note": "the metered customers only, over the hours every one of them has a reading"}
    return out
