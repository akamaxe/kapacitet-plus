"""Network model for the engine: power flow, limits and linear sensitivities in one place."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandapower as pp
import pandas as pd

from ..config import A
from ..grid.builder import load_net, load_tables
from .powerflow import PFResult, PowerFlowModel


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

    @property
    def any(self) -> np.ndarray:
        return self.over_v | self.under_v | self.line | self.trafo

    @property
    def export_side(self) -> np.ndarray:
        """Violations caused by generation: overvoltage and reverse-flow overloads."""
        return self.over_v | self.line_rev | self.trafo_rev

    @property
    def import_side(self) -> np.ndarray:
        """Violations caused by demand: undervoltage and forward-flow overloads."""
        return self.under_v | (self.line & ~self.line_rev) | (self.trafo & ~self.trafo_rev)


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
        dq = np.zeros((len(self.net.bus), self.C))
        dq[mask] = self.pf.dv_dq[pos_ns[mask]]
        self.dv_dq = dq[self.check_rows]
        v_nl = np.zeros(len(self.net.bus))
        v_nl[mask] = np.abs(self.pf.w0[pos_ns[mask]])
        self.v_noload = v_nl[self.check_rows] * self.vm_slack

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

    def screen_export(self, p_kw: np.ndarray, q_kvar: np.ndarray, screen_v: float = 0.006,
                      screen_frac: float = 0.06) -> np.ndarray:
        """Hours that *may* have an export-side violation (conservative linear screen).

        Voltage: no-load voltage plus the rise from positive injections only (loads and reactive
        power ignored, which can only overestimate the rise). Thermal: apparent power from the
        linear (lossless) active and reactive flows. Hours within the screening margins of a
        limit are returned for a full nonlinear check; the margins are validated in the tests.
        """
        p = np.asarray(p_kw, dtype=float)
        q = np.asarray(q_kvar, dtype=float)
        v_lin = self.v_noload[:, None] + self.dv_dp @ np.clip(p, 0.0, None)
        cand = v_lin.max(axis=0) > self.v_max - screen_v
        pl = self.phi @ p
        ql = self.phi @ q
        sl = np.hypot(pl, ql) / self.line_s_max_kw[:, None]
        cand |= ((sl > 1.0 - screen_frac) & (pl > 0)).any(axis=0)
        pt, qt = p.sum(axis=0), q.sum(axis=0)
        cand |= (np.hypot(pt, qt) / self.trafo_s_max_kw > 1.0 - screen_frac) & (pt > 0)
        return np.flatnonzero(cand)

    def export_critical_hours(self, p_kw: np.ndarray, q_kvar: np.ndarray, vm_slack=None) -> np.ndarray:
        """Hours with an export-side violation, found by screening + nonlinear confirmation."""
        cand = self.screen_export(p_kw, q_kvar)
        if len(cand) == 0:
            return cand
        vs = vm_slack if vm_slack is None or np.isscalar(vm_slack) else np.asarray(vm_slack)[cand]
        res = self.solve(p_kw[:, cand], q_kvar[:, cand], vs)
        return cand[self.violations(res, p_kw[:, cand]).export_side]

    def screen_import(self, p_kw: np.ndarray, q_kvar: np.ndarray, screen_v: float = 0.008,
                      screen_frac: float = 0.06) -> np.ndarray:
        """Hours that *may* have an import-side violation (conservative linear screen).

        Voltage drop from consumption only (generation ignored, which can only deepen the drop);
        thermal check on the forward (import) apparent power.
        """
        p = np.asarray(p_kw, dtype=float)
        q = np.asarray(q_kvar, dtype=float)
        v_lin = self.v_noload[:, None] - self.dv_dp @ np.clip(-p, 0.0, None) + self.dv_dq @ np.clip(q, None, 0.0)
        cand = v_lin.min(axis=0) < self.v_min + screen_v
        pl = self.phi @ p
        ql = self.phi @ q
        sl = np.hypot(pl, ql) / self.line_s_max_kw[:, None]
        cand |= ((sl > 1.0 - screen_frac) & (pl < 0)).any(axis=0)
        pt, qt = p.sum(axis=0), q.sum(axis=0)
        cand |= (np.hypot(pt, qt) / self.trafo_s_max_kw > 1.0 - screen_frac) & (pt < 0)
        return np.flatnonzero(cand)

    def import_critical_hours(self, p_kw: np.ndarray, q_kvar: np.ndarray, vm_slack=None) -> np.ndarray:
        cand = self.screen_import(p_kw, q_kvar)
        if len(cand) == 0:
            return cand
        vs = vm_slack if vm_slack is None or np.isscalar(vm_slack) else np.asarray(vm_slack)[cand]
        res = self.solve(p_kw[:, cand], q_kvar[:, cand], vs)
        return cand[self.violations(res, p_kw[:, cand]).import_side]

    # --------------------------------------------------------------------------------- solving
    def solve(self, p_kw: np.ndarray, q_kvar: np.ndarray, vm_slack=None, flows: bool = True) -> PFResult:
        vs = self.vm_slack if vm_slack is None else vm_slack
        return self.pf.solve_chunked(np.asarray(p_kw, dtype=float), np.asarray(q_kvar, dtype=float), vs,
                                     flows=flows)

    def violations(self, res: PFResult, p_kw: np.ndarray | None = None, tol_v: float = 1e-6,
                   tol_pct: float = 1e-4) -> Violations:
        """Limit violations per operating point. `p_kw` (C, H) gives flow directions on cables."""
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
        )


@lru_cache(maxsize=4)
def get_model(vm_slack: float | None = None, tap_pos: int | None = None, trafo_type: str | None = None) -> NetModel:
    return NetModel(vm_slack=vm_slack, tap_pos=tap_pos, trafo_type=trafo_type)
