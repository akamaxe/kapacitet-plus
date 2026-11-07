"""Cross-validation of Kapacitet+'s power flow against OpenDSS (through opendssdirect.py).

OpenDSS is EPRI's open-source distribution simulator, written independently of pandapower. This
script builds the same network there from plain data (``tools/pf_crossval.py``'s ``case.json``:
buses, lines with their R/X/C, the transformer, the MV voltage, one load and one generator per
customer), solves the same hours and compares bus voltages, transformer and line loading with
Kapacitet+'s ``NetModel.solve``. It needs the ``validation`` extra (``pip install -e
".[validation]"``); docs/validation-opendss.md reports the run on the reference network.

    python tools/dss_crossval.py run [--out DIR] [--seed 7] [--others 8] [--pf PATH]

``run`` exports ``case.json`` into ``--out`` (the same hours as the PowerFactory check: the peak
export hour, the peak import hour and eight drawn with seed 7) unless it is already there, solves
it in OpenDSS -> ``opendss.json``, and writes ``comparison-opendss.json`` and a Markdown table. With
``--pf`` pointing at a ``powerfactory.json`` that PowerFactory wrote for the same ``case.json``,
the table puts the three solvers side by side.

The modelling choices that make OpenDSS solve the same equations as pandapower:

- 50 Hz (``net.f_hz``): OpenDSS defaults to 60 Hz, which would scale every cable's shunt
  susceptance by 1.2.
- A stiff source: the MV bus is held at the hour's voltage behind a 1e-6 ohm impedance, as
  pandapower's ``ext_grid`` holds it exactly.
- Positive and zero sequence set equal on every cable (r0 = r1, x0 = x1, c0 = c1): the flow is
  balanced, so only the positive sequence carries current, and nothing depends on a zero
  sequence pandapower does not model.
- Constant-power loads and generators across the whole voltage range: OpenDSS turns a load into
  a constant impedance below ``vminpu`` (0.95 by default) and above ``vmaxpu`` (1.05), and the
  peak hours sit at 0.89 and 1.06 pu, so both are widened to 0.5 and 1.5.
- The transformer's leakage resistance split equally between its windings, as pandapower's T
  model does; its no-load current split into iron losses (``%noloadloss``) and the remaining
  magnetising current (``%imag``).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import time
from pathlib import Path

TARGET_V_PU = 0.002        # stated targets, the same as the PowerFactory check
TARGET_LOADING_PCT = 1.0
SOURCE_OHM = 1e-6          # the MV source's impedance (ohm): stiff, as pandapower's ext_grid
V_BAND = (0.5, 1.5)        # constant power between these voltages (pu), loads and generators
DSS_TOLERANCE = 1e-10      # OpenDSS's convergence tolerance on the voltage update (pu)


def pf_crossval():
    """The sibling script, for the parts the two checks share (hour choice, export, compare)."""
    name = "pf_crossval"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("pf_crossval.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------------------ the circuit

def _f(x: float) -> str:
    return repr(float(x))


def trafo_command(tr: dict) -> str:
    """The OpenDSS ``Transformer`` definition of a pandapower two-winding transformer."""
    s_kva = tr["sn_mva"] * 1000.0
    if not str(tr["vector_group"]).upper().startswith("DYN"):    # the windings below are D/YN
        raise ValueError(f"only Dyn transformers are supported, not {tr['vector_group']!r}")
    vk, vkr = float(tr["vk_percent"]), float(tr["vkr_percent"])
    xhl = math.sqrt(max(vk ** 2 - vkr ** 2, 0.0))
    p0 = float(tr["pfe_kw"]) / s_kva * 100.0                     # iron losses, % of rating
    imag = math.sqrt(max(float(tr["i0_percent"]) ** 2 - p0 ** 2, 0.0))
    step = (float(tr["tap_pos"]) - float(tr["tap_neutral"])) * float(tr["tap_step_percent"]) / 100.0
    taps = (1.0 + step, 1.0) if tr["tap_side"] == "hv" else (1.0, 1.0 + step)
    return (f"new transformer.t1 phases=3 windings=2 buses=[b{tr['hv_bus']} b{tr['lv_bus']}] "
            f"conns=[delta wye] kvs=[{_f(tr['vn_hv_kv'])} {_f(tr['vn_lv_kv'])}] "
            f"kvas=[{_f(s_kva)} {_f(s_kva)}] taps=[{_f(taps[0])} {_f(taps[1])}] "
            f"xhl={_f(xhl)} %rs=[{_f(vkr / 2)} {_f(vkr / 2)}] %noloadloss={_f(p0)} %imag={_f(imag)}")


def circuit_commands(net: dict) -> list[str]:
    """The OpenDSS script for `net` (``network_data``'s plain dict), every injection at zero."""
    buses = {b["idx"]: b for b in net["buses"]}
    f_hz = float(net.get("f_hz", 50.0))
    eg = net["ext_grid"]
    kv_mv = buses[eg["bus"]]["vn_kv"]
    lo, hi = V_BAND
    cmds = ["clear", f"set defaultbasefrequency={_f(f_hz)}",
            f"new circuit.kapacitet basekv={_f(kv_mv)} pu={_f(eg['vm_pu'])} angle=0 phases=3 "
            f"bus1=b{eg['bus']} basefreq={_f(f_hz)} r1={_f(SOURCE_OHM)} x1={_f(SOURCE_OHM)} "
            f"r0={_f(SOURCE_OHM)} x0={_f(SOURCE_OHM)}",
            trafo_command(net["trafo"])]
    for ln in net["lines"]:
        if ln["g_us_per_km"]:
            raise ValueError(f"line {ln['idx']}: shunt conductance is not carried over to OpenDSS")
        n = ln["parallel"]
        r, x, c = ln["r_ohm_per_km"] / n, ln["x_ohm_per_km"] / n, ln["c_nf_per_km"] * n
        cmds.append(f"new line.l{ln['idx']} bus1=b{ln['from_bus']} bus2=b{ln['to_bus']} phases=3 units=km "
                    f"length={_f(ln['length_km'])} r1={_f(r)} x1={_f(x)} c1={_f(c)} "
                    f"r0={_f(r)} x0={_f(x)} c0={_f(c)}")
    for c in net["customers"]:
        kv = _f(buses[c["bus"]]["vn_kv"])
        cmds.append(f"new load.c{c['i']} bus1=b{c['bus']} phases=3 conn=wye kv={kv} kw=0 kvar=0 model=1 "
                    f"vminpu={lo} vmaxpu={hi} vlowpu=0.1")
        cmds.append(f"new generator.pv{c['i']} bus1=b{c['bus']} phases=3 conn=wye kv={kv} kw=0 kvar=0 "
                    f"model=1 vminpu={lo} vmaxpu={hi}")
    kvs = sorted({b["vn_kv"] for b in net["buses"]}, reverse=True)
    cmds += [f"set voltagebases=[{' '.join(_f(k) for k in kvs)}]", "calcvoltagebases",
             "set mode=snapshot", "set controlmode=off", "set loadmult=1",
             f"set tolerance={DSS_TOLERANCE}", "set maxiterations=200"]
    return cmds


def build(dss, net: dict) -> None:
    for cmd in circuit_commands(net):
        dss.Text.Command(cmd)
    # every bus at the base pandapower uses for it (OpenDSS keeps line-to-neutral kV)
    for b in net["buses"]:
        dss.Circuit.SetActiveBus(f"b{b['idx']}")
        base = dss.Bus.kVBase() * math.sqrt(3)
        if abs(base - b["vn_kv"]) > 1e-9:
            raise RuntimeError(f"bus {b['idx']}: OpenDSS base {base} kV, pandapower {b['vn_kv']} kV")


def _phase_mags(dss, terminal: int, phases: int = 3) -> list[float]:
    """Current magnitudes (A) of the phase conductors at one terminal of the active element."""
    n = dss.CktElement.NumConductors()
    mags = dss.CktElement.CurrentsMagAng()[0::2]
    return mags[terminal * n: terminal * n + phases]


def solve_hours(net: dict, load_kw, pv_kw, q_kvar, vm_pu=None) -> dict:
    """Solve each hour in OpenDSS; the result has the shape ``pf_crossval.compare`` reads.

    `load_kw`, `pv_kw` and `q_kvar` are per hour, a list over customers each (``q_kvar`` is the net
    injection, + into the grid, as in ``case.json``); `vm_pu` is the MV voltage per hour (default:
    the network's own)."""
    import opendssdirect as dss

    t0 = time.time()
    build(dss, net)
    vm_pu = vm_pu if vm_pu is not None else [net["ext_grid"]["vm_pu"]] * len(load_kw)
    tr = net["trafo"]
    i_rated_hv = tr["sn_mva"] * 1000.0 / (math.sqrt(3) * tr["vn_hv_kv"])     # A
    i_rated_lv = tr["sn_mva"] * 1000.0 / (math.sqrt(3) * tr["vn_lv_kv"])
    i_max = {ln["idx"]: ln["max_i_ka"] * 1000.0 * ln["parallel"] * ln["df"] for ln in net["lines"]}
    out = {"ierr": [], "vm_pu": [], "trafo_loading_pct": [], "line_loading_pct": [], "trafo_p_mw": [],
           "iterations": [], "phase_spread_pu": []}
    for h, vm in enumerate(vm_pu):
        dss.Vsources.Name("source")
        dss.Vsources.PU(float(vm))
        for c in net["customers"]:
            i = c["i"]
            dss.Loads.Name(f"c{i}")
            dss.Loads.kW(float(load_kw[h][i]))
            dss.Loads.kvar(float(-q_kvar[h][i]))
            dss.Generators.Name(f"pv{i}")
            dss.Generators.kW(float(pv_kw[h][i]))
            dss.Generators.kvar(0.0)
        dss.Solution.Solve()
        ok = bool(dss.Solution.Converged())
        out["ierr"].append(0 if ok else 1)
        out["iterations"].append(int(dss.Solution.Iterations()))
        if not ok:
            for k in ("vm_pu", "trafo_loading_pct", "line_loading_pct", "trafo_p_mw", "phase_spread_pu"):
                out[k].append(None)
            continue
        vm_h, spread = [], 0.0
        for b in net["buses"]:
            dss.Circuit.SetActiveBus(f"b{b['idx']}")
            ph = dss.Bus.puVmagAngle()[0::2][:3]
            vm_h.append(sum(ph) / 3.0)
            spread = max(spread, max(ph) - min(ph))
        dss.Circuit.SetActiveElement("transformer.t1")
        i_hv, i_lv = max(_phase_mags(dss, 0)), max(_phase_mags(dss, 1))
        n = dss.CktElement.NumConductors()
        p_hv_kw = sum(dss.CktElement.Powers()[0:2 * n:2])
        lines = []
        for ln in net["lines"]:
            dss.Circuit.SetActiveElement(f"line.l{ln['idx']}")
            lines.append(100.0 * max(_phase_mags(dss, 0) + _phase_mags(dss, 1)) / i_max[ln["idx"]])
        out["vm_pu"].append(vm_h)
        out["trafo_loading_pct"].append(100.0 * max(i_hv / i_rated_hv, i_lv / i_rated_lv))
        out["trafo_p_mw"].append(p_hv_kw / 1000.0)
        out["line_loading_pct"].append(lines)
        out["phase_spread_pu"].append(spread)
    import importlib.metadata as md
    out["about"] = {"opendssdirect.py": md.version("opendssdirect.py"), "dss-python": md.version("dss-python"),
                    "engine": dss.Basic.Version().splitlines()[0], "tolerance_pu": DSS_TOLERANCE,
                    "source_ohm": SOURCE_OHM, "v_band_pu": list(V_BAND),
                    "elements": {"buses": len(net["buses"]), "lines": len(net["lines"]),
                                 "loads": len(net["customers"]), "generators": len(net["customers"])},
                    "seconds": round(time.time() - t0, 2)}
    return out


def solve_case(case: dict) -> dict:
    """OpenDSS's answer for every hour of a ``case.json``."""
    hs = case["hours"]
    vm = [h.get("vm_pu", case["network"]["ext_grid"]["vm_pu"]) for h in hs]
    return solve_hours(case["network"], [h["load_kw"] for h in hs], [h["pv_kw"] for h in hs],
                       [h["q_kvar"] for h in hs], vm)


def without_magnetising(case: dict) -> dict:
    """The same hours with the transformer's no-load branch removed (iron losses and no-load
    current at zero) in both OpenDSS and pandapower's Newton-Raphson: what is left of the
    difference once the one element the two tools place differently is out of the picture."""
    import copy

    import numpy as np
    import pandapower as pp

    from kapacitet.grid.builder import load_net

    net_d = copy.deepcopy(case["network"])
    net_d["trafo"].update(pfe_kw=0.0, i0_percent=0.0)
    hs = case["hours"]
    vm = [h.get("vm_pu", net_d["ext_grid"]["vm_pu"]) for h in hs]
    d = solve_hours(net_d, [h["load_kw"] for h in hs], [h["pv_kw"] for h in hs], [h["q_kvar"] for h in hs], vm)
    net = load_net()
    net.trafo["pfe_kw"], net.trafo["i0_percent"] = 0.0, 0.0
    nr = {"vm_pu": [], "trafo_loading_pct": [], "line_loading_pct": []}
    for h, v in zip(hs, vm):
        net.load["p_mw"], net.load["q_mvar"] = np.array(h["load_kw"]) / 1000, -np.array(h["q_kvar"]) / 1000
        net.sgen["p_mw"], net.sgen["q_mvar"] = np.array(h["pv_kw"]) / 1000, 0.0
        net.ext_grid["vm_pu"] = v
        pp.runpp(net, calculate_voltage_angles=False, tolerance_mva=1e-11)
        nr["vm_pu"].append(net.res_bus.vm_pu.tolist())
        nr["trafo_loading_pct"].append(float(net.res_trafo.loading_percent.iloc[0]))
        nr["line_loading_pct"].append(net.res_line.loading_percent.tolist())
    return pf_crossval().compare({"hours": hs, "kapacitet": nr, "pandapower_nr": nr}, d)["kapacitet"]


# ------------------------------------------------------------------------------ comparison

def compare(case: dict, dss_out: dict, pf: dict | None = None) -> dict:
    """``pf_crossval.compare`` with OpenDSS in PowerFactory's place, plus, when PowerFactory's
    answers for the same case are given, OpenDSS against PowerFactory hour by hour."""
    pfx = pf_crossval()
    cmp = pfx.compare(case, dss_out)
    cmp["targets"] = {"v_pu": TARGET_V_PU, "trafo_loading_pct": TARGET_LOADING_PCT}
    cmp["max_phase_spread_pu"] = max((s for s in dss_out.get("phase_spread_pu", []) if s is not None),
                                     default=None)
    if pf is not None:
        if len(pf["ierr"]) != len(case["hours"]):
            raise ValueError("powerfactory.json was not written for this case.json (different hours)")
        both = [i for i in range(len(case["hours"])) if dss_out["ierr"][i] == 0 and pf["ierr"][i] == 0]
        s = pfx._stats
        cmp["powerfactory"] = {
            "hours_compared": len(both),
            "v_pu": s([(a, b) for i in both for a, b in zip(dss_out["vm_pu"][i], pf["vm_pu"][i])]),
            "trafo_loading_pct": s([(dss_out["trafo_loading_pct"][i], pf["trafo_loading_pct"][i]) for i in both]),
            "line_loading_pct": s([(a, b) for i in both
                                   for a, b in zip(dss_out["line_loading_pct"][i], pf["line_loading_pct"][i])]),
            "trafo_p_kw": s([(dss_out["trafo_p_mw"][i] * 1000, pf["trafo_p_mw"][i] * 1000) for i in both]),
            "vs_kapacitet": pfx.compare(case, pf)["kapacitet"],
        }
    return cmp


def markdown(cmp: dict) -> str:
    pfx = pf_crossval()
    md = pfx.markdown(cmp, solver="OpenDSS")
    pf = cmp.get("powerfactory")
    if pf:
        e = pfx._e
        rows = ["", "Three solvers side by side (max abs diff over the hours both solved):", "",
                "| Quantity | OpenDSS vs Kapacitet+ | PowerFactory vs Kapacitet+ | OpenDSS vs PowerFactory |",
                "|---|---:|---:|---:|"]
        for key, qty in (("v_pu", "Bus voltage (pu)"), ("trafo_loading_pct", "Transformer loading (%)"),
                         ("line_loading_pct", "Line loading (%)"), ("trafo_p_kw", "Transformer inflow (kW)")):
            k = cmp["kapacitet"].get(key, {}).get("max")
            p = pf["vs_kapacitet"].get(key, {}).get("max")
            rows.append(f"| {qty} | {e(k)} | {e(p)} | {e(pf[key]['max'])} |")
        md += "\n" + "\n".join(rows)
    return md


def _default_out() -> Path:
    from kapacitet.paths import processed
    return processed("validation", "opendss")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="export case.json if missing, solve it in OpenDSS and compare")
    r.add_argument("--out", type=Path)
    r.add_argument("--seed", type=int, default=7)
    r.add_argument("--others", type=int, default=8)
    r.add_argument("--pf", type=Path, help="PowerFactory's answers for the same case.json")
    a = ap.parse_args(argv)
    out = a.out or _default_out()
    if not (out / "case.json").exists():
        pf_crossval().export(out, a.seed, a.others)
    case = json.loads((out / "case.json").read_text(encoding="utf-8"))
    dss_out = solve_case(case)
    (out / "opendss.json").write_text(json.dumps(dss_out), encoding="utf-8")
    pf = json.loads(a.pf.read_text(encoding="utf-8")) if a.pf else None
    cmp = compare(case, dss_out, pf)
    cmp["without_magnetising_vs_pandapower_nr"] = without_magnetising(case)
    cmp["versions"] = {**case["versions"], "opendss": dss_out["about"],
                       **({"powerfactory": pf.get("about")} if pf else {})}
    (out / "comparison-opendss.json").write_text(json.dumps(cmp, indent=1), encoding="utf-8")
    print(markdown(cmp))
    print(json.dumps({k: cmp[k] for k in ("hours_solved", "meets_targets", "kapacitet", "pandapower_nr",
                                          "max_phase_spread_pu", "without_magnetising_vs_pandapower_nr")},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
