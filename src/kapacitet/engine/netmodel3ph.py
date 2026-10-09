"""Unbalanced network model: the same contract as `NetModel`, one voltage per phase.

`hosting.py` only asks a model for `C`, `solve`, `violations` and `export_critical_hours`,
so this class is a drop-in replacement and the capacity calculation can be run either way
and the two compared. Everything the balanced model checks on one voltage per bus is
checked here on three, and the limits that matter per conductor are checked per conductor.

The screens keep the invariant of the balanced ones: they may return an hour that turns out
fine, never skip one that violates. Each phase is screened on its own, counting only the
terms that can push that phase towards its limit, and `tests/test_network_model3ph.py`
hunts for a counterexample over random adoption patterns.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandapower as pp
import pandas as pd

from ..config import A
from ..grid.builder import load_net, load_tables
from ..paths import processed
from .netmodel import Violations
from .phases import THREE_PHASE, phase_of
from .powerflow3ph import PF3Result, PowerFlow3Ph


def inverter_kw() -> np.ndarray | None:
    """AC rating of each customer's PV, or None when the profiles have not been built."""
    path = processed("pv", "roofs.parquet")
    if not path.exists():
        return None
    roofs = pd.read_parquet(path).sort_values("cid")
    return roofs["inverter_kw"].to_numpy()


@dataclass
class Unbalance:
    """What only the unbalanced model can say."""

    worst_pct: np.ndarray        # (H,) voltage unbalance factor, percent
    neutral_a: np.ndarray        # (H,) largest neutral current, amperes
    over_limit: np.ndarray       # (H,) bool, above the EN 50160 unbalance limit


