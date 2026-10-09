"""Batched three-phase Z-bus power flow on pandapower's own sequence model.

The balanced solver in `powerflow.py` carries one voltage per bus, so single-phase PV,
single-phase chargers and the neutral-point shift they cause are invisible to it. This
module runs the same fixed point in the phase frame,

    V = w + Z · conj(S / V),     Z = inv(Y_nn),   w = -Z · Y_ns · V_slack,

with three rows per bus (a, b, c) and one injection per phase, and stays vectorised over
thousands of operating points at once.

The admittance is not rebuilt from cable data. It is taken from pandapower's own positive,
negative and zero sequence networks and converted to the phase frame with the symmetrical
component transformation, so the network model is exactly the one `pp.runpp_3ph` solves:

    Y_abc = Σ_k  kron(Y_k, A[:, k] ⊗ A⁻¹[k, :]),        k = 0, 1, 2

with A the symmetrical-component matrix. The neutral is already eliminated inside the zero
sequence impedance of the four-wire cables, so the state is 3n and not 4n.

Memory: Z is dense and 3n × 3n complex. That is 4 MB for the 165-bus test grid and about
120 MB for the 907-bus IEEE European LV feeder, which is the practical ceiling here.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandapower as pp
from pandapower.pd2ppc import _pd2ppc
from pandapower.pd2ppc_zero import _pd2ppc_zero
from pandapower.pypower.idx_brch import F_BUS, T_BUS
from pandapower.pypower.idx_bus import BUS_TYPE, REF
from pandapower.pypower.makeYbus import makeYbus

PHASES = ("a", "b", "c")
A_OP = np.exp(2j * np.pi / 3)                      # 120° rotation
A_MAT = np.array([[1, 1, 1],
                  [1, A_OP ** 2, A_OP],
                  [1, A_OP, A_OP ** 2]])           # phase = A @ sequence
A_INV = np.linalg.inv(A_MAT)


@dataclass
class PF3Result:
    """Per-phase results. Bus rows are (bus, phase) pairs in pandapower bus order."""

    v: np.ndarray                 # complex, (3 · n_bus, H)
    iterations: int
    max_mismatch: float
    i_line_ka: np.ndarray = None        # (3 · n_line, H), magnitude, worst end
    line_loading: np.ndarray = None     # (n_line, H), percent, worst phase
    i_neutral_ka: np.ndarray = None     # (n_line, H), return current in the neutral
    trafo_p_mw: np.ndarray = None       # (H,), active power MV to LV (negative = reverse)

    # Kirchhoff at the LV busbar: the phasor sum of what leaves on the feeders. Reads lower
    # than pandapower's res_trafo_3ph under unbalance because that one adds the feeder
    # current *magnitudes* instead of the phasors, which is only the same thing when every
    # feeder is in phase. Both agree on a balanced case for exactly that reason. The test
    # suite reproduces pandapower's number from our own currents to show where it comes from.
    trafo_loading: np.ndarray = None    # (H,), percent, worst phase

    @property
    def vm(self) -> np.ndarray:
        return np.abs(self.v)

    def phase(self, p: str) -> np.ndarray:
        """Voltage magnitudes of one phase, shape (n_bus, H)."""
        return np.abs(self.v[PHASES.index(p)::3])

    @property
    def unbalance_pct(self) -> np.ndarray:
        """Voltage unbalance factor per bus, percent, shape (n_bus, H).

        Ratio of the negative to the positive sequence magnitude, which is the definition
        EN 50160 uses for the unbalance limit.
        """
        v012 = np.einsum("ij,jkh->ikh", A_INV, self.v.reshape(-1, 3, self.v.shape[1]).transpose(1, 0, 2))
        return 100.0 * np.abs(v012[2]) / np.maximum(np.abs(v012[1]), 1e-12)


def sequence_ybus(net: pp.pandapowerNet) -> tuple[dict, dict, dict, dict]:
    """Bus and branch admittances of the three sequence networks, plus the positive ppc.

    `makeYbus` must be fed the *internal* ppc: the external one keeps out-of-service and
    isolated branches whose zero-sequence impedance is zero, which turns into NaN. The
    positive sequence is built last so `net._pd2ppc_lookups` is left describing it.
    """
    out: dict[int, dict] = {}
    ppci1 = None
    for seq in (1, 2, 0):                      # positive first, so its ppci is the reference
        _, ppci = _pd2ppc_zero(net, None) if seq == 0 else _pd2ppc(net, seq)
        y, yf, yt = makeYbus(ppci["baseMVA"], ppci["bus"], ppci["branch"])
        mats = {k: np.asarray(m.todense()) for k, m in (("y", y), ("yf", yf), ("yt", yt))}
        if any(not np.isfinite(m).all() for m in mats.values()):
            raise ValueError(f"sequence {seq} admittance has non-finite entries")
        mats["branch"] = ppci["branch"]
        out[seq] = mats
        if seq == 1:
            ppci1 = ppci

    # A Dyn transformer has no zero-sequence series branch: the delta keeps that current
    # circulating, so pandapower drops the branch and leaves a shunt on the LV bus instead.
    # Align the branch rows on the positive sequence, with zeros where a branch is missing.
    n_ref = out[1]["yf"].shape[0]
    for seq in (0, 2):
        n = out[seq]["yf"].shape[0]
        if n == n_ref:
            continue
        ref_ends = out[1]["branch"][:n, [F_BUS, T_BUS]].real
        if not np.array_equal(out[seq]["branch"][:, [F_BUS, T_BUS]].real, ref_ends):
            raise ValueError(f"sequence {seq} branches are not a prefix of the positive sequence")
        for key in ("yf", "yt"):
            pad = np.zeros((n_ref - n, out[seq][key].shape[1]), dtype=complex)
            out[seq][key] = np.vstack([out[seq][key], pad])
    return out[0], out[1], out[2], ppci1


def phase_frame(y0: np.ndarray, y1: np.ndarray, y2: np.ndarray) -> np.ndarray:
    """Block-wise symmetrical-component transform of three sequence matrices.

    Works for the square bus admittance and for the rectangular branch matrices alike:
    both map sequence voltages to sequence currents, so I_abc = A·Y_k·A⁻¹·V_abc. Indices
    are element-major, so row 3·i + p is phase p of element i.
    """
    rows, cols = y0.shape
    out = np.zeros((3 * rows, 3 * cols), dtype=complex)
    for k, yk in enumerate((y0, y1, y2)):
        out += np.kron(yk, np.outer(A_MAT[:, k], A_INV[k, :]))
    return out


class PowerFlow3Ph:
    """Precomputed phase-frame Z-bus model of a pandapower network with one ext_grid."""

    def __init__(self, net: pp.pandapowerNet, customer_bus: np.ndarray):
        pp.runpp_3ph(net)                       # populates the sequence options and lookups
        s0, s1, s2, _ = sequence_ybus(net)
        y_abc = phase_frame(s0["y"], s1["y"], s2["y"])
        self.Yf = phase_frame(s0["yf"], s1["yf"], s2["yf"])
        self.Yt = phase_frame(s0["yt"], s1["yt"], s2["yt"])

        ppc = net["_ppc1"]
        ref_bus = np.flatnonzero(ppc["bus"][:, BUS_TYPE] == REF)
        if len(ref_bus) != 1:
            raise ValueError("phase-frame Z-bus expects exactly one slack bus")
        self.ref_bus = int(ref_bus[0])
        n = len(s1["y"])
        self.n_bus = n

        ref_rows = 3 * self.ref_bus + np.arange(3)
        self.ns = np.setdiff1d(np.arange(3 * n), ref_rows)
        self.ref_rows = ref_rows
        self.Z = np.linalg.inv(y_abc[np.ix_(self.ns, self.ns)])
        self.Yns = y_abc[np.ix_(self.ns, ref_rows)]

        lookup = net._pd2ppc_lookups["bus"]
        self.bus_ppc = np.asarray(lookup[net.bus.index.to_numpy()], dtype=int)
        pos = -np.ones(3 * n, dtype=int)
        pos[self.ns] = np.arange(len(self.ns))
        self.pos_in_ns = pos
        self.base_mva = float(ppc["baseMVA"])

        self.customer_bus = np.asarray(customer_bus, dtype=int)
        self.customer_ppc = lookup[self.customer_bus]
        if (self.customer_ppc == self.ref_bus).any():
            raise ValueError("a customer is connected to the slack bus")

        # Branch bookkeeping, for per-phase currents and loadings.
        br = net._pd2ppc_lookups["branch"]
        l0, l1 = br["line"]
        self.line_rows = np.arange(l0, l1)
        self.trafo_row = int(br["trafo"][0])
        vn_kv = ppc["bus"][:, 9]                                   # BASE_KV
        fb = ppc["branch"][:, F_BUS].real.astype(int)
        tb = ppc["branch"][:, T_BUS].real.astype(int)
        # Injections are fed in per unit of the three-phase base while the voltages are
        # phase-to-neutral, so the matching current base is S / V_LN = sqrt(3) · S / V_LL.
        self.i_base_from = np.sqrt(3) * self.base_mva / vn_kv[fb]
        self.i_base_to = np.sqrt(3) * self.base_mva / vn_kv[tb]
        line = net.line
        self.line_imax_ka = (line["max_i_ka"] * line["parallel"] * line["df"]).to_numpy()
        tr = net.trafo.iloc[0]
        self.trafo_sn_mva = float(tr["sn_mva"])
        self.trafo_i_lv_rated = self.trafo_sn_mva / (np.sqrt(3) * float(tr["vn_lv_kv"]))
        self.trafo_from_bus = int(ppc["branch"][self.trafo_row, F_BUS].real)

        # The transformer's own branch has no zero-sequence row (the delta blocks it), so its
        # current is taken from Kirchhoff at the LV busbar instead: everything that leaves the
        # busbar came through the transformer. Exact, and it needs no zero-sequence branch.
        lv_bus = int(ppc["branch"][self.trafo_row, T_BUS].real)
        self.net_lv_bus = lv_bus
        self.lv_from = self.line_rows[fb[self.line_rows] == lv_bus]
        self.lv_to = self.line_rows[tb[self.line_rows] == lv_bus]
        if len(self.lv_from) + len(self.lv_to) == 0:
            raise ValueError("no feeder leaves the LV busbar")

    # ------------------------------------------------------------------------------ solving
    def _rows_for(self, which: np.ndarray, phase_idx: np.ndarray) -> np.ndarray:
        """Row of the non-slack system for the selected customers on the given phase."""
        rows = 3 * self.customer_ppc[which] + np.asarray(phase_idx, dtype=int)
        return self.pos_in_ns[rows]

    def solve(self, p_kw: np.ndarray, q_kvar: np.ndarray | None, phase_idx: np.ndarray,
              vm_slack: float | np.ndarray = 1.0, tol: float = 1e-10,
              max_iter: int = 80, flows: bool = True) -> PF3Result:
        """Solve H operating points.

        p_kw, q_kvar: (n_customers, H), positive = injection into the grid.
        phase_idx: (n_customers,) with 0, 1 or 2 for a single-phase connection, or -1 for a
        balanced three-phase connection, whose power is split equally over the three phases.
        """
        p_kw = np.atleast_2d(np.asarray(p_kw, dtype=float))
        q_kvar = np.zeros_like(p_kw) if q_kvar is None else np.asarray(q_kvar, dtype=float)
        phase_idx = np.asarray(phase_idx, dtype=int)
        H = p_kw.shape[1]

        s = np.zeros((len(self.ns), H), dtype=complex)
        single = phase_idx >= 0
        if single.any():
            rows = self._rows_for(single, phase_idx[single])
            np.add.at(s, rows, (p_kw[single] + 1j * q_kvar[single]) / (1000.0 * self.base_mva))
        if (~single).any():
            third = (p_kw[~single] + 1j * q_kvar[~single]) / (3000.0 * self.base_mva)
            for ph in range(3):
                rows = self.pos_in_ns[3 * self.customer_ppc[~single] + ph]
                np.add.at(s, rows, third)

        vs = np.broadcast_to(np.asarray(vm_slack, dtype=float), (H,))
        v_ref = np.outer(np.array([1.0, A_OP ** 2, A_OP]), vs)     # (3, H) balanced source
        w = -(self.Z @ (self.Yns @ v_ref))
        v = w.copy()
        it, err = 0, np.inf
        for it in range(1, max_iter + 1):
            v_new = w + self.Z @ np.conj(s / v)
            err = float(np.max(np.abs(v_new - v)))
            v = v_new
            if err < tol:
                break

        v_all = np.empty((3 * self.n_bus, H), dtype=complex)
        v_all[self.ns] = v
        v_all[self.ref_rows] = v_ref
        rows = (3 * self.bus_ppc[:, None] + np.arange(3)).ravel()
        res = PF3Result(v=v_all[rows], iterations=it, max_mismatch=err)
        if flows:
            self._add_flows(res, v_all)
        return res

    def _add_flows(self, res: PF3Result, v_all: np.ndarray) -> None:
        """Per-phase branch currents, loadings and the neutral return current."""
        i_f = self.Yf @ v_all
        i_t = self.Yt @ v_all
        rows = (3 * self.line_rows[:, None] + np.arange(3)).ravel()
        base_f = np.repeat(self.i_base_from[self.line_rows], 3)[:, None]
        base_t = np.repeat(self.i_base_to[self.line_rows], 3)[:, None]
        il = np.maximum(np.abs(i_f[rows]) * base_f, np.abs(i_t[rows]) * base_t)
        res.i_line_ka = il
        per_phase = il.reshape(len(self.line_rows), 3, -1)
        res.line_loading = 100.0 * per_phase.max(axis=1) / self.line_imax_ka[:, None]
        # The neutral carries the unbalance: I_n = -(Ia + Ib + Ic), zero on a balanced feeder.
        i_f3 = i_f[rows].reshape(len(self.line_rows), 3, -1)
        res.i_neutral_ka = np.abs(i_f3.sum(axis=1)) * self.i_base_from[self.line_rows, None]

        def _phase_rows(branches):
            return (3 * np.asarray(branches)[:, None] + np.arange(3)).ravel()

        leaving = np.zeros((3, v_all.shape[1]), dtype=complex)
        if len(self.lv_from):
            leaving += i_f[_phase_rows(self.lv_from)].reshape(-1, 3, v_all.shape[1]).sum(axis=0)
        if len(self.lv_to):
            leaving += i_t[_phase_rows(self.lv_to)].reshape(-1, 3, v_all.shape[1]).sum(axis=0)
        i_lv = np.abs(leaving) * self.i_base_to[self.trafo_row]
        res.trafo_loading = 100.0 * (i_lv / self.trafo_i_lv_rated).max(axis=0)
        # Active power through the transformer, positive from the MV side into the LV network.
        v_lv = v_all[3 * int(self.net_lv_bus) + np.arange(3)]
        res.trafo_p_mw = (v_lv * np.conj(leaving)).real.sum(axis=0) * self.base_mva


    def solve_chunked(self, p_kw: np.ndarray, q_kvar: np.ndarray | None, phase_idx: np.ndarray,
                      vm_slack: float | np.ndarray = 1.0, chunk: int = 2048,
                      flows: bool = True) -> PF3Result:
        """Same as solve() but in column chunks, because a year of three-phase state is large."""
        p_kw = np.atleast_2d(np.asarray(p_kw, dtype=float))
        H = p_kw.shape[1]
        if H <= chunk:
            return self.solve(p_kw, q_kvar, phase_idx, vm_slack, flows=flows)
        q_kvar = np.zeros_like(p_kw) if q_kvar is None else np.asarray(q_kvar, dtype=float)
        vs = np.broadcast_to(np.asarray(vm_slack, dtype=float), (H,))
        parts = [self.solve(p_kw[:, a:a + chunk], q_kvar[:, a:a + chunk], phase_idx,
                            vs[a:a + chunk], flows=flows)
                 for a in range(0, H, chunk)]
        cat = lambda name, axis: np.concatenate([getattr(r, name) for r in parts], axis=axis)
        return PF3Result(
            v=cat("v", 1),
            iterations=max(r.iterations for r in parts),
            max_mismatch=max(r.max_mismatch for r in parts),
            i_line_ka=cat("i_line_ka", 1) if flows else None,
            line_loading=cat("line_loading", 1) if flows else None,
            i_neutral_ka=cat("i_neutral_ka", 1) if flows else None,
            trafo_loading=cat("trafo_loading", 0) if flows else None,
            trafo_p_mw=cat("trafo_p_mw", 0) if flows else None,
        )


def build_model(net: pp.pandapowerNet, customer_bus: np.ndarray) -> PowerFlow3Ph:
    return PowerFlow3Ph(net, np.asarray(customer_bus))

