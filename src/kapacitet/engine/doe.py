"""Dynamic Operating Envelopes (DOE) for non-firm PV customers, with nonlinear verification.

Customers connected within the static hosting capacity are *firm* (never limited). Later
adopters are *non-firm*: each hour they receive an export envelope E_i(t) such that the grid
stays within limits even if every non-firm customer exports up to its envelope at once:

    V0_n(t) + Σ_i S_ni·E_i ≤ V_max − m_v        (customer voltages; S = ∂|V|/∂P from Z-bus)
    P0_l(t) + Σ_{i below l} E_i ≤ P̄_l(t)        (cables; P̄ = √(S_max² − Q0²))
    P0_T(t) + Σ_i E_i ≤ P̄_T(t)                  (transformer, reverse flow)
    0 ≤ E_i ≤ Ē_i(t) = min(desired export d_i(t), inverter, fuse)       (demand-aware bound)

V0, P0, Q0 come from the nonlinear power flow of the firm state (non-firm customers export
nothing). The linearisation is corrected by an iterative, monotone tightening of the margins
using the nonlinear power flow, and a per-hour scaling back-off guarantees that only
verified envelopes are published. `desired export` uses perfect foresight of load and PV
(labelled as such in the UI).

The critical hours come from a linear screen, so the verification above only covers the hours
the screen kept. `verify_full_year` solves every hour in which a non-firm customer exports, with
the published envelope held, and `compute` runs it by default (`verify="full"`); the capacity
searches, which call `compute` many times and publish only the point they settle on, pass
`verify="critical"`.

Allocation policies:
  maxmin   — lexicographic max-min fairness by progressive filling (exact because the
             constraint matrix is non-negative); every customer gets the same kW unless
             it needs less or a constraint it affects binds.
  prorata  — the same, weighted by inverter rating: everyone is limited to the same share
             of its rating (a common DSO practice).
  propfair — proportional fairness, max Σ log E_i (Petrou et al., 2020), cvxpy + Clarabel.
  maxtotal — maximise Σ E_i (LP); shows the unfairness to end-of-feeder customers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .netmodel import NetModel, at_hours
from .powerflow import require_finite
from .scenario import customers


def customer_caps() -> np.ndarray:
    """Export capacity of each customer's PV system: inverter rating, capped by the main fuse."""
    from ..config import A

    c = customers()
    return np.minimum(c["kwp"].to_numpy() / A("pv_dc_ac_ratio"), c["fuse_kva"].to_numpy()).astype(float)

log = logging.getLogger(__name__)
EPS_V = 1e-4        # pu, added to voltage margins on every correction
EPS_KW = 0.05       # kW, added to thermal margins on every correction


@dataclass
class DoeResult:
    nonfirm: np.ndarray            # (N,) customer indices
    hours: np.ndarray              # (Hc,) hour indices where envelopes bind (critical hours)
    E: np.ndarray                  # (N, Hc) granted export envelope in critical hours (kW)
    desired: np.ndarray            # (N, H) desired export of non-firm customers (kW)
    cap: np.ndarray                # (N,) envelope outside critical hours (inverter/fuse cap, kW)
    curtailed: np.ndarray          # (N, H) curtailed energy per hour (kWh)
    iterations: int = 0
    backoff_hours: int = 0
    base_violation_hours: int = 0  # hours where the firm state alone violates a limit
    verified_violations: int = 0   # hours with violations after verification (must be 0)
    binding: np.ndarray | None = None   # (Hc,) which limit bound each critical hour: 0 voltage,
                                        # 1 cable, 2 transformer. The evidence a regulator needs
                                        # that curtailment was a last resort, not a preference.
    binding_line: np.ndarray | None = None  # (Hc,) pandapower line index when a cable bound, else -1
    info: dict = field(default_factory=dict)
    # How far the envelopes were checked: "critical" (the critical hours only, which the linear
    # screen chose) or "full" (every hour in which a non-firm customer exports, verify_full_year).
    verification: str = "critical"
    full_year_violations: int | None = None   # export violations in that full check (must be 0)

    def envelope(self, H: int) -> np.ndarray:
        """Published export limit (kW) per non-firm customer and hour."""
        env = np.repeat(self.cap[:, None], H, axis=1).astype(np.float32)
        env[:, self.hours] = self.E
        return env

    @property
    def curtailed_kwh(self) -> np.ndarray:
        return self.curtailed.sum(axis=1)


