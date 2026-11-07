"""Cross-validation of Kapacitet+'s power flow against DIgSILENT PowerFactory.

Three steps, two interpreters. The first and the last run in the project's environment
(pandapower and Kapacitet+); the middle one runs inside PowerFactory's Python API, which has
neither, so that half of the file uses only the standard library and imports nothing at the top
that PowerFactory's interpreter lacks.

1. ``export`` (project environment): the reference LV network as plain data (buses, lines with
   R/X/C and lengths, the 10/0.4 kV transformer with its impedance and tap, the MV voltage, one
   load and one static generator per customer), the hours to compare, their injections, and
   Kapacitet+'s answer for them (``NetModel.solve``, plus pandapower's Newton-Raphson as a
   second reference) -> ``case.json``.

       python tools/pf_crossval.py export [--out DIR] [--seed 7] [--others 8]

2. ``run_in_powerfactory`` (PowerFactory): builds the network in a new project, applies each
   hour's injections, runs the balanced load flow and writes bus voltages, transformer and line
   loading -> ``powerfactory.json``. With the ``powerfactory`` MCP server, in parallel mode
   (``/inMemory``) so that the user's database is never changed::

       import runpy
       ns = runpy.run_path(r"<repo>\\tools\\pf_crossval.py", run_name="pf_crossval")
       result = ns["run_in_powerfactory"](app, r"<DIR>\\case.json", r"<DIR>\\powerfactory.json")

   It tightens PowerFactory's convergence tolerance to 1e-5 kVA at every voltage level;
   ``tol_kva=None`` keeps PowerFactory's own (written, for instance, to
   ``powerfactory-defaults.json``, which ``compare --pf powerfactory-defaults.json`` reads).

3.``compare`` (any interpreter): maximum and mean absolute differences -> ``comparison.json``
   and a Markdown table on stdout (docs/validation-powerfactory.md quotes it).

       python tools/pf_crossval.py compare [--out DIR] [--pf powerfactory.json]

The project PowerFactory builds lives only in that session's memory: nothing here writes a
PowerFactory project file.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

TARGET_V_PU = 0.002        # stated targets, docs/validation-powerfactory.md
TARGET_LOADING_PCT = 1.0
PROJECT = "Kapacitet crossval"
GRID = "Smedslatten LV"


# ------------------------------------------------------------------------- 1. export (project env)

def pick_hours(total_kw, seed: int, others: int) -> list[dict]:
    """The peak export hour, the peak import hour and `others` more drawn with a fixed seed
    (without replacement, never repeating the two peaks), in time order after the peaks."""
    import numpy as np

    total = np.asarray(total_kw, dtype=float)
    h_exp, h_imp = int(np.argmax(total)), int(np.argmin(total))
    rest = np.setdiff1d(np.arange(len(total)), [h_exp, h_imp])
    drawn = np.sort(np.random.default_rng(seed).choice(rest, size=others, replace=False))
    hours = [{"hour": h_exp, "why": "peak export"}, {"hour": h_imp, "why": "peak import"}]
    hours += [{"hour": int(h), "why": f"drawn (seed {seed})"} for h in drawn]
    return hours


def network_data(m) -> dict:
    """The NetModel's pandapower network as plain data, in pandapower order."""
    net = m.net
    tr = net.trafo.iloc[0]
    eg = net.ext_grid.iloc[0]
    buses = [{"idx": int(i), "name": str(r["name"]), "vn_kv": float(r["vn_kv"])} for i, r in net.bus.iterrows()]
    lines = [{"idx": int(i), "name": str(r["name"]), "std_type": str(r["std_type"]),
              "from_bus": int(r["from_bus"]), "to_bus": int(r["to_bus"]), "length_km": float(r["length_km"]),
              "r_ohm_per_km": float(r["r_ohm_per_km"]), "x_ohm_per_km": float(r["x_ohm_per_km"]),
              "c_nf_per_km": float(r["c_nf_per_km"]), "g_us_per_km": float(r["g_us_per_km"]),
              "max_i_ka": float(r["max_i_ka"]), "parallel": int(r["parallel"]), "df": float(r["df"])}
             for i, r in net.line.iterrows()]
    trafo = {k: (float(tr[k]) if isinstance(tr[k], (int, float)) or hasattr(tr[k], "dtype") else str(tr[k]))
             for k in ("sn_mva", "vn_hv_kv", "vn_lv_kv", "vk_percent", "vkr_percent", "pfe_kw", "i0_percent",
                       "shift_degree", "tap_side", "tap_neutral", "tap_min", "tap_max", "tap_step_percent",
                       "tap_pos", "vector_group", "std_type")}
    trafo.update(hv_bus=int(tr["hv_bus"]), lv_bus=int(tr["lv_bus"]))
    return {"buses": buses, "lines": lines, "trafo": trafo, "f_hz": float(net.f_hz),
            "ext_grid": {"bus": int(eg["bus"]), "vm_pu": float(m.vm_slack)},
            "customers": [{"i": i, "bus": int(b)} for i, b in enumerate(m.cust["bus"].to_numpy())]}