class NetModel3Ph:
    def __init__(self, phase: np.ndarray | None = None, rule: str | None = None,
                 vm_slack: float | None = None, tap_pos: int | None = None,
                 trafo_type: str | None = None):
        self.net = load_net()
        if tap_pos is not None:
            self.net.trafo["tap_pos"] = tap_pos
        if trafo_type is not None:
            pp.change_std_type(self.net, 0, trafo_type, element="trafo")
        self.buses, self.lines, self.cust = load_tables()
        self.C = len(self.cust)
        self.pf = PowerFlow3Ph(self.net, self.cust["bus"].to_numpy())
        self.phase = phase_of(self.cust, inverter_kw(), rule) if phase is None else np.asarray(phase)
        if len(self.phase) != self.C:
            raise ValueError("one phase per customer is required")

        self.vm_slack = A("mv_voltage_pu") if vm_slack is None else vm_slack
        self.v_max = A("v_max_pu") - A("v_margin_pu")
        self.v_min = A("v_min_pu") + A("v_margin_pu")
        self.thermal = A("thermal_limit_pct")
        self.unbalance_limit = 2.0           # EN 50160, negative over positive sequence

        # Voltage is checked on all three phases of every customer bus.
        bus_pos = pd.Series(np.arange(len(self.net.bus)), index=self.net.bus.index)
        self.cust_bus_row = bus_pos[self.cust["bus"]].to_numpy()
        buses = np.unique(self.cust_bus_row)
        self.check_buses = buses
        self.check_rows = (3 * buses[:, None] + np.arange(3)).ravel()

        self._build_sensitivities()
        self._build_incidence()
        self.dist_m = self.cust["dist_m"].to_numpy()

    # ---------------------------------------------------------------------- linear model
    def _customer_columns(self) -> np.ndarray:
        """Column of Z for each customer, averaged over phases for three-phase services."""
        pf = self.pf
        cols = np.zeros((len(pf.ns), self.C), dtype=complex)
        single = self.phase >= 0
        if single.any():
            rows = pf.pos_in_ns[3 * pf.customer_ppc[single] + self.phase[single]]
            cols[:, single] = pf.Z[:, rows]
        if (~single).any():
            idx = np.flatnonzero(~single)
            for ph in range(3):
                rows = pf.pos_in_ns[3 * pf.customer_ppc[idx] + ph]
                cols[:, idx] += pf.Z[:, rows] / 3.0
        return cols

    def _build_sensitivities(self) -> None:
        pf = self.pf
        cols = self._customer_columns() / (1000.0 * pf.base_mva)
        n = 3 * pf.n_bus
        dv = np.zeros((n, self.C))
        dq = np.zeros((n, self.C))
        rows_ns = pf.ns
        dv[rows_ns] = cols.real
        dq[rows_ns] = cols.imag
        # map from ppc rows to pandapower bus rows, then keep the checked ones
        order = (3 * pf.bus_ppc[:, None] + np.arange(3)).ravel()
        self.dv_dp = np.clip(dv[order][self.check_rows], 0.0, None)   # conservative
        self.dv_dq = dq[order][self.check_rows]

        self.v_noload = self._no_load_voltage()

    def _no_load_voltage(self) -> np.ndarray:
        """Voltage magnitude with nothing connected, per checked bus and phase."""
        res = self.pf.solve(np.zeros((self.C, 1)), None, self.phase, self.vm_slack, flows=False)
        return res.vm[self.check_rows, 0]

    def _build_incidence(self) -> None:
        """Downstream customers per line, split by the phase they sit on."""
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
        # share of each customer's power that lands on each phase
        share = np.zeros((3, self.C))
        for ph in range(3):
            share[ph] = (self.phase == ph).astype(float)
        share += (self.phase == THREE_PHASE).astype(float) / 3.0
        self.phase_share = share
        self.phi_phase = np.stack([phi * share[ph] for ph in range(3)])      # (3, L, C)

        imax = self.pf.line_imax_ka
        three_phase_kva = np.sqrt(3) * 0.4 * imax * 1000.0 * self.thermal / 100.0
        self.line_s_max_kw = three_phase_kva
        self.line_s_max_phase_kw = three_phase_kva / 3.0
        self.trafo_s_max_kw = self.pf.trafo_sn_mva * 1000.0 * self.thermal / 100.0
        self.trafo_s_max_phase_kw = self.trafo_s_max_kw / 3.0

    # ------------------------------------------------------------------------- screening
    def _phase_flows(self, p: np.ndarray, q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Linear per-phase line flows, shape (3, L, H)."""
        return (np.einsum("plc,ch->plh", self.phi_phase, p),
                np.einsum("plc,ch->plh", self.phi_phase, q))

    def screen_export(self, p_kw: np.ndarray, q_kvar: np.ndarray, screen_v: float = 0.010,
                      screen_frac: float = 0.08) -> np.ndarray:
        """Hours that may have an export-side violation. Never skips one that has."""
        p = np.asarray(p_kw, dtype=float)
        q = np.asarray(q_kvar, dtype=float)
        v_lin = self.v_noload[:, None] + self.dv_dp @ np.clip(p, 0.0, None)
        cand = v_lin.max(axis=0) > self.v_max - screen_v
        pl, ql = self._phase_flows(p, q)
        sl = np.hypot(pl, ql) / self.line_s_max_phase_kw[None, :, None]
        cand |= ((sl > 1.0 - screen_frac) & (pl > 0)).any(axis=(0, 1))
        pt = self.phase_share @ p
        qt = self.phase_share @ q
        st = np.hypot(pt, qt) / self.trafo_s_max_phase_kw
        cand |= ((st > 1.0 - screen_frac) & (pt > 0)).any(axis=0)
        return np.flatnonzero(cand)

    def screen_import(self, p_kw: np.ndarray, q_kvar: np.ndarray, screen_v: float = 0.012,
                      screen_frac: float = 0.08) -> np.ndarray:
        p = np.asarray(p_kw, dtype=float)
        q = np.asarray(q_kvar, dtype=float)
        v_lin = (self.v_noload[:, None] - self.dv_dp @ np.clip(-p, 0.0, None)
                 + self.dv_dq @ np.clip(q, None, 0.0))
        cand = v_lin.min(axis=0) < self.v_min + screen_v
        pl, ql = self._phase_flows(p, q)
        sl = np.hypot(pl, ql) / self.line_s_max_phase_kw[None, :, None]
        cand |= ((sl > 1.0 - screen_frac) & (pl < 0)).any(axis=(0, 1))
        pt = self.phase_share @ p
        qt = self.phase_share @ q
        st = np.hypot(pt, qt) / self.trafo_s_max_phase_kw
        cand |= ((st > 1.0 - screen_frac) & (pt < 0)).any(axis=0)
        return np.flatnonzero(cand)

    def export_critical_hours(self, p_kw: np.ndarray, q_kvar: np.ndarray, vm_slack=None) -> np.ndarray:
        cand = self.screen_export(p_kw, q_kvar)
        if len(cand) == 0:
            return cand
        vs = vm_slack if vm_slack is None or np.isscalar(vm_slack) else np.asarray(vm_slack)[cand]
        res = self.solve(p_kw[:, cand], q_kvar[:, cand], vs)
        return cand[self.violations(res, p_kw[:, cand]).export_side]

    def import_critical_hours(self, p_kw: np.ndarray, q_kvar: np.ndarray, vm_slack=None) -> np.ndarray:
        cand = self.screen_import(p_kw, q_kvar)
        if len(cand) == 0:
            return cand
        vs = vm_slack if vm_slack is None or np.isscalar(vm_slack) else np.asarray(vm_slack)[cand]
        res = self.solve(p_kw[:, cand], q_kvar[:, cand], vs)
        return cand[self.violations(res, p_kw[:, cand]).import_side]

    # --------------------------------------------------------------------------- solving
    def solve(self, p_kw: np.ndarray, q_kvar: np.ndarray, vm_slack=None, flows: bool = True) -> PF3Result:
        vs = self.vm_slack if vm_slack is None else vm_slack
        return self.pf.solve_chunked(np.asarray(p_kw, dtype=float),
                                     np.asarray(q_kvar, dtype=float), self.phase, vs, flows=flows)

    def violations(self, res: PF3Result, p_kw: np.ndarray | None = None, tol_v: float = 1e-6,
                   tol_pct: float = 1e-4) -> Violations:
        vm = res.vm[self.check_rows]
        vmax, vmin = vm.max(axis=0), vm.min(axis=0)
        over_line = res.line_loading > self.thermal + tol_pct
        if p_kw is not None:
            toward_sub = (self.phi @ np.asarray(p_kw, dtype=float)) > 0
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
            vmax=vmax, vmin=vmin,
            max_line_loading=res.line_loading.max(axis=0),
            trafo_loading=res.trafo_loading,
        )

    def unbalance(self, res: PF3Result) -> Unbalance:
        """Unbalance factor and neutral current, which the balanced model cannot report."""
        worst = res.unbalance_pct[self.check_buses].max(axis=0)
        return Unbalance(worst_pct=worst,
                         neutral_a=res.i_neutral_ka.max(axis=0) * 1000.0,
                         over_limit=worst > self.unbalance_limit)


@lru_cache(maxsize=4)
def get_model3ph(rule: str | None = None, vm_slack: float | None = None,
                 tap_pos: int | None = None, trafo_type: str | None = None) -> NetModel3Ph:
    return NetModel3Ph(rule=rule, vm_slack=vm_slack, tap_pos=tap_pos, trafo_type=trafo_type)