# --------------------------------------------------------------------------------- allocation

def waterfill(A: np.ndarray, b: np.ndarray, ub: np.ndarray, tol: float = 1e-9) -> np.ndarray:
    """Max-min fair allocation by progressive filling, vectorised over hours.

    A: (K, N) non-negative; b: (K, H) right-hand sides; ub: (N, H) upper bounds (≥ 0).
    Returns E (N, H). Rows with b < 0 are treated as b = 0 (their customers get 0).
    """
    K, N = A.shape
    H = b.shape[1]
    b = np.maximum(b, 0.0)
    E = np.zeros((N, H))
    active = ub > tol
    # customers of already-tight rows cannot grow
    tight0 = b <= tol
    if tight0.any():
        active &= ~((A.T @ tight0.astype(float)) > 0)
    AT = A.T.copy()
    cols = np.flatnonzero(active.any(axis=0))       # hours still being filled
    for _ in range(N + K + 1):
        if len(cols) == 0:
            break
        act = active[:, cols].astype(float)
        Ec = E[:, cols]
        bc = b[:, cols]
        r = A @ act                                 # growth rate of each row
        slack = bc - A @ Ec
        with np.errstate(divide="ignore", invalid="ignore"):
            step_rows = np.where(r > tol, slack / r, np.inf).min(axis=0)
        room = np.where(act > 0, ub[:, cols] - Ec, np.inf).min(axis=0)
        delta = np.maximum(np.minimum(step_rows, room), 0.0)
        delta = np.where(np.isfinite(delta), delta, 0.0)
        Ec = Ec + delta[None, :] * act
        E[:, cols] = Ec
        a = active[:, cols] & ((ub[:, cols] - Ec) > tol)
        tight = (bc - A @ Ec) <= tol * np.maximum(1.0, np.abs(bc))
        a &= ~((AT @ tight.astype(float)) > 0)
        active[:, cols] = a
        cols = cols[a.any(axis=0)]
    return np.minimum(E, ub)


def _cvx_batch(A: np.ndarray, b: np.ndarray, ub: np.ndarray, objective: str, batch: int = 96) -> np.ndarray:
    import cvxpy as cp

    K, N = A.shape
    H = b.shape[1]
    E_out = np.zeros((N, H))
    for a in range(0, H, batch):
        sl = slice(a, min(a + batch, H))
        bb, uu = np.maximum(b[:, sl], 0.0), np.maximum(ub[:, sl], 0.0)
        Hb = bb.shape[1]
        E = cp.Variable((N, Hb), nonneg=True)
        cons = [A @ E <= bb, E <= uu]
        if objective == "propfair":
            w = (uu > 1e-6).astype(float)
            # log(E + δ) keeps the problem finite when a bound is zero; δ = 1 W
            obj = cp.Maximize(cp.sum(cp.multiply(w, cp.log(E + 1e-3))))
        else:
            obj = cp.Maximize(cp.sum(E))
        prob = cp.Problem(obj, cons)
        try:
            prob.solve(solver=cp.CLARABEL)
        except Exception:  # pragma: no cover - fall back to SCS
            prob.solve(solver=cp.SCS, eps=1e-6)
        val = np.clip(np.nan_to_num(E.value), 0.0, uu)
        E_out[:, sl] = _within_rows(A, bb, val)
    return E_out


def _within_rows(A: np.ndarray, b: np.ndarray, E: np.ndarray, tol: float = 1e-9) -> np.ndarray:
    """E (N, H) with the solver's tolerance overshoot removed, so A E <= b holds exactly.

    Each customer on a row the solution overshoots is scaled by that row's factor b/(A E) (the
    smallest over its rows), and no other customer is touched. Scaling the whole hour by the worst
    row, as before, let a nanowatt over a row with no capacity left (b = 0) zero every customer of
    the hour, the ones on no binding limit included: more capacity elsewhere could then give a
    smaller total (found by tests/test_properties_allocation.py).
    """
    use = A @ E
    with np.errstate(divide="ignore", invalid="ignore"):
        row = np.clip(np.where(use > b + tol, b / use, 1.0), 0.0, 1.0)      # (K, H)
    factor = np.ones_like(E)
    on = A > 0
    for k in np.flatnonzero((row < 1.0).any(axis=1)):
        factor = np.where(on[k][:, None], np.minimum(factor, row[k][None, :]), factor)
    return E * factor