def export(out: Path, seed: int = 7, others: int = 8) -> Path:
    import importlib.metadata as md

    import numpy as np
    import pandapower as pp

    from kapacitet.config import settings
    from kapacitet.engine.netmodel import get_model
    from kapacitet.engine.scenario import Scenario
    from kapacitet.grid.builder import load_net
    from kapacitet.timeindex import year_index

    cfg = settings()
    m = get_model()
    C = m.C
    every = np.ones(C, dtype=bool)
    sc = Scenario(year=cfg.reference_year, pv=every, hp=every, ev=every)
    arr = sc.arrays()
    hours = pick_hours(arr["p_net"].sum(axis=0), seed, others)
    cols = [h["hour"] for h in hours]
    load, pv, q = (arr[k][:, cols].astype(float) for k in ("load", "pv", "q"))
    p = pv - load

    res = m.solve(p, q, strict=True)             # Kapacitet+: Z-bus fixed point on pandapower's model
    # pandapower's Newton-Raphson on the same network, as a second reference
    net = load_net()
    nr = {"vm_pu": [], "trafo_loading_pct": [], "line_loading_pct": []}
    for h in range(len(cols)):
        net.load["p_mw"], net.load["q_mvar"] = load[:, h] / 1000, -q[:, h] / 1000
        net.sgen["p_mw"], net.sgen["q_mvar"] = pv[:, h] / 1000, 0.0
        net.ext_grid["vm_pu"] = m.vm_slack
        pp.runpp(net, calculate_voltage_angles=False, tolerance_mva=1e-11)
        nr["vm_pu"].append(net.res_bus.vm_pu.round(8).tolist())
        nr["trafo_loading_pct"].append(round(float(net.res_trafo.loading_percent.iloc[0]), 6))
        nr["line_loading_pct"].append(net.res_line.loading_percent.round(6).tolist())

    index = year_index(cfg.reference_year)
    for i, h in enumerate(hours):
        h["timestamp_utc"] = str(index[h["hour"]])
        h["load_kw"] = [round(x, 6) for x in load[:, i]]
        h["pv_kw"] = [round(x, 6) for x in pv[:, i]]
        h["q_kvar"] = [round(x, 6) for x in q[:, i]]          # net injection (+ = into the grid)
        h["sum_p_kw"] = round(float(p[:, i].sum()), 3)
    case = {
        "about": "Kapacitet+ reference network and hours for the PowerFactory cross-check (tools/pf_crossval.py)",
        "scenario": {"year": cfg.reference_year, "pv": "all", "hp": "all", "ev": "all", "load_seed": sc.load_seed},
        "versions": {"kapacitet": md.version("kapacitet"), "pandapower": pp.__version__, "numpy": np.__version__},
        "network": network_data(m),
        "hours": hours,
        "kapacitet": {"vm_pu": np.abs(res.v).T.round(8).tolist(),
                      "trafo_loading_pct": res.trafo_loading.round(6).tolist(),
                      "line_loading_pct": res.line_loading.T.round(6).tolist(),
                      "trafo_p_mw": res.trafo_p_mw.round(8).tolist()},
        "pandapower_nr": nr,
    }
    out.mkdir(parents=True, exist_ok=True)
    path = out / "case.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    return path


