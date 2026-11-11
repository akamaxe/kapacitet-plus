"""Where do envelopes win? The capacity stages and the intraday scorecard on the study demo's
three reference networks, each at two MV voltages (docs/envelopes-by-network.md).

    python tools/envelope_networks.py prepare <work> [--jobs 3]
    python tools/envelope_networks.py run <work> --network demo-landnetz --mv 1.06 [--workers 4]
    python tools/envelope_networks.py all <work> [--workers 4]
    python tools/envelope_networks.py table <work> [--json FILE]

``prepare`` makes the study of `kapacitet study demo` in `<work>/study` and builds its stations
in quick mode, which gives each network its grid and its profiles for every weather year.

``run`` computes one case in `<work>/cases/<network>@<mv>`, a copy of the station's data folder:
the capacity of 20 adoption orders over the weather years (`run.capacity`), the envelopes issued
the day before (`run_extra.foresight`) and the ones re-issued during the day with their scorecard
(`run_extra.intraday`), then the limit the next adopter after the hourly study breaks, per order
(`station.limit_of`). The MV voltage is the study layer of the assumptions (`mv_voltage_pu` in a
study.toml of its own, read through KAPACITET_STUDY), so the worker processes the stages start
apply it as this one does. A case takes its stages up where an earlier run left them. ``all``
runs every case that is not finished, one after another.

``table`` reads the finished cases, applies the decision rule and prints the two tables of the
document; ``--json`` also writes the summary they are made from.

Nothing here is new engine code: it chains the stages the reference network is published with.
`<work>` must be outside the repository.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

NETWORKS = ("demo-dickert", "demo-dorfnetz", "demo-landnetz")
NAMES = {"demo-dickert": "Dickert, middle feeders", "demo-dorfnetz": "Kerber Dorfnetz",
         "demo-landnetz": "Kerber Landnetz, cable"}
MV_LEVELS = (1.00, 1.06)
# The decision rule of docs/envelopes-by-network.md, written before any case was computed.
RULE_GAIN_PCT = 10.0
RULE_VIOLATION_HOURS = 0
# What a station's own build leaves in processed/ that a case computes for itself or never reads.
NOT_COPIED = ("results", "request", "ui", "nowcast")
BASES = ("dayahead", "intraday_worst_change", "intraday_forecast")
BASIS_LABEL = {"dayahead": "issued the day before", "intraday_worst_change": "re-issued, largest past change",
               "intraday_forecast": "re-issued, quantile forecast"}
LIMIT_LABEL = {"voltage": "voltage", "cable": "cable", "transformer": "transformer",
               "nonconverged": "no solution", "none": "every house fits"}


# ---------------------------------------------------------------- the folders of a run

def case_name(network: str, mv: float) -> str:
    return f"{network}@{mv:.2f}"


def case_dir(work: Path, network: str, mv: float) -> Path:
    return work / "cases" / case_name(network, mv)


def study_at(work: Path, mv: float) -> Path:
    """A folder holding the demo study's study.toml with the MV voltage as its one override."""
    folder = work / f"study@{mv:.2f}"
    target = folder / "study.toml"
    if target.exists():
        return folder
    text = (work / "study" / "study.toml").read_text(encoding="utf-8")
    if tomllib.loads(text).get("assumptions"):
        raise SystemExit("the demo study already overrides assumptions: this script expects none")
    override = (f'mv_voltage_pu = {{ value = {mv:.2f}, unit = "pu", '
                'source = "docs/envelopes-by-network.md: the MV voltage of this case" }')
    lines = text.splitlines()
    header = next((i for i, ln in enumerate(lines) if ln.strip() == "[assumptions]"), None)
    if header is None:                    # the template has the (empty) section; a study.toml may not
        lines += ["", "[assumptions]", override]
    else:
        lines.insert(header + 1, override)
    folder.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    got = tomllib.loads(target.read_text(encoding="utf-8"))["assumptions"]["mv_voltage_pu"]["value"]
    assert abs(got - mv) < 1e-9, target
    return folder