def allocate(policy: str, A: np.ndarray, b: np.ndarray, ub: np.ndarray,
             weights: np.ndarray | None = None) -> np.ndarray:
    if policy == "maxmin":
        return waterfill(A, b, ub)
    if policy == "prorata":
        # weighted max-min: every customer limited to the same share of its rating
        w = np.maximum(weights, 1e-6)
        return waterfill(A * w[None, :], b, ub / w[:, None]) * w[:, None]
    if policy in ("propfair", "maxtotal"):
        return _cvx_batch(A, b, ub, policy)
    raise ValueError(f"unknown policy {policy}")


# --------------------------------------------------------------------------------- DOE computation

VERIFY = ("full", "critical")


def held_injection(arrays: dict[str, np.ndarray], r: DoeResult) -> np.ndarray:
    """Net injection (C, H) with every non-firm customer of `r` held to its published envelope in
    every hour: it exports what it wants up to the envelope, and its consumption is untouched."""
    p_net = np.asarray(arrays["p_net"], dtype=float)
    p = p_net.copy()
    nf = np.asarray(r.nonfirm, dtype=int)
    if len(nf):
        p[nf] = np.minimum(p_net[nf], 0.0) + np.minimum(np.clip(p_net[nf], 0.0, None), r.envelope(p.shape[1]))
    return p


class EnvelopesNotVerified(RuntimeError):
    """Envelopes about to be published, or used as a baseline, that did not pass the full-year check."""


def require_verified(full_year_violations: int | None, where: str, *, nonconverged_hours: int = 0) -> None:
    """Refuse to go on with envelopes that break a limit in some hour of the year with them held.

    `full_year_violations` is `DoeResult.full_year_violations` (or a sum of them): None means the
    full-year check never ran, which is not a verification either. `where` names the policy, year
    or point, for the message. Hours without a converged power flow refuse as well.
    """
    if full_year_violations is None:
        raise EnvelopesNotVerified(f"{where}: the envelopes were not verified over the full year")
    if full_year_violations > 0 or nonconverged_hours > 0:
        raise EnvelopesNotVerified(
            f"{where}: {full_year_violations} hours break a limit with the envelopes held and "
            f"{nonconverged_hours} hours have no converged power flow; refusing to publish or use them")


# the scalar part of verify_full_year's answer, which a DoeResult keeps in info["full_year"]
FULL_YEAR_SUMMARY = ("hours_checked", "violation_hours", "export_violation_hours", "firm_violation_hours",
                     "nonconverged_hours", "pf_iterations", "pf_max_mismatch")