# --------------------------------------------------------------------- 2. inside PowerFactory

def _set(obj, **attrs) -> None:
    for k, v in attrs.items():
        obj.SetAttribute(k, v)


def _cubicle(term, name: str):
    return term.CreateObject("StaCubic", name)


def build_network(app, net: dict):
    """A new project with the network in it; returns (project, grid, objects by role)."""
    prj = app.CreateProject(f"{PROJECT} {time.strftime('%H%M%S')}", GRID)   # a fresh one on every run
    if prj is None:
        raise RuntimeError("CreateProject failed")
    prj.Activate()
    grid = prj.GetContents("*.ElmNet", 1)[0]
    equip = app.GetProjectFolder("equip")

    terms = {}
    for b in net["buses"]:
        t = grid.CreateObject("ElmTerm", f"B{b['idx']}")
        _set(t, uknom=b["vn_kv"], iUsage=0, systype=0, phtech=0)
        terms[b["idx"]] = t

    xnet = grid.CreateObject("ElmXnet", "MV grid")
    _set(xnet, bus1=_cubicle(terms[net["ext_grid"]["bus"]], "xnet"), bustp="SL",
         usetp=net["ext_grid"]["vm_pu"], phiini=0.0)

    tr = net["trafo"]
    if not str(tr["vector_group"]).upper().startswith("DYN"):   # the windings below are fixed to D/YN
        raise ValueError(f"only Dyn transformers are supported, not {tr['vector_group']!r}")
    ttyp = equip.CreateObject("TypTr2", "Trafo " + tr["std_type"].replace("/", "-"))
    # pcutr: copper losses (kW) = vkr % of rated power; curmg/pfe: no-load current and iron losses
    _set(ttyp, strn=tr["sn_mva"], utrn_h=tr["vn_hv_kv"], utrn_l=tr["vn_lv_kv"], uktr=tr["vk_percent"],
         pcutr=tr["vkr_percent"] / 100.0 * tr["sn_mva"] * 1000.0, curmg=tr["i0_percent"], pfe=tr["pfe_kw"],
         nt2ag=round(tr["shift_degree"] / 30.0) % 12, tr2cn_h="D", tr2cn_l="YN",
         tap_side=0 if tr["tap_side"] == "hv" else 1, dutap=tr["tap_step_percent"], phitr=0.0,
         nntap0=int(tr["tap_neutral"]), ntpmn=int(tr["tap_min"]), ntpmx=int(tr["tap_max"]), itapch=1)
    elm_tr = grid.CreateObject("ElmTr2", "T1")
    _set(elm_tr, typ_id=ttyp, bushv=_cubicle(terms[tr["hv_bus"]], "hv"), buslv=_cubicle(terms[tr["lv_bus"]], "lv"),
         nntap=int(tr["tap_pos"]), ntnum=1)

    ltypes = {}
    lines = {}
    for ln in net["lines"]:
        key = (ln["std_type"], ln["r_ohm_per_km"], ln["x_ohm_per_km"], ln["c_nf_per_km"], ln["max_i_ka"])
        if key not in ltypes:
            lt = equip.CreateObject("TypLne", f"{ln['std_type']} #{len(ltypes)}")
            _set(lt, uline=terms[ln["from_bus"]].GetAttribute("uknom"), sline=ln["max_i_ka"], rline=ln["r_ohm_per_km"], xline=ln["x_ohm_per_km"],
                 cline=ln["c_nf_per_km"] / 1000.0, gline=ln["g_us_per_km"], nlnph=3, nneutral=0, systp=0)
            ltypes[key] = lt
        e = grid.CreateObject("ElmLne", f"L{ln['idx']}")
        _set(e, typ_id=ltypes[key], dline=ln["length_km"], nlnum=ln["parallel"], fline=ln["df"],
             bus1=_cubicle(terms[ln["from_bus"]], f"L{ln['idx']}a"),
             bus2=_cubicle(terms[ln["to_bus"]], f"L{ln['idx']}b"))
        lines[ln["idx"]] = e

    loads, gens = [], []
    for c in net["customers"]:
        t = terms[c["bus"]]
        ld = grid.CreateObject("ElmLod", f"Load C{c['i']}")
        _set(ld, bus1=_cubicle(t, f"ld{c['i']}"), plini=0.0, qlini=0.0)
        g = grid.CreateObject("ElmGenstat", f"PV C{c['i']}")
        _set(g, bus1=_cubicle(t, f"pv{c['i']}"), sgn=0.05, cosn=1.0, pgini=0.0, qgini=0.0)
        loads.append(ld)
        gens.append(g)
    return prj, grid, {"terms": terms, "trafo": elm_tr, "lines": lines, "loads": loads, "gens": gens,
                       "xnet": xnet}


