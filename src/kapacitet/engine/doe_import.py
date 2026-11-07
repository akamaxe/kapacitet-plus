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


def _envelopes(model: NetModel, p_rest: np.ndarray, q_rest: np.ndarray, flex: np.ndarray, plugged: np.ndarray,
               charger: float, max_iter: int) -> tuple[np.ndarray, np.ndarray, int]:
    """Import envelopes (C, H) for the flexible chargers, every plugged-in one at its envelope at once.

    Returns the envelopes, the hours the state without flexible charging already breaks an
    import-side limit (left at the charger's rating: no envelope can repair them) and the
    iterations of the shrink loop. An hour whose firm state has no power-flow answer gets zero.
    """
    C, H = p_rest.shape
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    env = np.full((C, H), charger)
    firm_bad = model.import_critical_hours(p_rest, q_rest)   # violated even without flexible charging
    # An hour whose firm state has no power-flow answer is never excused: its envelope is zero.
    if len(firm_bad):
        fv = model.violations(model.solve(p_rest[:, firm_bad], q_rest[:, firm_bad]), p_rest[:, firm_bad])
        unsolved = firm_bad[fv.nonconverged]
        firm_bad = firm_bad[~fv.nonconverged]
    else:
        unsolved = firm_bad
    idx = np.flatnonzero(flex)
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
    if len(unsolved):
        env[np.ix_(idx, unsolved)] = 0.0
    return env, firm_bad, it


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
    env, firm_bad, it = _envelopes(model, p_rest, q_rest, flex, plugged_matrix(sessions, C, H) & flex[:, None],
                                   charger, max_iter)
    sched, unmet = _reschedule(sessions, env, charger, H, flex)
    residual = np.setdiff1d(model.import_critical_hours(p_rest - sched, q_rest - sched * tanphi), firm_bad)
    limited = (unc_sched > env + 1e-6).any(axis=0)   # hours where the envelope actually binds a charger
    res = ImportResult(schedule=sched.astype(np.float32), unmet_kwh=float(unmet), requested_kwh=requested,
                       limited_hours=int(limited.sum()), iterations=it, residual_violation_hours=len(residual),
                       uncontrolled_violation_hours=n_unc)
    res.firm_violation_hours = int(len(firm_bad))  # type: ignore[attr-defined]
    res.envelope = env                              # type: ignore[attr-defined]
    return res


# ------------------------------------------------------------ issued the day before (Operation)

@dataclass
class IssuedImport:
    """Import envelopes issued the day before for every flexible charger, with their checks."""

    flex: np.ndarray                 # (N,) customer indices with a flexible charger, in row order
    envelope: np.ndarray             # (N, H) kW each charger may draw, every one of them at once
    firm_kw: float                   # the firm import limit: the charger's rating, kept in any hour without an envelope
    critical_hours: int              # hours the planning state screened as needing an envelope
    limited_hours: int               # hours in which some charger is held below its rating
    firm_violation_hours: int        # planning-state hours over a limit with no flexible charging at all (envelope 0)
    planning_violation_hours: int    # hours the envelopes break a limit beyond the firm state: 0 to publish
    actual_violation_hours: int      # the real year with every charger at its envelope, beyond its own firm state
    retreats: int                    # halvings the verification needed

    @property
    def verified(self) -> bool:
        return self.planning_violation_hours == 0


def _broken(model: NetModel, p_rest: np.ndarray, q_rest: np.ndarray, draw: np.ndarray,
            excused: np.ndarray, tanphi: float) -> np.ndarray:
    """Hours in which the state with every charger drawing `draw` (C, H) breaks an import-side
    limit, nonlinear, outside `excused`. The linear screen never skips a violating hour, so only
    the hours it flags are solved."""
    p, q = p_rest - draw, q_rest - draw * tanphi
    hrs = np.setdiff1d(model.import_critical_hours(p, q), excused)
    if not len(hrs):
        return hrs
    v = model.violations(model.solve(p[:, hrs], q[:, hrs]), p[:, hrs])
    return hrs[v.import_side]


def issue_day_ahead(model: NetModel, plan: dict[str, np.ndarray], actual: dict[str, np.ndarray],
                    flex: np.ndarray, max_iter: int = 8, retreats: int = 12) -> IssuedImport:
    """Import envelopes allocated on a planning state that dominates for import, then verified.

    `plan` and `actual` hold `p_net` and `q` (C, H) of everything except flexible charging. The
    plan is made the day before, so it cannot know when a car arrives: every flexible charger is
    taken as plugged in every hour, which only makes the envelopes safer. Every charger at its
    envelope at once is solved against the plan; an hour that still breaks a limit has its
    envelopes halved until it does not, and is set to zero if halving runs out. An hour the
    plan breaks with no flexible charging at all gets zero, since nothing can be added to it.
    The real year is checked the same way and reported, not repaired (as `plan_exceedance` is).
    """
    C, H = plan["p_net"].shape
    charger = float(A("ev_charger_kw"))
    tanphi = float(np.tan(np.arccos(A("load_power_factor"))))
    idx = np.flatnonzero(flex)
    p_rest, q_rest = plan["p_net"].astype(float), plan["q"].astype(float)
    plugged = np.repeat(np.asarray(flex, dtype=bool)[:, None], H, axis=1)
    corner = charger * plugged
    critical = int(len(model.import_critical_hours(p_rest - corner, q_rest - corner * tanphi)))
    env, _, _ = _envelopes(model, p_rest, q_rest, flex, plugged, charger, max_iter)
    none = np.zeros((C, H))
    firm = _broken(model, p_rest, q_rest, none, np.zeros(0, int), tanphi)
    env[np.ix_(idx, firm)] = 0.0
    n = 0
    bad = _broken(model, p_rest, q_rest, np.where(plugged, env, 0.0), firm, tanphi)
    while len(bad) and n < retreats:
        env[np.ix_(idx, bad)] *= 0.5
        n += 1
        bad = _broken(model, p_rest, q_rest, np.where(plugged, env, 0.0), firm, tanphi)
    if len(bad):
        env[np.ix_(idx, bad)] = 0.0       # the firm state of those hours, which keeps its limits
        bad = _broken(model, p_rest, q_rest, np.where(plugged, env, 0.0), firm, tanphi)
    a_p, a_q = actual["p_net"].astype(float), actual["q"].astype(float)
    a_firm = _broken(model, a_p, a_q, none, np.zeros(0, int), tanphi)
    a_bad = _broken(model, a_p, a_q, np.where(plugged, env, 0.0), a_firm, tanphi)
    e = env[idx]
    return IssuedImport(flex=idx, envelope=e.astype(np.float32), firm_kw=charger, critical_hours=critical,
                        limited_hours=int((e < charger - 1e-6).any(axis=0).sum()) if len(idx) else 0,
                        firm_violation_hours=int(len(firm)), planning_violation_hours=int(len(bad)),
                        actual_violation_hours=int(len(a_bad)), retreats=n)