def verify_full_year(model: NetModel, arrays: dict[str, np.ndarray], r: DoeResult, vm_slack=None, *,
                     keep_state: bool = False, every_hour: bool = False) -> dict:
    """Solve the year again with the nonlinear power flow, every non-firm customer of `r` held to
    its published envelope, and count what still breaks an export-side limit.

    `r` may have been computed against another state than `arrays` (a day-ahead planning state
    checked against what happened). Only the hours in which some non-firm customer wants to
    export can differ from the firm state, so only those are solved; `every_hour` solves every
    hour instead, for the callers that count every violation of the year, and `keep_state` does
    the same and returns the state too ("p", "result", "violations"), for the callers that
    publish it.

    `violation_hours` counts the solved hours with a non-firm export that break a limit the firm
    state alone (non-firm customers exporting nothing) does not break, since no envelope can fix
    those; an hour without a converged, finite answer is never excused. It must be 0.
    `export_violation_hours` counts every export-side violation of the hours solved, the firm
    ones included: with `every_hour` or `keep_state` that is the whole year, and
    `import_violation_hours` and `any_violation_hours` count the import side and either side.
    """
    p_net = np.asarray(arrays["p_net"], dtype=float)
    q = np.asarray(arrays["q"], dtype=float)
    H = p_net.shape[1]
    nf = np.asarray(r.nonfirm, dtype=int)
    exporting = (p_net[nf] > 0).any(axis=0) if len(nf) else np.zeros(H, dtype=bool)
    hrs = np.arange(H) if keep_state or every_hour else np.flatnonzero(exporting)
    out = dict.fromkeys(FULL_YEAR_SUMMARY, 0)
    out.update(hours_checked=int(len(hrs)), import_violation_hours=0, any_violation_hours=0,
               pf_max_mismatch=0.0, max_voltage_pu=None,
               max_trafo_loading_pct=None, max_line_loading_pct=None)
    if len(hrs) == 0:
        return out
    p = held_injection(arrays, r)[:, hrs]
    res = model.solve(p, q[:, hrs], at_hours(vm_slack, hrs))
    v = model.violations(res, p)
    bad = v.export_side & exporting[hrs]
    excused = np.zeros(len(hrs), dtype=bool)
    ask = np.flatnonzero(bad & ~v.nonconverged)
    if len(ask):
        p_firm = p_net[:, hrs[ask]].copy()
        p_firm[nf] = np.minimum(p_firm[nf], 0.0)
        vf = model.violations(model.solve(p_firm, q[:, hrs[ask]], at_hours(vm_slack, hrs[ask])), p_firm)
        excused[ask] = vf.export_side & ~vf.nonconverged
    out.update(violation_hours=int((bad & ~excused).sum()), export_violation_hours=int(v.export_side.sum()),
               firm_violation_hours=int(excused.sum()), nonconverged_hours=int(v.nonconverged.sum()),
               import_violation_hours=int(v.import_side.sum()), any_violation_hours=int(v.any.sum()),
               pf_iterations=int(res.iterations), pf_max_mismatch=float(res.max_mismatch),
               max_voltage_pu=float(np.max(v.vmax)), max_trafo_loading_pct=float(np.max(v.trafo_loading)),
               max_line_loading_pct=float(np.max(v.max_line_loading)))
    if out["violation_hours"] or out["nonconverged_hours"]:
        log.warning("full-year verification: %d violation hours with the envelopes held, %d hours without a "
                    "converged power flow (of %d checked)", out["violation_hours"], out["nonconverged_hours"],
                    len(hrs))
    if keep_state:
        out.update(p=p, result=res, violations=v)
    return out


def _finish(model: NetModel, arrays: dict[str, np.ndarray], r: DoeResult, vm_slack, verify: str) -> DoeResult:
    """Record how far `r` was verified, running the full-year check when asked for."""
    r.verification = verify
    if verify == "full":
        full = verify_full_year(model, arrays, r, vm_slack)
        r.full_year_violations = full["violation_hours"]
        r.info["full_year"] = {k: full[k] for k in FULL_YEAR_SUMMARY}
    return r