def run_in_powerfactory(app, case_path: str, out_path: str, tol_kva: float | None = 1e-5) -> dict:
    """Build the case's network, run one balanced load flow per hour, write the answers.

    `tol_kva` is the largest node power error PowerFactory may accept, at every voltage level.
    Its defaults (1 kVA on HV and MV buses, 0.01 kVA on LV buses) stop Newton-Raphson at about
    1e-4 pu on this network; None keeps them, to show that effect."""
    t0 = time.time()
    case = json.loads(Path(case_path).read_text(encoding="utf-8"))
    net = case["network"]
    prj, grid, obj = build_network(app, net)
    ldf = app.GetFromStudyCase("ComLdf")
    tol_attrs = ("errlf", "allowedBusErrMv", "allowedBusErrLv")   # bus equations: HV, MV, LV (kVA)
    defaults = {k: ldf.GetAttribute(k) for k in (*tol_attrs, "erreq") if ldf.HasAttribute(k)}
    ldf_settings = {"iopt_net": 0,      # AC balanced, positive sequence
                    "iopt_at": 0,       # no automatic tap adjustment
                    "iopt_lim": 0,      # no reactive power limits
                    "iopt_pq": 0,       # constant power: no voltage dependence of loads
                    "iopt_tem": 0}      # line resistances at 20 degC (the type data)
    if tol_kva is not None:
        ldf_settings.update({k: tol_kva for k in tol_attrs})
        ldf_settings["erreq"] = 1e-5    # model equations (%)
    for k, v in ldf_settings.items():
        if ldf.HasAttribute(k):
            ldf.SetAttribute(k, v)
    used = {k: ldf.GetAttribute(k) for k in (*ldf_settings, *defaults) if ldf.HasAttribute(k)}

    bus_order = [b["idx"] for b in net["buses"]]
    line_order = [ln["idx"] for ln in net["lines"]]
    out = {"vm_pu": [], "trafo_loading_pct": [], "line_loading_pct": [], "trafo_p_mw": [], "ierr": []}
    for h in case["hours"]:
        for i, (ld, g) in enumerate(zip(obj["loads"], obj["gens"])):
            _set(ld, plini=h["load_kw"][i] / 1000.0, qlini=-h["q_kvar"][i] / 1000.0)
            _set(g, pgini=h["pv_kw"][i] / 1000.0, qgini=0.0)
        ierr = ldf.Execute()
        out["ierr"].append(int(ierr))
        if ierr:
            for k in ("vm_pu", "trafo_loading_pct", "line_loading_pct", "trafo_p_mw"):
                out[k].append(None)
            continue
        out["vm_pu"].append([obj["terms"][b].GetAttribute("m:u") for b in bus_order])
        out["trafo_loading_pct"].append(obj["trafo"].GetAttribute("c:loading"))
        out["trafo_p_mw"].append(obj["trafo"].GetAttribute("m:P:bushv"))
        out["line_loading_pct"].append([obj["lines"][i].GetAttribute("c:loading") for i in line_order])
    import powerfactory
    out["about"] = {"powerfactory": getattr(powerfactory, "__version__", None),
                    "python": sys.version.split()[0], "project": prj.loc_name, "ldf_settings": used,
                    "ldf_defaults": defaults, "tol_kva": tol_kva,
                    "elements": {"terminals": len(obj["terms"]), "lines": len(obj["lines"]),
                                 "loads": len(obj["loads"]), "static_generators": len(obj["gens"])},
                    "seconds": round(time.time() - t0, 2)}
    Path(out_path).write_text(json.dumps(out), encoding="utf-8")
    return {"hours": len(case["hours"]), "ierr": out["ierr"], "about": out["about"]}


