"""Network model for the engine: power flow, limits and linear sensitivities in one place."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandapower as pp
import pandas as pd

from ..cache import memo
from ..config import A
from ..grid.builder import load_net, load_tables
from .powerflow import PFResult, PowerFlowModel, require_finite

log = logging.getLogger(__name__)


@dataclass
class Violations:
    over_v: np.ndarray     # (H,) bool
    under_v: np.ndarray    # (H,) bool
    line: np.ndarray       # (H,) bool, any cable above its rating
    trafo: np.ndarray      # (H,) bool
    line_rev: np.ndarray   # (H,) bool, overload with power flowing towards the substation
    trafo_rev: np.ndarray  # (H,) bool, overload with reverse power through the transformer
    vmax: np.ndarray       # (H,) max customer voltage
    vmin: np.ndarray       # (H,) min customer voltage
    max_line_loading: np.ndarray   # (H,) %
    trafo_loading: np.ndarray      # (H,) %
    # (H,) bool: the power flow did not converge or its state is not finite. Such an hour has no
    # answer, so it counts as a violation on both sides: a NaN would otherwise compare False
    # with every limit and pass.
    nonconverged: np.ndarray

    @property
    def any(self) -> np.ndarray:
        return self.over_v | self.under_v | self.line | self.trafo | self.nonconverged

    @property
    def export_side(self) -> np.ndarray:
        """Violations caused by generation: overvoltage and reverse-flow overloads."""
        return self.over_v | self.line_rev | self.trafo_rev | self.nonconverged

    @property
    def import_side(self) -> np.ndarray:
        """Violations caused by demand: undervoltage and forward-flow overloads."""
        return self.under_v | (self.line & ~self.line_rev) | (self.trafo & ~self.trafo_rev) | self.nonconverged


def unsolved(res) -> np.ndarray:
    """(H,) True where a power-flow result is no answer: not converged, or not finite anywhere.

    Works for the balanced and the unbalanced result alike; a result built by hand without
    convergence flags is judged on finiteness alone.
    """
    bad = ~np.isfinite(res.v).all(axis=0)
    for name in ("trafo_loading", "trafo_p_mw"):
        a = getattr(res, name, None)
        if a is not None and np.size(a):
            bad |= ~np.isfinite(a)
    lines = getattr(res, "line_loading", None)
    if lines is not None and np.size(lines):
        bad |= ~np.isfinite(lines).all(axis=0)
    converged = getattr(res, "converged", None)
    if converged is not None:
        bad |= ~np.asarray(converged, dtype=bool)
    if bad.any():
        log.debug("%d of %d operating points did not converge or are not finite; counted as violations",
                  int(bad.sum()), len(bad))
    return bad


def slack_per_hour(vm_slack, default: float, H: int) -> np.ndarray:
    """The MV voltage of each of H hours, from a scalar, an (H,) array or None (the model's own)."""
    return np.broadcast_to(np.asarray(default if vm_slack is None else vm_slack, dtype=float), (H,))


def at_hours(vm_slack, hours: np.ndarray):
    """`vm_slack` for a subset of hours: a scalar (or None) passes through, an array is indexed."""
    return vm_slack if vm_slack is None or np.ndim(vm_slack) == 0 else np.asarray(vm_slack)[hours]


# ------------------------------------------------------------------------------- the screens

IMPORT_SCREEN_V = 0.008    # pu, absolute margin of the import voltage screen
EXPORT_SCREEN_V = 0.006    # pu, absolute margin of the export voltage screen
SCREEN_FRAC = 0.06         # fraction of the rating, margin of both thermal screens
DROP_ALLOWANCE = 0.12      # relative margin on the import drop, for the second-order terms


def screen_bounds(v_noload_unit: np.ndarray, dv_dp: np.ndarray, dv_dp_cross: np.ndarray,
                  dv_dq: np.ndarray, v_min: float, p: np.ndarray, q: np.ndarray, vs: np.ndarray,
                  import_screen_v: float, drop_allowance: float = DROP_ALLOWANCE):
    """The linear bounds both screens judge an hour by, for a balanced or an unbalanced model.

    `v_noload_unit` (rows,) is the no-load voltage at 1 pu; `dv_dp` (rows, C) the sensitivity to
    active injection clipped at zero and `dv_dp_cross` its negative part (an injection that
    lowers a voltage: on the unbalanced model, the other phases of a single-phase house through
    the neutral); `dv_dq` (rows, C) the sensitivity to reactive injection; `p`/`q` (C, H) and
    `vs` (H,) the MV voltage. Returns `vs`, `v_hi` (rows, H) the export voltage bound, `v_lo`
    (rows, H) the import voltage bound and `k` (H,) = 1/V_low. A non-finite input gives NaN,
    which every comparison fails: the screens keep such hours by `_not_finite`.

    The bound, for any MV voltage of the band 0.98-1.06 pu. The nonlinear check flags a
    customer voltage past v_min/v_max by more than 1e-6 pu, a loading past the thermal limit by
    more than 1e-4 %, or an hour without a converged answer, on both sides; a screen has to keep
    every such hour. The sensitivities (Re Z, Im Z) and the kW ratings hold at 1 pu, the no-load
    voltage is exactly linear in vs, and a power S draws the current S/V, so every power term is
    divided by a voltage the hour cannot go below while it is inside the limits:

    - Import voltage: the drop of every injection that lowers a voltage (loads, generation
      through a negative coefficient, reactive power of the sign whose Im Z term is a drop)
      over v_min, not 1 pu. While no customer is below v_min no current is above S/v_min, and
      an hour that ends below v_min crosses v_min on the way, where the same holds. The drop is
      then raised by `drop_allowance` for what the first-order term leaves out (mostly the
      angle the drop itself opens, X sin(theta) on top of R cos(theta)), which the tests
      measure at up to 11 % of the drop near the limit; `import_screen_v` is a further absolute
      margin. The scale 1/min(vs, 1) used before was 1 above 1 pu, where the drop needed to
      reach v_min is the largest (0.09 pu at 1.00, 0.11 at 1.02, 0.15 at 1.06), so its error
      outgrew the 0.008 pu margin: the screen missed hours at 0.909999 pu.
    - V_low, the hour's lower voltage bound: the import bound's lowest customer voltage less
      `import_screen_v`, kept within [v_min, 1]. It holds only for an hour inside the lower
      limit, so both screens keep every hour the import bound cannot place there.
    - Export voltage: the rise of every injection that raises a voltage (generation, loads
      through a negative coefficient, reactive power of that sign; the screen used to leave the
      last two out, and an inverter injecting vars raises the voltage too) over V_low, as no
      current is above S/V_low. EXPORT_SCREEN_V is an absolute margin for the rest; the tests
      find the bound conservative without it. Both screens keep every hour the export bound
      cannot place inside the upper limit, as that is where a flow can fail to converge.
    - Thermal, both sides: the lossless apparent flow over V_low, against the 1 pu rating;
      SCREEN_FRAC covers the losses the lossless flow leaves out.

    Each term is at least the earlier screen's (which divided by min(vs, 1), left reactive
    power out of the rise and the negative coefficients out of both), so the candidates now
    hold the old ones: a result the nonlinear flow confirms changes only where the old screen
    missed an hour.
    """
    def pos(a):
        return np.clip(a, 0.0, None)

    gen, load = pos(p), pos(-p)
    dq_up, dq_down = pos(dv_dq), pos(-dv_dq)
    rise = dv_dp @ gen + dv_dp_cross @ load + dq_up @ pos(q) + dq_down @ pos(-q)
    drop = dv_dp @ load + dv_dp_cross @ gen + dq_up @ pos(-q) + dq_down @ pos(q)
    v0 = v_noload_unit[:, None] * vs[None, :]
    v_lo = v0 - drop * ((1.0 + drop_allowance) / v_min)
    v_low = np.clip(v_lo.min(axis=0) - import_screen_v, v_min, 1.0)
    k = 1.0 / v_low
    return vs, v0 + rise * k[None, :], v_lo, k


def outside_voltage_bounds(v_hi: np.ndarray, v_lo: np.ndarray, v_min: float, v_max: float,
                           import_screen_v: float, export_screen_v: float) -> np.ndarray:
    """(H,) the hours the linear bounds cannot place inside both voltage limits: both screens
    keep them, for an hour outside either limit may not converge (a violation on both sides)
    and below v_min V_low no longer holds."""
    return (v_lo.min(axis=0) < v_min + import_screen_v) | (v_hi.max(axis=0) > v_max - export_screen_v)


class NetModel:
    def __init__(self, vm_slack: float | None = None, tap_pos: int | None = None,
                 trafo_type: str | None = None):
        self.net = load_net()
        if tap_pos is not None:
            self.net.trafo["tap_pos"] = tap_pos
        if trafo_type is not None:
            pp.change_std_type(self.net, 0, trafo_type, element="trafo")
        self.buses, self.lines, self.cust = load_tables()
        self.C = len(self.cust)
        self.pf = PowerFlowModel(self.net, self.cust["bus"].to_numpy())
        self.vm_slack = A("mv_voltage_pu") if vm_slack is None else vm_slack
        self.v_max = A("v_max_pu") - A("v_margin_pu")
        self.v_min = A("v_min_pu") + A("v_margin_pu")
        self.thermal = A("thermal_limit_pct")

        # Bus rows (pandapower order) used for voltage limits: customer connection points.
        bus_pos = pd.Series(np.arange(len(self.net.bus)), index=self.net.bus.index)
        self.cust_bus_row = bus_pos[self.cust["bus"]].to_numpy()
        self.check_rows = np.unique(self.cust_bus_row)

        # Voltage sensitivity per kW (rows: check buses, cols: customers).
        dv = np.zeros((len(self.net.bus), self.C))
        pos_ns = self.pf.pos_in_ns[self.pf.bus_ppc]
        mask = pos_ns >= 0
        dv[mask] = self.pf.dv_dp[pos_ns[mask]]
        self.dv_dp = np.clip(dv[self.check_rows], 0.0, None)   # negative coefficients clipped (conservative)
        self.dv_dp_cross = np.clip(-dv[self.check_rows], 0.0, None)   # what the clip drops, for the screens
        dq = np.zeros((len(self.net.bus), self.C))
        dq[mask] = self.pf.dv_dq[pos_ns[mask]]
        self.dv_dq = dq[self.check_rows]
        v_nl = np.zeros(len(self.net.bus))
        v_nl[mask] = np.abs(self.pf.w0[pos_ns[mask]])
        # No-load voltage at 1 pu on the MV side. It is exactly linear in the MV voltage (w0 is),
        # so the screens scale it by the voltage of each hour; v_noload is the model's own MV.
        self.v_noload_unit = v_nl[self.check_rows]
        self.v_noload = self.v_noload_unit * self.vm_slack

        # Downstream incidence of customers per line (radial network) and thermal limits in kW.
        parent_line = {int(r.to_bus): int(r.line) for r in self.lines.itertuples()}
        parent_bus = {int(r.to_bus): int(r.from_bus) for r in self.lines.itertuples()}
        L = len(self.lines)
        phi = np.zeros((L, self.C), dtype=np.float32)
        for i, b in enumerate(self.cust["bus"].to_numpy()):
            x = int(b)
            while x in parent_line:
                phi[parent_line[x], i] = 1.0
                x = parent_bus[x]
        self.phi = phi
        line_imax = self.pf.line_imax_ka
        self.line_s_max_kw = np.sqrt(3) * 0.4 * line_imax * 1000.0 * self.thermal / 100.0
        self.trafo_s_max_kw = self.pf.trafo_sn_mva * 1000.0 * self.thermal / 100.0
        self.dist_m = self.cust["dist_m"].to_numpy()

    def _screen_bounds(self, p: np.ndarray, q: np.ndarray, vm_slack
                       ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """The MV voltage of each hour, the linear bounds both screens judge it by, and 1/V_low:
        `screen_bounds` on this model's sensitivities (the bound is documented there)."""
        vs = slack_per_hour(vm_slack, self.vm_slack, p.shape[1])
        return screen_bounds(self.v_noload_unit, self.dv_dp, self.dv_dp_cross, self.dv_dq, self.v_min,
                             p, q, vs, IMPORT_SCREEN_V)

    @staticmethod
    def _not_finite(p: np.ndarray, q: np.ndarray, vs: np.ndarray) -> np.ndarray:
        """Hours a screen cannot judge, so it keeps them: a NaN passes every comparison."""
        return ~(np.isfinite(p).all(axis=0) & np.isfinite(q).all(axis=0) & np.isfinite(vs))

    def screen_export(self, p_kw: np.ndarray, q_kvar: np.ndarray, screen_v: float = EXPORT_SCREEN_V,
                      screen_frac: float = SCREEN_FRAC, vm_slack=None) -> np.ndarray:
        """Hours that *may* have an export-side violation (conservative linear screen).

        Voltage: no-load voltage plus the rise from every injection that raises it (export, and
        reactive power of the sign that raises the voltage; loads ignored, which can only
        overestimate the rise), over the hour's lower voltage bound. Thermal: apparent power
        from the linear (lossless) active and reactive flows over the same bound. Hours within
        the screening margins of a limit are returned for a full nonlinear check; the bound is
        documented in `screen_bounds` and tested across the MV band. `vm_slack` (scalar or per
        hour, default the model's) is the MV voltage the hours run at; hours with non-finite
        input are always returned.
        """
        p = np.asarray(p_kw, dtype=float)
        q = np.asarray(q_kvar, dtype=float)
        vs, v_hi, v_lo, k = self._screen_bounds(p, q, vm_slack)
        cand = v_hi.max(axis=0) > self.v_max - screen_v
        cand |= outside_voltage_bounds(v_hi, v_lo, self.v_min, self.v_max, IMPORT_SCREEN_V, EXPORT_SCREEN_V)
        pl = self.phi @ p
        ql = self.phi @ q
        sl = np.hypot(pl, ql) / self.line_s_max_kw[:, None] * k[None, :]
        cand |= ((sl > 1.0 - screen_frac) & (pl > 0)).any(axis=0)
        pt, qt = p.sum(axis=0), q.sum(axis=0)
        cand |= (np.hypot(pt, qt) / self.trafo_s_max_kw * k > 1.0 - screen_frac) & (pt > 0)
        cand |= self._not_finite(p, q, vs)
        return np.flatnonzero(cand)

    def _confirm(self, p_kw: np.ndarray, q_kvar: np.ndarray, cand: np.ndarray, vm_slack, side: str) -> np.ndarray:
        """The candidate hours the nonlinear flow confirms on `side`. Non-finite input raises
        ValueError naming the hours of the whole input, not their place among the candidates."""
        if len(cand) == 0:
            return cand
        require_finite(p_kw, q_kvar, vm_slack)
        res = self.solve(p_kw[:, cand], q_kvar[:, cand], at_hours(vm_slack, cand))
        return cand[getattr(self.violations(res, p_kw[:, cand]), side)]

    def export_critical_hours(self, p_kw: np.ndarray, q_kvar: np.ndarray, vm_slack=None) -> np.ndarray:
        """Hours with an export-side violation, found by screening + nonlinear confirmation."""
        return self._confirm(p_kw, q_kvar, self.screen_export(p_kw, q_kvar, vm_slack=vm_slack), vm_slack,
                             "export_side")

    def screen_import(self, p_kw: np.ndarray, q_kvar: np.ndarray, screen_v: float = IMPORT_SCREEN_V,
                      screen_frac: float = SCREEN_FRAC, vm_slack=None) -> np.ndarray:
        """Hours that *may* have an import-side violation (conservative linear screen).

        Voltage drop from every injection that lowers the voltage (consumption, and reactive
        power of the sign that lowers it; generation ignored, which can only deepen the drop),
        taken at the lower voltage limit (`screen_bounds`); thermal check on the forward
        (import) apparent power over the hour's lower voltage bound. `vm_slack` as in
        `screen_export`.
        """
        p = np.asarray(p_kw, dtype=float)
        q = np.asarray(q_kvar, dtype=float)
        vs, v_hi, v_lo, k = self._screen_bounds(p, q, vm_slack)
        cand = v_lo.min(axis=0) < self.v_min + screen_v
        cand |= outside_voltage_bounds(v_hi, v_lo, self.v_min, self.v_max, IMPORT_SCREEN_V, EXPORT_SCREEN_V)
        pl = self.phi @ p
        ql = self.phi @ q
        sl = np.hypot(pl, ql) / self.line_s_max_kw[:, None] * k[None, :]
        cand |= ((sl > 1.0 - screen_frac) & (pl < 0)).any(axis=0)
        pt, qt = p.sum(axis=0), q.sum(axis=0)
        cand |= (np.hypot(pt, qt) / self.trafo_s_max_kw * k > 1.0 - screen_frac) & (pt < 0)
        cand |= self._not_finite(p, q, vs)
        return np.flatnonzero(cand)

    def import_critical_hours(self, p_kw: np.ndarray, q_kvar: np.ndarray, vm_slack=None) -> np.ndarray:
        return self._confirm(p_kw, q_kvar, self.screen_import(p_kw, q_kvar, vm_slack=vm_slack), vm_slack,
                             "import_side")

    # --------------------------------------------------------------------------------- solving
    def solve(self, p_kw: np.ndarray, q_kvar: np.ndarray, vm_slack=None, flows: bool = True,
              strict: bool = False) -> PFResult:
        """Nonlinear power flow of H hours. Non-finite input raises ValueError naming the hours;
        `strict` raises PowerFlowNotConverged where an hour did not converge (the validation
        stage and the tests), otherwise such an hour is a violation in `violations`."""
        p = np.asarray(p_kw, dtype=float)
        q = np.asarray(q_kvar, dtype=float)
        vs = self.vm_slack if vm_slack is None else vm_slack
        require_finite(p, q, vs)
        return self.pf.solve_chunked(p, q, vs, flows=flows, strict=strict)

    def violations(self, res: PFResult, p_kw: np.ndarray | None = None, tol_v: float = 1e-6,
                   tol_pct: float = 1e-4) -> Violations:
        """Limit violations per operating point. `p_kw` (C, H) gives flow directions on cables.

        An hour without a converged, finite answer is a violation of every kind (`nonconverged`)."""
        vm = np.abs(res.v[self.check_rows])
        vmax, vmin = vm.max(axis=0), vm.min(axis=0)
        over_line = res.line_loading > self.thermal + tol_pct            # (L, H)
        mll = res.line_loading.max(axis=0)
        if p_kw is not None:
            toward_sub = (self.phi @ np.asarray(p_kw, dtype=float)) > 0  # (L, H)
            line_rev = (over_line & toward_sub).any(axis=0)
        else:
            line_rev = over_line.any(axis=0) & (res.trafo_p_mw < 0)
        trafo_over = res.trafo_loading > self.thermal + tol_pct
        return Violations(
            over_v=vmax > self.v_max + tol_v,
            under_v=vmin < self.v_min - tol_v,
            line=over_line.any(axis=0),
            trafo=trafo_over,
            line_rev=line_rev,
            trafo_rev=trafo_over & (res.trafo_p_mw < 0),
            vmax=vmax, vmin=vmin, max_line_loading=mll, trafo_loading=res.trafo_loading,
            nonconverged=unsolved(res),
        )


@memo(maxsize=4)
def get_model(vm_slack: float | None = None, tap_pos: int | None = None, trafo_type: str | None = None) -> NetModel:
    return NetModel(vm_slack=vm_slack, tap_pos=tap_pos, trafo_type=trafo_type)