def compute(model: NetModel, arrays: dict[str, np.ndarray], nonfirm: np.ndarray, *, policy: str = "maxmin",
            max_iter: int = 4, vm_slack=None, verify: str = "full") -> DoeResult:
    """Export envelopes for the non-firm customers of a scenario (arrays from Scenario.arrays()).

    `verify="full"` (the default) checks the result in every hour of the year as well, with
    `verify_full_year`, and records it in `full_year_violations`. "critical" checks the critical
    hours only: for the capacity searches, which call this many times and publish only the point
    they settle on. Non-finite input raises ValueError naming the hours.
    """
    if verify not in VERIFY:
        raise ValueError(f"verify must be one of {VERIFY}, not {verify!r}")
    p_net, q = arrays["p_net"].astype(float), arrays["q"].astype(float)
    require_finite(p_net, q, vm_slack)
    C, H = p_net.shape
    nonfirm = np.asarray(nonfirm, dtype=int)
    N = len(nonfirm)
    kwp_cap = customer_caps()
    desired = np.clip(p_net[nonfirm], 0.0, None)
    cap = kwp_cap[nonfirm]
    # With nobody to limit, or no hour in which a limit is reached, the record of what bound each
    # critical hour is empty, not missing: a missing one means a result from before it was kept,
    # which the audit trail refuses.
    nothing_bound = {"binding": np.zeros(0, np.int8), "binding_line": np.zeros(0, np.int32)}
    if N == 0:
        return _finish(model, arrays, DoeResult(nonfirm, np.zeros(0, int), np.zeros((0, 0)), desired, cap,
                                                np.zeros((0, H)), **nothing_bound), vm_slack, verify)

    # 1) Critical hours: every non-firm customer exporting as desired violates a limit
    #    (linear screen, confirmed with the nonlinear power flow).
    hours = model.export_critical_hours(p_net, q, vm_slack)
    Hc = len(hours)
    curtailed = np.zeros((N, H))
    if Hc == 0:
        return _finish(model, arrays, DoeResult(nonfirm, hours, np.zeros((N, 0)), desired, cap, curtailed,
                                                info={"critical_hours": 0, "policy": policy}, **nothing_bound),
                       vm_slack, verify)

    # 2) Firm state on critical hours: non-firm customers export nothing.
    p_base = p_net[:, hours].copy()
    p_base[nonfirm] = np.minimum(p_base[nonfirm], 0.0)
    q_c = q[:, hours]
    vs = at_hours(vm_slack, hours)
    res0 = model.solve(p_base, q_c, vs)
    v0 = np.abs(res0.v[model.check_rows])                                 # (Kv, Hc)
    base_viol = model.violations(res0, p_base)
    # 3) Linear constraints A E <= b
    Sv = model.dv_dp[:, nonfirm]                                          # (Kv, N)
    phi = model.phi[:, nonfirm]                                           # (L, N)
    keep = phi.sum(axis=1) > 0
    # identical downstream sets carry the same flow: keep only the lowest-rated cable of each set
    order_l = np.lexsort((model.line_s_max_kw, ))
    seen: dict[bytes, int] = {}
    for li in order_l:
        if not keep[li]:
            continue
        key = model.phi[li].tobytes()  # same full downstream set => same base flow
        if key in seen:
            keep[li] = False
        else:
            seen[key] = li
    phi_k = phi[keep]
    P0_l = model.phi[keep] @ p_base                                       # (Lk, Hc) toward substation
    Q0_l = model.phi[keep] @ q_c
    Pbar_l = np.sqrt(np.maximum(model.line_s_max_kw[keep, None] ** 2 - Q0_l ** 2, 0.0))
    P0_T = p_base.sum(axis=0)
    Q0_T = q_c.sum(axis=0)
    Pbar_T = np.sqrt(np.maximum(model.trafo_s_max_kw ** 2 - Q0_T ** 2, 0.0))
    A = np.vstack([Sv, phi_k, np.ones((1, N))])
    Kv, Kl = Sv.shape[0], phi_k.shape[0]
    line_idx = np.flatnonzero(keep)
    m_v = np.zeros((Kv, Hc))
    m_l = np.zeros((Kl, Hc))
    m_t = np.zeros((1, Hc))
    ub = np.minimum(desired[:, hours], cap[:, None])
    # Where the firm state has no converged, finite answer nothing can be verified against it:
    # the envelope there is zero, and the hour is still counted as a violation below.
    unsolved0 = base_viol.nonconverged
    ub[:, unsolved0] = 0.0

    def rhs() -> np.ndarray:
        b = np.vstack([model.v_max - v0 - m_v, Pbar_l - P0_l - m_l, (Pbar_T - P0_T)[None, :] - m_t])
        b[:, unsolved0] = 0.0
        return b

    E = np.zeros((N, Hc))
    it = 0
    for it in range(1, max_iter + 1):
        b = rhs()
        E = allocate(policy, A, b, ub, weights=cap)
        p_chk = p_base.copy()
        p_chk[nonfirm] += E
        res = model.solve(p_chk, q_c, vs)
        viol = model.violations(res, p_chk)
        bad = viol.export_side
        if not bad.any():
            break
        # tighten margins where the nonlinear flow exceeds the limit (monotone)
        v_nl = np.abs(res.v[model.check_rows])
        v_lin = v0 + Sv @ E
        over = v_nl > model.v_max + 1e-6
        m_v += np.where(over, np.maximum(v_nl - v_lin, 0.0) + EPS_V, 0.0)
        ll = res.line_loading[line_idx] / model.thermal
        m_l += np.where(ll > 1.0 + 1e-6, (ll - 1.0) * model.line_s_max_kw[line_idx, None] + EPS_KW, 0.0)
        tl = res.trafo_loading / model.thermal
        m_t += np.where(tl > 1.0 + 1e-6, (tl - 1.0) * model.trafo_s_max_kw + EPS_KW, 0.0)[None, :]

    # 4) Back-off: scale the envelopes of any still-violating hour until it verifies.
    backoff = 0
    if bad.any():
        idx = np.flatnonzero(bad)
        backoff = len(idx)
        lo, hi = np.zeros(len(idx)), np.ones(len(idx))
        for _ in range(18):
            mid = (lo + hi) / 2.0
            p_chk = p_base[:, idx].copy()
            p_chk[nonfirm] += E[:, idx] * mid[None, :]
            v2 = model.violations(model.solve(p_chk, q_c[:, idx], at_hours(vs, idx)), p_chk)
            ok = ~v2.export_side
            lo = np.where(ok, mid, lo)
            hi = np.where(ok, hi, mid)
        E[:, idx] *= lo[None, :]

    # 5) Final verification on all critical hours.
    p_chk = p_base.copy()
    p_chk[nonfirm] += E
    final = model.violations(model.solve(p_chk, q_c, vs), p_chk)
    # An hour the firm state alone breaks is not the envelopes' doing, unless the firm state has
    # no answer there: a non-converged or non-finite hour is never excused.
    firm_bad = base_viol.export_side & ~base_viol.nonconverged
    verified_bad = int((final.export_side & ~firm_bad).sum())

    curtailed[:, hours] = np.maximum(desired[:, hours] - E, 0.0)

    # Which limit bound each critical hour: the row of A E <= b with the least slack. A
    # regulator asking why a customer was curtailed gets the component, not just the fact.
    slack = rhs() - A @ E
    scale = np.concatenate([np.full(Kv, 0.01),                      # pu, so 1 % is "tight"
                            model.line_s_max_kw[line_idx] * 0.01,   # kW, 1 % of the rating
                            [model.trafo_s_max_kw * 0.01]])
    tight = np.argmin(slack / scale[:, None], axis=0)
    binding = np.where(tight < Kv, 0, np.where(tight < Kv + Kl, 1, 2)).astype(np.int8)
    binding_line = np.where(binding == 1, line_idx[np.clip(tight - Kv, 0, max(Kl - 1, 0))], -1)

    r = DoeResult(nonfirm=nonfirm, hours=hours, E=E.astype(np.float32), desired=desired.astype(np.float32),
                  cap=cap.astype(np.float32), curtailed=curtailed.astype(np.float32), iterations=it,
                  backoff_hours=backoff, base_violation_hours=int(base_viol.export_side.sum()),
                  verified_violations=verified_bad,
                  binding=binding, binding_line=binding_line.astype(np.int32),
                  info={"critical_hours": int(Hc), "policy": policy})
    return _finish(model, arrays, r, vm_slack, verify)


def fixed_cap(model: NetModel, arrays: dict[str, np.ndarray], nonfirm: np.ndarray, snapshot_p: np.ndarray,
              snapshot_q: np.ndarray, vm_slack=None) -> tuple[float, np.ndarray]:
    """Static export limit κ (share of each non-firm inverter rating) feasible in the worst case.

    snapshot_p/q: the worst-case firm state (C,) used by the static method. Returns κ and the
    curtailed energy per hour for the non-firm customers, (N, H).
    """
    cap = customer_caps()
    nonfirm = np.asarray(nonfirm, dtype=int)
    lo, hi = 0.0, 1.0
    for _ in range(20):
        mid = (lo + hi) / 2.0
        p = snapshot_p.copy()
        p[nonfirm] = cap[nonfirm] * mid
        v = model.violations(model.solve(p[:, None], snapshot_q[:, None], vm_slack), p[:, None])
        if v.export_side[0]:
            hi = mid
        else:
            lo = mid
    kappa = lo
    desired = np.clip(arrays["p_net"][nonfirm].astype(float), 0.0, None)
    curtailed = np.maximum(desired - kappa * cap[nonfirm, None], 0.0)
    return kappa, curtailed.astype(np.float32)