# ----------------------------------------------------------------------------- 3. compare

def _stats(pairs) -> dict:
    d = [abs(a - b) for a, b in pairs]
    if not d:                                    # no hour solved: nothing to compare, and no crash
        return {"max": None, "mean": None, "n": 0}
    return {"max": max(d), "mean": sum(d) / len(d), "n": len(d)}


def compare(case: dict, pf: dict) -> dict:
    """Max and mean absolute differences of PowerFactory against Kapacitet+ (Z-bus) and
    against pandapower's Newton-Raphson, over every hour PowerFactory solved."""
    refs = {"kapacitet": case["kapacitet"], "pandapower_nr": case["pandapower_nr"]}
    ok = [i for i, e in enumerate(pf["ierr"]) if e == 0]
    out: dict = {"hours_solved": len(ok), "hours": len(case["hours"]),
                 "all_hours_solved": len(ok) == len(case["hours"]),
                 "targets": {"v_pu": TARGET_V_PU, "trafo_loading_pct": TARGET_LOADING_PCT}}
    for name, ref in refs.items():
        v = [(pf["vm_pu"][i][b], ref["vm_pu"][i][b]) for i in ok for b in range(len(ref["vm_pu"][i]))]
        t = [(pf["trafo_loading_pct"][i], ref["trafo_loading_pct"][i]) for i in ok]
        ln = [(pf["line_loading_pct"][i][k], ref["line_loading_pct"][i][k])
              for i in ok for k in range(len(ref["line_loading_pct"][i]))]
        out[name] = {"v_pu": _stats(v), "trafo_loading_pct": _stats(t), "line_loading_pct": _stats(ln)}
        if "trafo_p_mw" in ref and "trafo_p_mw" in pf:   # HV-side inflow; the NR reference does not store it
            out[name]["trafo_p_kw"] = _stats([(pf["trafo_p_mw"][i] * 1000, ref["trafo_p_mw"][i] * 1000)
                                              for i in ok])
    k = out["kapacitet"]
    # an hour PowerFactory did not solve (often a peak) is never a met target
    out["meets_targets"] = {q: out["all_hours_solved"] and k[q]["max"] <= lim
                            for q, lim in (("v_pu", TARGET_V_PU), ("trafo_loading_pct", TARGET_LOADING_PCT))}
    rows = []
    for i in ok:
        h = case["hours"][i]
        kv, pv_ = case["kapacitet"]["vm_pu"][i], pf["vm_pu"][i]
        rows.append({"hour": h["hour"], "timestamp_utc": h["timestamp_utc"], "why": h["why"],
                     "sum_p_kw": h["sum_p_kw"],
                     "v_min_k": min(kv), "v_max_k": max(kv), "v_min_pf": min(pv_), "v_max_pf": max(pv_),
                     "max_dv": max(abs(a - b) for a, b in zip(kv, pv_)),
                     "trafo_k": case["kapacitet"]["trafo_loading_pct"][i], "trafo_pf": pf["trafo_loading_pct"][i],
                     "dp_trafo_kw": (pf["trafo_p_mw"][i] - case["kapacitet"]["trafo_p_mw"][i]) * 1000
                     if "trafo_p_mw" in pf and "trafo_p_mw" in case["kapacitet"] else None})
    out["per_hour"] = rows
    return out


