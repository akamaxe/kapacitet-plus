"""The study commands: `kapacitet study init|demo|fetch|import|check|build|status <dir>`, and the
hidden `kapacitet station build <sid> --study <dir>` that a study's build starts once per station.

pipeline.py registers them with one call to `register`; nothing heavy is imported until a command
runs.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("study", help="a DSO's study: many secondary substations, each built by its own process",
                       description="A study lives in its own folder, outside any git work tree; a bare name is a "
                                   "folder under %USERPROFILE%\\kapacitet-studies (or KAPACITET_STUDY_DIR).")
    ss = p.add_subparsers(dest="study_command", required=True)

    def add(name: str, help_: str) -> argparse.ArgumentParser:
        q = ss.add_parser(name, help=help_)
        q.add_argument("dir", help="the study folder, or a bare name under the default folder")
        q.add_argument("--allow-git", action="store_true",
                       help="allow a folder inside a git work tree (synthetic data only)")
        return q

    q = add("init", "start an empty study with a study.toml of defaults")
    q.add_argument("--name", default="", help="the DSO's name")
    add("demo", "create a synthetic study of three stations from pandapower's LV benchmark networks")
    q = add("fetch", "download the study's SMHI weather and spot prices into <dir>/raw")
    q.add_argument("--refresh", action="store_true", help="ignore what is already downloaded")
    q = add("import", "split inputs/network into one import folder per station")
    q.add_argument("--contract", default=None, help="add a station from a contract folder first")
    q.add_argument("--station", default=None, help="with --contract: the station's id (the folder's name by default)")
    q = add("check", "data-quality report on every station's network")
    q.add_argument("--station", action="append", default=None, help="only this station (repeatable)")
    q = add("build", "build the stations whose inputs changed, side by side")
    q.add_argument("--jobs", type=int, default=None, help="station processes at a time (default: 6, at most the cores)")
    q.add_argument("--force", action="store_true", help="build every station again")
    q.add_argument("--quick", action="store_true", help="one weather year and fewer adoption orders")
    q.add_argument("--station", action="append", default=None, help="only this station (repeatable)")
    q = add("status", "what each station is and how its last build went")
    q.add_argument("--json", action="store_true", help="the records as JSON")
    p.set_defaults(func=cmd_study)

    p = sub.add_parser("station", help=argparse.SUPPRESS)
    p.add_argument("action", choices=["build"])
    p.add_argument("sid")
    p.add_argument("--study", required=True)
    p.add_argument("--quick", action="store_true")
    p.add_argument("--allow-git", action="store_true")
    p.set_defaults(func=cmd_station)


def _fail(err: Exception, code: int = 2) -> None:
    print(err)
    sys.exit(code)


def cmd_study(args: argparse.Namespace) -> None:
    from . import StudyError

    try:
        {"init": _init, "demo": _demo, "fetch": _fetch, "import": _import, "check": _check, "build": _build,
         "status": _status}[args.study_command](args)
    except StudyError as err:
        _fail(err)


def _init(args) -> None:
    from . import init_study

    study = init_study(args.dir, args.name, allow_git=args.allow_git)
    print(f"Study {study.id} in {study.root}")
    print("Next: put one contract folder per station in inputs/network/contract/<station>, then")
    print(f"  kapacitet study import {study.root}")


def _demo(args) -> None:
    from .demo import create

    study = create(args.dir, allow_git=args.allow_git)
    print(f"Synthetic study {study.id} in {study.root}: stations {', '.join(study.station_ids())}")
    print(f"Next: kapacitet study build {study.root}")


def _fetch(args) -> None:
    """The fetch runs as its own process, so the study's site and folder are in force from the start."""
    from . import open_study

    study = open_study(args.dir, allow_git=args.allow_git)
    env = {k: v for k, v in os.environ.items() if not k.startswith("KAPACITET_")}
    env.update(KAPACITET_STUDY=str(study.root), KAPACITET_DATA_DIR=str(study.root),
               KAPACITET_HOME=str(study.root), KAPACITET_RAW_DIR=str(study.raw))
    cmd = [sys.executable, "-m", "kapacitet", "fetch", "--only", "smhi,prices"]
    if args.refresh:
        cmd.append("--refresh")
    sys.exit(subprocess.run(cmd, env=env).returncode)


def _import(args) -> None:
    from . import open_study
    from .intake import add_contract, check, split

    study = open_study(args.dir, allow_git=args.allow_git)
    if args.contract:
        sid = add_contract(study, args.contract, args.station)
        print(f"Added station {sid} from {args.contract}")
    sids = split(study)
    reports = check(study, sids)
    for sid in sids:
        r = reports[sid]
        print(f"  {sid}: {'usable' if r.usable else 'NOT usable'}, completeness {r.score}/100")
    print(f"{len(sids)} stations imported. Next: kapacitet study build {study.root}")


def _check(args) -> None:
    from . import open_study
    from .intake import check

    study = open_study(args.dir, allow_git=args.allow_git)
    reports = check(study, args.station)
    for sid, r in reports.items():
        print(f"--- {sid}")
        print(r.text())
    if not all(r.usable for r in reports.values()):
        sys.exit(1)


def _build(args) -> None:
    from . import open_study
    from .portfolio import run

    study = open_study(args.dir, allow_git=args.allow_git)
    out = run(study, stations=args.station, jobs=args.jobs, force=args.force, quick=args.quick,
              allow_git=args.allow_git)
    for sid, why in out["skipped"].items():
        print(f"  {sid}: {why}, not built")
    for sid, r in out["built"].items():
        tail = f" ({r['error']})" if r.get("error") else ""
        print(f"  {sid}: {r['status']} in {r['seconds']:.0f} s{tail}")
    if any(r["status"] != "ready" for r in out["built"].values()):
        sys.exit(1)


def _status(args) -> None:
    from . import open_study
    from .portfolio import reason

    study = open_study(args.dir, allow_git=args.allow_git)
    recs = [study.record(sid) for sid in study.station_ids()]
    if args.json:
        print(json.dumps(recs, indent=1, ensure_ascii=False))
        return
    print(f"Study {study.id} ({study.config.dso.name or 'no DSO name'}), method {study.config.dso.method_version}")
    for rec in recs:
        steps = " ".join(f"{s['name']}:{s['status']}" for s in rec.get("steps", []))
        why = reason(study, rec["id"], quick=bool(rec.get("quick")))
        stale = "" if why in ("unchanged", "building") else f" [{why}]"
        n = (rec.get("summary") or {}).get("customers")
        print(f"  {rec['id']}: {rec.get('status')}{stale}, {n if n is not None else '?'} customers  {steps}")
        if rec.get("error"):
            print(f"      {rec['error']}")


def cmd_station(args: argparse.Namespace) -> None:
    """One station's build, in this process (started by `study build`)."""
    import logging

    from . import StudyError, open_study
    from .station import build

    try:
        study = open_study(args.study, allow_git=args.allow_git)
        rec = build(study, args.sid, quick=args.quick)
    except StudyError as err:
        _fail(err)
    logging.getLogger("kapacitet.study").info("station %s: %s", args.sid, rec["status"])
    sys.exit(0 if rec["status"] == "ready" else 1)