def case_env(work: Path, network: str, mv: float) -> dict[str, str]:
    """The environment of a case's process: nothing of this process's KAPACITET_* survives."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("KAPACITET_")}
    study = study_at(work, mv)
    env.update(KAPACITET_DATA_DIR=str(case_dir(work, network, mv)), KAPACITET_HOME=str(study),
               KAPACITET_STUDY=str(study), KAPACITET_RAW_DIR=str(work / "study" / "raw"),
               KAPACITET_INBOX_WORKER="0", PYTHONUNBUFFERED="1")
    return env


def place_case(work: Path, network: str, mv: float) -> Path:
    """The case's data folder: the station's grid and profiles, without what its own build computed."""
    target = case_dir(work, network, mv)
    if (target / "processed" / "grid").is_dir():
        return target
    source = work / "study" / "stations" / network / "processed"
    if not (source / "grid").is_dir():
        raise SystemExit(f"{network} is not built in {work / 'study'}: run `prepare` first")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target / "processed", ignore=shutil.ignore_patterns(*NOT_COPIED))
    return target


# ---------------------------------------------------------------- prepare and run

def _kapacitet(args: list[str]) -> None:
    subprocess.run([sys.executable, "-m", "kapacitet", *args], check=True)


def prepare(work: Path, jobs: int) -> None:
    study = work / "study"
    if not (study / "study.toml").exists():
        _kapacitet(["study", "demo", str(study)])
    _kapacitet(["study", "build", str(study), "--quick", "--jobs", str(jobs)])


def run_case(work: Path, network: str, mv: float, workers: int, quick: bool = False) -> int:
    """One case in its own process and environment; its log is <case>/run.log."""
    folder = place_case(work, network, mv)
    cmd = [sys.executable, str(Path(__file__).resolve()), "_case", "--workers", str(workers)]
    if quick:
        cmd.append("--quick")
    with (folder / "run.log").open("ab") as log:
        return subprocess.run(cmd, env=case_env(work, network, mv), stdout=log, stderr=subprocess.STDOUT).returncode


def finished(folder: Path) -> bool:
    return (folder / "case.json").exists()


def _case(workers: int, quick: bool) -> None:
    """Runs inside a case's environment (case_env): the three stages, then the binding limits."""
    from kapacitet import paths
    from kapacitet.config import A, settings
    from kapacitet.engine import run, run_extra

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    results = paths.PROCESSED / "results"
    cfg = settings()
    years = tuple(cfg.weather_years) if not quick else (cfg.reference_year,)
    n_orders = run.N_ORDERS if not quick else 4
    z_star = float(A("z_star_pct"))
    record = paths.DATA / "case.json"
    timing_file = paths.DATA / "timing.json"
    timing = json.loads(timing_file.read_text(encoding="utf-8")) if timing_file.exists() else {}

    def stage(name: str, output: str, fn) -> None:
        if (results / output).exists():
            return
        t0 = time.perf_counter()
        fn()
        timing[f"{name}_s"] = round(time.perf_counter() - t0, 1)
        timing_file.write_text(json.dumps(timing, indent=1), encoding="utf-8")

    stage("capacity", "hosting_summary.json", lambda: run.capacity(years, n_orders, z_star, workers))
    stage("dayahead", "foresight.json", lambda: run_extra.foresight(years, z_star, workers=workers))
    stage("intraday", "intraday.json", lambda: run_extra.intraday(years, z_star, workers=workers))
    t0 = time.perf_counter()
    binds = binding_limits(years)
    timing["binding_s"] = round(time.perf_counter() - t0, 1)
    record.write_text(json.dumps({"mv_voltage_pu": float(A("mv_voltage_pu")), "years": list(years),
                                  "orders": n_orders, "quick": bool(quick), "binds": binds, "seconds": timing,
                                  "code": _code(), "versions": run._versions()}, indent=1), encoding="utf-8")


def _code() -> dict:
    from kapacitet.study import code_version

    return code_version()


def binding_limits(years) -> dict[str, int]:
    """For each adoption order, the limit the next adopter after the hourly study's count breaks:
    how many orders each kind of limit binds in."""
    import numpy as np

    from kapacitet import paths
    from kapacitet.engine import run
    from kapacitet.engine import station as es
    from kapacitet.engine.scenario import Scenario, adoption_order

    m = run.model()
    hp, ev = run.background_masks(m.C)
    summary = json.loads((paths.PROCESSED / "results" / "hosting_summary.json").read_text(encoding="utf-8"))

    def states(order, k: int):
        mask = np.zeros(m.C, dtype=bool)
        mask[order[:k]] = True
        for y in years:
            arr = Scenario(year=y, pv=mask, hp=hp, ev=ev).arrays()
            yield y, arr["p_net"].astype(float), arr["q"].astype(float)

    counts: dict[str, int] = {}
    for r in summary["per_order"]:
        k = int(r["timeseries"]["adopters"])
        limit = "none" if k >= m.C else (es.limit_of(m, states(adoption_order(r["order"], m.C), k + 1), "export")["limit"]
                                         or "none")
        counts[limit] = counts.get(limit, 0) + 1
    return counts


