"""Batched Z-bus power flow on pandapower's exact network model.

For a network with one slack bus, the non-slack bus voltages satisfy

    V = w + Z · conj(S / V),     Z = inv(Y_nn),   w = -Z · Y_ns · V_slack,

where S are the per-unit complex power injections. The fixed point converges in a few
iterations for distribution grids and is vectorised over thousands of operating points
(hours or candidate cases) at once. Y comes from pandapower's own model (cables with shunt
capacitance, transformer with magnetising branch and tap), so results match `pp.runpp`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandapower as pp
import pandas as pd
from pandapower.pypower.idx_brch import F_BUS, T_BUS
from pandapower.pypower.idx_bus import BUS_TYPE, REF
from pandapower.pypower.makeYbus import makeYbus


@dataclass
class PFResult:
    v: np.ndarray               # complex voltages, pu, shape (n_bus, H), rows in pandapower bus order
    i_line_ka: np.ndarray       # line current magnitude (max of both ends), kA, shape (n_line, H)
    line_loading: np.ndarray    # percent, shape (n_line, H)
    trafo_loading: np.ndarray   # percent, shape (H,)
    trafo_p_mw: np.ndarray      # active power from MV to LV side (negative = reverse flow), shape (H,)
    iterations: int
    max_mismatch: float

    @property
    def vm(self) -> np.ndarray:
        return np.abs(self.v)


class PowerFlowModel:
    """Precomputed Z-bus model of a pandapower radial network with one ext_grid."""

    def __init__(self, net: pp.pandapowerNet, customer_bus: np.ndarray):
        self.net = net
        net.load["p_mw"] = 0.0
        net.load["q_mvar"] = 0.0
        net.sgen["p_mw"] = 0.0
        net.sgen["q_mvar"] = 0.0
        pp.runpp(net, calculate_voltage_angles=False, init="flat", tolerance_mva=1e-10)
        ppc = net._ppc
        self.base_mva = float(ppc["baseMVA"])
        Ybus, Yf, Yt = makeYbus(ppc["baseMVA"], ppc["bus"], ppc["branch"])
        n = Ybus.shape[0]
        ref = np.flatnonzero(ppc["bus"][:, BUS_TYPE] == REF)
        if len(ref) != 1:
            raise ValueError("Z-bus model expects exactly one slack bus")
        self.ref = int(ref[0])
        self.ns = np.setdiff1d(np.arange(n), ref)
        Y = Ybus.tocsc()
        Ynn = Y[self.ns][:, self.ns].toarray()
        Yns = Y[self.ns][:, [self.ref]].toarray()[:, 0]
        self.Z = np.linalg.inv(Ynn)
        self.w0 = -(self.Z @ Yns)  # for V_slack = 1∠0
        self.Yf, self.Yt = Yf.tocsr(), Yt.tocsr()

        lookup = net._pd2ppc_lookups["bus"]
        self.bus_ppc = np.asarray(lookup[net.bus.index.to_numpy()], dtype=int)
        pos_in_ns = -np.ones(n, dtype=int)
        pos_in_ns[self.ns] = np.arange(len(self.ns))
        self.pos_in_ns = pos_in_ns
        self.customer_bus = np.asarray(customer_bus, dtype=int)            # pandapower bus index per customer
        self.customer_row = pos_in_ns[lookup[self.customer_bus]]             # row in the non-slack system
        if (self.customer_row < 0).any():
            raise ValueError("a customer is connected to the slack bus")

        br = net._pd2ppc_lookups["branch"]
        l0, l1 = br["line"]
        t0, t1 = br["trafo"]
        self.line_rows = np.arange(l0, l1)
        self.trafo_row = t0
        vn_kv = ppc["bus"][:, 9]  # BASE_KV
        fb = ppc["branch"][:, F_BUS].real.astype(int)
        tb = ppc["branch"][:, T_BUS].real.astype(int)
        self.i_base_from = self.base_mva / (np.sqrt(3) * vn_kv[fb])  # kA per pu
        self.i_base_to = self.base_mva / (np.sqrt(3) * vn_kv[tb])
        line = net.line
        self.line_imax_ka = (line["max_i_ka"] * line["parallel"] * line["df"]).to_numpy()
        tr = net.trafo.iloc[0]
        self.trafo_sn_mva = float(tr["sn_mva"])
        self.trafo_i_lv_rated = self.trafo_sn_mva / (np.sqrt(3) * float(tr["vn_lv_kv"]))
        self.trafo_i_hv_rated = self.trafo_sn_mva / (np.sqrt(3) * float(tr["vn_hv_kv"]))
        self.n_bus_pp = len(net.bus)

        # Linear sensitivities of voltage magnitude to active/reactive injection at each
        # customer (pu per kW / kvar), valid around V ≈ 1 (used by DOE with a correction loop).
        zc = self.Z[:, self.customer_row]
        self.dv_dp = zc.real / (1000.0 * self.base_mva)
        self.dv_dq = zc.imag / (1000.0 * self.base_mva)

    # ------------------------------------------------------------------------------ solving
    def _bus_injection_pu(self, p_kw: np.ndarray, q_kvar: np.ndarray) -> np.ndarray:
        """Sum customer injections (kW, positive = generation) into non-slack bus rows (pu)."""
        H = p_kw.shape[1]
        s = np.zeros((len(self.ns), H), dtype=complex)
        np.add.at(s, self.customer_row, (p_kw + 1j * q_kvar) / (1000.0 * self.base_mva))
        return s

    def solve(self, p_kw: np.ndarray, q_kvar: np.ndarray | None = None, vm_slack: float | np.ndarray = 1.0,
              tol: float = 1e-10, max_iter: int = 60, flows: bool = True) -> PFResult:
        """Solve H operating points. p_kw, q_kvar: (n_customers, H), positive = injection."""
        p_kw = np.atleast_2d(np.asarray(p_kw, dtype=float))
        if q_kvar is None:
            q_kvar = np.zeros_like(p_kw)
        s = self._bus_injection_pu(p_kw, np.asarray(q_kvar, dtype=float))
        vs = np.broadcast_to(np.asarray(vm_slack, dtype=float), (p_kw.shape[1],))
        w = np.outer(self.w0, vs)
        v = w.copy()
        it, err = 0, np.inf
        for it in range(1, max_iter + 1):
            v_new = w + self.Z @ np.conj(s / v)
            err = float(np.max(np.abs(v_new - v)))
            v = v_new
            if err < tol:
                break
        v_all = np.empty((len(self.ns) + 1, v.shape[1]), dtype=complex)
        v_all[self.ns] = v
        v_all[self.ref] = vs
        v_pp = v_all[self.bus_ppc]
        if not flows:
            return PFResult(v=v_pp, i_line_ka=np.empty((0, 0)), line_loading=np.empty((0, 0)),
                            trafo_loading=np.empty(0), trafo_p_mw=np.empty(0), iterations=it, max_mismatch=err)
        i_f = self.Yf @ v_all
        i_t = self.Yt @ v_all
        il = np.maximum(np.abs(i_f[self.line_rows]) * self.i_base_from[self.line_rows, None],
                        np.abs(i_t[self.line_rows]) * self.i_base_to[self.line_rows, None])
        loading = 100.0 * il / self.line_imax_ka[:, None]
        k = self.trafo_row
        i_hv = np.abs(i_f[k]) * self.i_base_from[k]
        i_lv = np.abs(i_t[k]) * self.i_base_to[k]
        tr_loading = 100.0 * np.maximum(i_hv / self.trafo_i_hv_rated, i_lv / self.trafo_i_lv_rated)
        fb = int(self.net._ppc["branch"][k, F_BUS].real)
        p_hv = (v_all[fb] * np.conj(i_f[k])).real * self.base_mva
        return PFResult(v=v_pp, i_line_ka=il, line_loading=loading, trafo_loading=tr_loading, trafo_p_mw=p_hv,
                        iterations=it, max_mismatch=err)

    def solve_chunked(self, p_kw: np.ndarray, q_kvar: np.ndarray, vm_slack=1.0, chunk: int = 4096,
                      flows: bool = True) -> PFResult:
        """Same as solve() but in column chunks to bound memory."""
        H = p_kw.shape[1]
        vs = np.broadcast_to(np.asarray(vm_slack, dtype=float), (H,))
        parts = [self.solve(p_kw[:, a:a + chunk], q_kvar[:, a:a + chunk], vs[a:a + chunk], flows=flows)
                 for a in range(0, H, chunk)]
        if len(parts) == 1:
            return parts[0]
        cat = (lambda name, axis: np.concatenate([getattr(r, name) for r in parts], axis=axis))
        return PFResult(
            v=cat("v", 1),
            i_line_ka=cat("i_line_ka", 1) if flows else np.empty((0, 0)),
            line_loading=cat("line_loading", 1) if flows else np.empty((0, 0)),
            trafo_loading=cat("trafo_loading", 0) if flows else np.empty(0),
            trafo_p_mw=cat("trafo_p_mw", 0) if flows else np.empty(0),
            iterations=max(r.iterations for r in parts),
            max_mismatch=max(r.max_mismatch for r in parts),
        )


def build_model(net: pp.pandapowerNet, customers: pd.DataFrame) -> PowerFlowModel:
    return PowerFlowModel(net, customers["bus"].to_numpy())