def _e(x) -> str:
    return "n/a" if x is None else f"{x:.2e}"


def markdown(cmp: dict, solver: str = "PowerFactory") -> str:
    """The tables docs/validation-*.md quote; `solver` names the tool compared with Kapacitet+."""
    lines = ["| Hour (UTC, interval start) | Why | Net export (kW) | V min / max, Kapacitet+ (pu) | "
             f"V min / max, {solver} (pu) | max abs dV (pu) | Trafo loading, Kapacitet+ / {solver} (%) |",
             "|---|---|---:|---|---|---:|---|"]
    for r in cmp["per_hour"]:
        lines.append(f"| {r['timestamp_utc'][:16]} | {r['why']} | {r['sum_p_kw']:.1f} | "
                     f"{r['v_min_k']:.4f} / {r['v_max_k']:.4f} | {r['v_min_pf']:.4f} / {r['v_max_pf']:.4f} | "
                     f"{r['max_dv']:.2e} | {r['trafo_k']:.2f} / {r['trafo_pf']:.2f} |")
    dp = [f"{r['dp_trafo_kw']:+.2e}" for r in cmp["per_hour"] if r.get("dp_trafo_kw") is not None]
    if dp:
        lines += ["", f"Transformer inflow, {solver} minus Kapacitet+ (kW), per hour: " + ", ".join(dp)]
    lines += ["", "| Quantity | Against | max abs diff | mean abs diff | Target |", "|---|---|---:|---:|---:|"]
    for name, label in (("kapacitet", "Kapacitet+ (Z-bus)"), ("pandapower_nr", "pandapower NR")):
        s = cmp[name]
        for key, qty, target in (("v_pu", "Bus voltage (pu)", TARGET_V_PU),
                                 ("trafo_loading_pct", "Transformer loading (%)", TARGET_LOADING_PCT),
                                 ("line_loading_pct", "Line loading (%)", ""),
                                 ("trafo_p_kw", "Transformer inflow (kW)", "")):
            if key in s:
                lines.append(f"| {qty} | {label} | {_e(s[key]['max'])} | {_e(s[key]['mean'])} | {target} |")
    if not cmp["all_hours_solved"]:
        lines += ["", f"{solver} solved {cmp['hours_solved']} of {cmp['hours']} hours: targets not met."]
    return "\n".join(lines)


def _default_out() -> Path:
    from kapacitet.paths import processed
    return processed("validation", "powerfactory")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="write case.json (project environment)")
    e.add_argument("--out", type=Path)
    e.add_argument("--seed", type=int, default=7)
    e.add_argument("--others", type=int, default=8)
    c = sub.add_parser("compare", help="compare powerfactory.json with case.json")
    c.add_argument("--out", type=Path)
    c.add_argument("--pf", default="powerfactory.json", help="PowerFactory's answers, in the same folder")
    a = ap.parse_args(argv)
    out = a.out or _default_out()
    if a.cmd == "export":
        print(export(out, a.seed, a.others))
        return 0
    case = json.loads((out / "case.json").read_text(encoding="utf-8"))
    pf = json.loads((out / a.pf).read_text(encoding="utf-8"))
    cmp = compare(case, pf)
    cmp["versions"] = {**case["versions"], "powerfactory": pf["about"]}
    (out / a.pf.replace("powerfactory", "comparison")).write_text(json.dumps(cmp, indent=1), encoding="utf-8")
    print(markdown(cmp))
    print(json.dumps({k: cmp[k] for k in ("hours_solved", "meets_targets", "kapacitet", "pandapower_nr")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