# ---------------------------------------------------------------- the summary and the rule

def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def summarise(folder: Path) -> dict:
    """One finished case as the tables read it: every figure a median over the adoption orders."""
    res = folder / "processed" / "results"
    hs, fs, idy, case = (_read(res / "hosting_summary.json"), _read(res / "foresight.json"),
                         _read(res / "intraday.json"), _read(folder / "case.json"))
    grid = _read(folder / "processed" / "grid" / "summary.json")
    med = {k: v["kw_ac"]["median"] for k, v in hs["measures"].items()}

    def basis(top: dict | None, key: str | None = None) -> dict | None:
        if top is None:
            return None
        pick = (lambda b: b["median_kw"][key]) if key else (lambda b: b["median_kw"])
        return {name: {"kw": pick(b), "violation_hours": int(b["violation_hours"])}
                for name, b in (("aggregate", top), ("installation", top["installation"]))}

    variants = idy.get("variants") or {}
    network, _, mv = folder.name.partition("@")
    out = {
        "network": network, "name": NAMES.get(network, network), "mv_pu": float(case["mv_voltage_pu"]),
        "customers": int(hs["customers"]), "transformer": grid.get("trafo_type"),
        "orders": int(case["orders"]), "years": [case["years"][0], case["years"][-1]], "quick": bool(case["quick"]),
        "binds": case["binds"],
        "static_kw": med["static"], "hourly_kw": med["timeseries"],
        "perfect_foresight": {"aggregate": {"kw": med["doe"]}, "installation": {"kw": med["doe_installation"]}},
        "dayahead": basis(fs, "climatological"),
        "intraday_worst_change": basis(variants.get("worst_change")),
        "intraday_forecast": basis(variants.get("forecast")),
        "scorecard": {name: {"aggregate": v["scorecard"]["result"], "installation": v["installation"]["scorecard"]["result"]}
                      for name, v in variants.items()},
        "seconds": case["seconds"], "code": case["code"], "versions": case["versions"],
    }
    assert abs(out["mv_pu"] - float(mv)) < 1e-9, f"{folder.name} was computed at {out['mv_pu']} pu"
    out["rule"] = rule(out)
    return out


def gain_pct(kw: float | None, hourly_kw: float) -> float | None:
    return None if kw is None or not hourly_kw else (kw / hourly_kw - 1.0) * 100.0


def rule(case: dict) -> dict:
    """The decision rule on one case: each issuable basis with the budget held per installation
    against the hourly study, and the perfect-foresight ceiling beside it."""
    hourly = case["hourly_kw"]
    bases = {}
    for name in BASES:
        b = (case.get(name) or {}).get("installation")
        if b is None:
            continue
        g = gain_pct(b["kw"], hourly)      # a withheld capacity (an unverified replay) has no figure
        bases[name] = {"gain_pct": g, "violation_hours": b["violation_hours"],
                       "passes": bool(g is not None and g >= RULE_GAIN_PCT - 1e-9
                                      and b["violation_hours"] <= RULE_VIOLATION_HOURS)}
    ceiling = gain_pct(case["perfect_foresight"]["installation"]["kw"], hourly)
    best = max((b["gain_pct"] for b in bases.values() if b["gain_pct"] is not None), default=None)
    return {"gain_threshold_pct": RULE_GAIN_PCT, "bases": bases, "best_gain_pct": best,
            "ceiling_gain_pct": ceiling, "room": bool(ceiling is not None and ceiling >= RULE_GAIN_PCT - 1e-9),
            "passes": any(b["passes"] for b in bases.values())}


def summaries(work: Path) -> list[dict]:
    folders = sorted(p for p in (work / "cases").glob("*@*") if finished(p)) if (work / "cases").is_dir() else []
    return [summarise(p) for p in folders]


# ---------------------------------------------------------------- the tables of the document

def _kw(x: float | None) -> str:
    return "–" if x is None else f"{x:.1f}"


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{x:+.1f} %"


