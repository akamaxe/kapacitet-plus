"""Import envelopes for flexible EV charging ("smart charging within the plug-in window").

EV charging is the flexible part of demand; household load and heat pumps stay firm. In the
hours where uncontrolled charging would break an import-side limit (undervoltage, cable or
transformer overload in the forward direction), every flexible charger gets an import
envelope from the same max-min water-filling used for export (voltage-drop and forward-flow
rows). Energy that cannot be delivered is moved to later hours of the same plug-in session;
the schedule is re-checked until it verifies. The KPI is the energy still missing when the
car leaves.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config import A
from .doe import waterfill
from .netmodel import NetModel


@dataclass
class ImportResult:
    schedule: np.ndarray        # (C, H) EV charging power after control (kW)
    unmet_kwh: float            # energy not delivered by departure (all sessions)
    requested_kwh: float
    limited_hours: int          # hours in which some charger was limited
    iterations: int
    residual_violation_hours: int
    uncontrolled_violation_hours: int


def _reschedule(sessions, env: np.ndarray, charger_kw: float, H: int, flex: np.ndarray) -> tuple[np.ndarray, float]:
    """Charge each session as early as possible within its window under the envelope."""
    sched = np.zeros_like(env)
    unmet = 0.0
    for c, s, e, need in zip(sessions.customer, sessions.start, sessions.end, sessions.energy_kwh):
        if not flex[c]:
            continue
        rem = float(need)
        for h in range(int(s), min(int(e), H)):
            if rem <= 1e-9:
                break
            room = min(charger_kw, env[c, h]) - sched[c, h]
            if room <= 0:
                continue
            p = min(room, rem)
            sched[c, h] += p
            rem -= p
        unmet += rem
    return sched, unmet


def plugged_matrix(sessions, C: int, H: int) -> np.ndarray:
    plugged = np.zeros((C, H), dtype=bool)
    for c, s, e in zip(sessions.customer, sessions.start, sessions.end):
        plugged[c, int(s):min(int(e), H)] = True
    return plugged


def smart_charging(model: NetModel, arrays: dict[str, np.ndarray], ev_uncontrolled: np.ndarray,
                   sessions, flex: np.ndarray, max_iter: int = 4) -> ImportResult:
    """arrays: scenario arrays including uncontrolled EV load; flex: bool (C,) EVs under envelopes.

    Envelopes are simultaneously feasible: every plugged-in flexible car charging at its
    envelope at the same time keeps the grid within import-side limits, so any schedule below
    the envelopes (here: charge as early as possible) is safe.
    """
    C, H = arrays["p_net"].shape
    charger = A("ev_charger_kw")
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    ev_flex = np.where(flex[:, None], ev_uncontrolled, 0.0)
    p_rest = arrays["p_net"].astype(float) + ev_flex          # everything except flexible charging
    q_rest = arrays["q"].astype(float) + ev_flex * tanphi
    requested = float(sum(e for c, e in zip(sessions.customer, sessions.energy_kwh) if flex[c]))
    env = np.full((C, H), charger)
    unc_sched, _ = _reschedule(sessions, env, charger, H, flex)
    n_unc = len(model.import_critical_hours(p_rest - unc_sched, q_rest - unc_sched * tanphi))
    firm_bad = model.import_critical_hours(p_rest, q_rest)   # violated even without flexible charging
    idx = np.flatnonzero(flex)
    plugged = plugged_matrix(sessions, C, H) & flex[:, None]
    corner = charger * plugged
    crit = model.import_critical_hours(p_rest - corner, q_rest - corner * tanphi)
    crit = np.setdiff1d(crit, firm_bad)
    it = 0
    if len(crit) and len(idx):
        pb, qb = p_rest[:, crit], q_rest[:, crit]
        res0 = model.solve(pb, qb)
        v0 = np.abs(res0.v[model.check_rows])
        Sv = model.dv_dp[:, idx]
        phi = model.phi[:, idx]
        keep = phi.sum(axis=1) > 0
        P0 = -(model.phi[keep] @ pb)                           # forward (import) flow on cables
        Q0 = -(model.phi[keep] @ qb)
        Pbar = np.sqrt(np.maximum(model.line_s_max_kw[keep, None] ** 2 - Q0 ** 2, 0.0))
        PT, QT = -pb.sum(axis=0), -qb.sum(axis=0)
        PbarT = np.sqrt(np.maximum(model.trafo_s_max_kw ** 2 - QT ** 2, 0.0))
        Amat = np.vstack([Sv, phi[keep], np.ones((1, len(idx)))])
        b0 = np.vstack([v0 - model.v_min, Pbar - P0, (PbarT - PT)[None, :]])
        ub = corner[idx][:, crit]
        shrink = np.ones(len(crit))
        for it in range(1, max_iter + 1):
            alloc = waterfill(Amat, b0 * shrink[None, :], ub)
            p_chk = pb.copy()
            p_chk[idx] -= alloc                                # chargers at unity power factor
            v = model.violations(model.solve(p_chk, qb), p_chk)
            bad = v.import_side
            if not bad.any():
                break
            shrink = np.where(bad, shrink * 0.97, shrink)      # tighten only the hours that failed
        env_c = env[:, crit]
        env_c[idx] = np.minimum(env_c[idx], alloc)
        env[:, crit] = env_c
    sched, unmet = _reschedule(sessions, env, charger, H, flex)
    residual = np.setdiff1d(model.import_critical_hours(p_rest - sched, q_rest - sched * tanphi), firm_bad)
    limited = (unc_sched > env + 1e-6).any(axis=0)   # hours where the envelope actually binds a charger
    res = ImportResult(schedule=sched.astype(np.float32), unmet_kwh=float(unmet), requested_kwh=requested,
                       limited_hours=int(limited.sum()), iterations=it, residual_violation_hours=len(residual),
                       uncontrolled_violation_hours=n_unc)
    res.firm_violation_hours = int(len(firm_bad))  # type: ignore[attr-defined]
    res.envelope = env                              # type: ignore[attr-defined]
    return res