def _binds(counts: dict[str, int]) -> str:
    return ", ".join(f"{LIMIT_LABEL.get(k, k)} {n}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))


def _verdict(r: dict) -> str:
    if r["passes"]:
        return "**passes**"
    return "does not pass" if r["room"] else "does not pass (no room)"


def tables(cases: list[dict]) -> str:
    """The two tables of docs/envelopes-by-network.md, from the cases' summaries."""
    if not cases:
        return "No case is finished."
    head = ("| Network | MV (pu) | Houses | Limit that binds (orders) | Static | Hourly study | Day before | "
            "Re-issued, largest change | Re-issued, forecast | Perfect foresight | Best issuable | Ceiling | Rule |")
    lines = ["Capacity in kW AC with the 3 % budget held per installation (the rule's basis); the last three "
             "columns are against the hourly study.", "", head, "|" + "---|" * 13]
    for c in cases:
        inst = lambda name: _kw(((c.get(name) or {}).get("installation") or {}).get("kw"))  # noqa: E731
        r = c["rule"]
        lines.append(f"| {c['name']} | {c['mv_pu']:.2f} | {c['customers']} | {_binds(c['binds'])} | "
                     f"{_kw(c['static_kw'])} | {_kw(c['hourly_kw'])} | {inst('dayahead')} | "
                     f"{inst('intraday_worst_change')} | {inst('intraday_forecast')} | "
                     f"{_kw(c['perfect_foresight']['installation']['kw'])} | {_pct(r['best_gain_pct'])} | "
                     f"{_pct(r['ceiling_gain_pct'])} | {_verdict(r)} |")
    lines += ["", "The same with the budget held for the installations together (aggregate), the hours above a limit "
              "in the replay of every issuable basis (both budgets), and the product's own scorecard.", "",
              "| Network | MV (pu) | Day before | Re-issued, largest change | Re-issued, forecast | Perfect foresight | "
              "Hours above a limit | Scorecard, largest change | Scorecard, forecast |", "|" + "---|" * 9]
    for c in cases:
        agg = lambda name: _kw(((c.get(name) or {}).get("aggregate") or {}).get("kw"))  # noqa: E731
        hours = sum(b["violation_hours"] for name in BASES for b in (c.get(name) or {}).values())
        card = lambda v: " / ".join(c["scorecard"][v][b] for b in ("aggregate", "installation")) if v in c["scorecard"] else "–"  # noqa: E731
        lines.append(f"| {c['name']} | {c['mv_pu']:.2f} | {agg('dayahead')} | {agg('intraday_worst_change')} | "
                     f"{agg('intraday_forecast')} | {_kw(c['perfect_foresight']['aggregate']['kw'])} | {hours} | "
                     f"{card('worst_change')} | {card('forecast')} |")
    return "\n".join(lines)


# ---------------------------------------------------------------- the command line

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="the demo study and its stations' grids and profiles")
    p.add_argument("work", type=Path)
    p.add_argument("--jobs", type=int, default=3)
    for name, text in (("run", "one case"), ("all", "every case that is not finished")):
        p = sub.add_parser(name, help=text)
        p.add_argument("work", type=Path)
        p.add_argument("--workers", type=int, default=4)
        p.add_argument("--quick", action="store_true", help="a smoke run (one year, four orders); never reported")
        if name == "run":
            p.add_argument("--network", required=True, choices=NETWORKS)
            p.add_argument("--mv", type=float, required=True)
    p = sub.add_parser("table", help="the tables of the document, from the finished cases")
    p.add_argument("work", type=Path)
    p.add_argument("--json", type=Path, help="also write the summary the tables are made from")
    p = sub.add_parser("_case")              # inside a case's environment (run_case)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--quick", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "_case":
        _case(a.workers, a.quick)
        return 0
    work = a.work.resolve()
    if a.cmd == "prepare":
        prepare(work, a.jobs)
        return 0
    if a.cmd in ("run", "all"):
        # the high MV voltage first (where the voltage is expected to bind), the smallest network first
        todo = ([(a.network, a.mv)] if a.cmd == "run" else
                [(n, mv) for mv in sorted(MV_LEVELS, reverse=True) for n in reversed(NETWORKS)])
        failed = 0
        for network, mv in todo:
            folder = case_dir(work, network, mv)
            if finished(folder):
                print(f"{folder.name}: already finished")
                continue
            t0 = time.perf_counter()
            code = run_case(work, network, mv, a.workers, a.quick)
            print(f"{folder.name}: {'finished' if code == 0 else f'failed with {code} (see run.log)'} "
                  f"in {time.perf_counter() - t0:.0f} s", flush=True)
            failed += code != 0
        return 1 if failed else 0
    cases = summaries(work)
    if any(c["quick"] for c in cases):
        print("(some cases are smoke runs, one year and four orders: not for the document)\n")
    print(tables(cases))
    if a.json:
        a.json.write_text(json.dumps(cases, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
