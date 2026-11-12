"""The study commands: `kapacitet study init|demo|fetch|import|check|build|status|collect|validate|
review|sign|publish|diff|cases|sync|sign-rule|portfolio|options <dir>`, and the hidden `kapacitet
station build|validate|cases|sync|options <sid> --study <dir>` that a study's build, validation,
case check, queue rebuild and options start once per station.

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
    how = q.add_mutually_exclusive_group()
    how.add_argument("--contract", default=None, help="add a station from a contract folder first")
    how.add_argument("--cim", action="store_true",
                     help="convert inputs/network/cgmes (CGMES 2.4.15) first, one station per LV transformer, "
                          "with inputs/cim_customers.csv naming each EnergyConsumer's customer_id and fuse_a")
    q.add_argument("--station", default=None, help="with --contract: the station's id (the folder's name by default)")
    q = add("check", "data-quality report on every station's network")
    q.add_argument("--station", action="append", default=None, help="only this station (repeatable)")
    q = add("build", "build the stations whose inputs changed, side by side")
    q.add_argument("--jobs", type=int, default=None, help="station processes at a time (default: 6, at most the cores)")
    q.add_argument("--force", action="store_true", help="build every station again")
    q.add_argument("--quick", action="store_true", help="one weather year and fewer adoption orders")
    q.add_argument("--prefer-identified", action="store_true",
                   help="a phase identified from the meters' voltages replaces the one the DSO recorded")
    q.add_argument("--station", action="append", default=None, help="only this station (repeatable)")
    q = add("status", "what each station is and how its last build went")
    q.add_argument("--json", action="store_true", help="the records as JSON")
    q = add("collect", "every station's capacity, upstream caps applied, into runs/<run>/stations.parquet")
    q.add_argument("--run", "--quarter", dest="quarter", default=None, help="the run: a quarter as 2026Q4 or a month as 2026-11 (default: this quarter, or this month with [publication] cadence = \"monthly\")")
    q = add("validate", "replay each station's metered hours and compare with the measurements and dpPower")
    q.add_argument("--station", action="append", default=None, help="only this station (repeatable)")
    q = add("review", "runs/<run>/review.csv, the least margin first, with runs/<run>/corrections.csv overlaid")
    q.add_argument("--run", "--quarter", dest="quarter", default=None, help="the run: a quarter as 2026Q4 or a month as 2026-11 (default: this quarter, or this month with [publication] cadence = \"monthly\")")
    q = add("sign", "seal the run's outputs in the study's evidence chain: runs/<run>/signoff.json")
    q.add_argument("--by", required=True, help='who signs, as "name, role"')
    q.add_argument("--run", "--quarter", dest="quarter", default=None, help="the run: a quarter as 2026Q4 or a month as 2026-11 (default: this quarter, or this month with [publication] cadence = \"monthly\")")
    q = add("publish", "runs/<run>/publication: capacity.csv and .geojson, the basis of calculation, the "
                       "validation report, the manifest and the embeddable map, embed/ (a draft, UTKAST, "
                       "unless signed)")
    q.add_argument("--run", "--quarter", dest="quarter", default=None, help="the run: a quarter as 2026Q4 or a month as 2026-11 (default: this quarter, or this month with [publication] cadence = \"monthly\")")
    q = add("diff", "runs/<to>/changes.csv: what changed since another run, and why")
    q.add_argument("--from", dest="q_from", default=None,
                   help="the earlier run, as 2026Q3 or 2026-10 (default: the run before --to in time, of either kind)")
    q.add_argument("--to", dest="q_to", default=None, help="the later run (default: this quarter or month)")
    q = add("cases", "check a batch of connection cases, station by station, into cases/<batch>: verdicts.csv, "
                     "the Swedish annexes and the drafts")
    q.add_argument("csv", help="the cases: case_id, received_at, connection_point_id, pv_kwp, pv_kw_ac, pv_phases, "
                               "ev_kw, ev_phases, hp, bat_kwh [, decision_dso]")
    q.add_argument("--batch", default=None, help="the batch's name (default: the file's name)")
    q.add_argument("--compare-decisions", action="store_true",
                   help="also cases/<batch>/confusion.csv: the proposals against decision_dso")
    q.add_argument("--jobs", type=int, default=None, help="station processes at a time")
    q = add("sync", "rebuild every station's queue from the DSO's approved connections, into "
                    "cases/<batch>/sync_report.csv")
    q.add_argument("--approved", required=True, help="the approved connections: a case's columns, decision "
                                                     "(firm|nonfirm) and decided_at")
    q.add_argument("--pending", default=None, help="the cases received and not yet decided")
    q.add_argument("--batch", default=None, help="the report's name (default: sync-<approved file's name>)")
    q.add_argument("--jobs", type=int, default=None, help="station processes at a time")
    q = add("sign-rule", "sign the automatic rule for the study's cases: rule_signature.json, sealed")
    q.add_argument("--by", required=True, help='who signs, as "name, role"')
    q.add_argument("--kinds", required=True, help="the kinds of request it covers: pv,ev,hp,battery")
    q.add_argument("--max-pv-kwp", type=float, required=True, help="the largest solar system it covers (kWp)")
    q.add_argument("--valid-until", required=True, help="the last day it holds, as 2027-06-30")
    q = add("portfolio", "runs/<quarter>/portfolio.csv and portfolio_sv.md: per station its margins, risk and "
                         "trend, the least margin per customer first")
    q.add_argument("--run", "--quarter", dest="quarter", default=None, help="the run's quarter (default: this quarter)")
    q = add("options", "runs/<quarter>/options.csv and a Swedish note per station: reinforce now, connect with "
                       "conditions or wait, priced from study.toml [costs]")
    q.add_argument("--run", "--quarter", dest="quarter", default=None, help="the run's quarter (default: this quarter)")
    q.add_argument("--jobs", type=int, default=None, help="station processes at a time")
    q = add("devplan", "runs/<quarter>/devplan/: hourly profiles, the flexibility need and a ten-year investment "
                       "list per station, inputs for the network development plan (run `study options` first)")
    q.add_argument("--run", "--quarter", dest="quarter", default=None, help="the run's quarter (default: this quarter)")
    q.add_argument("--jobs", type=int, default=None, help="station processes at a time")
    q = add("scenarios", "runs/<quarter>/scenarios.csv: each station grown a year at a time to [scenarios] year "
                         "from its own equipment and meters, with what is left of its published capacity")
    q.add_argument("--run", "--quarter", dest="quarter", default=None, help="the run's quarter (default: this quarter)")
    q.add_argument("--jobs", type=int, default=None, help="station processes at a time")
    p.set_defaults(func=cmd_study)

    p = sub.add_parser("station", help=argparse.SUPPRESS)
    p.add_argument("action", choices=["build", "validate", "cases", "sync", "options", "devplan", "scenarios"])
    p.add_argument("sid")
    p.add_argument("--study", required=True)
    p.add_argument("--batch", default=None)
    p.add_argument("--quick", action="store_true")
    p.add_argument("--prefer-identified", action="store_true")
    p.add_argument("--allow-git", action="store_true")
    p.set_defaults(func=cmd_station)


def _fail(err: Exception, code: int = 2) -> None:
    print(err)
    sys.exit(code)


def cmd_study(args: argparse.Namespace) -> None:
    from . import StudyError

    try:
        {"init": _init, "demo": _demo, "fetch": _fetch, "import": _import, "check": _check, "build": _build,
         "status": _status, "collect": _collect, "validate": _validate, "review": _review, "sign": _sign,
         "publish": _publish, "diff": _diff, "cases": _cases, "sync": _sync,
         "sign-rule": _sign_rule, "portfolio": _portfolio, "options": _options,
         "devplan": _devplan, "scenarios": _scenarios}[args.study_command](args)
    except StudyError as err:
        _fail(err)


def _init(args) -> None:
    from . import init_study

    study = init_study(args.dir, args.name, allow_git=args.allow_git)
    print(f"Study {study.id} in {study.root}")
    print("Next: put one contract folder per station in inputs/network/contract/<station>, then")
    print(f"  kapacitet study import {study.root}")
    print("or CGMES files in inputs/network/cgmes with inputs/cim_customers.csv beside them, then")
    print(f"  kapacitet study import {study.root} --cim")


def _demo(args) -> None:
    from .demo import create

    study = create(args.dir, allow_git=args.allow_git)
    print(f"Synthetic study {study.id} in {study.root}: stations {', '.join(study.station_ids())}")
    print(f"Next: kapacitet study build {study.root}")


def _fetch(args) -> None:
    """The fetch runs as its own process, so the study's site and folder are in force from the start.
    A study without a place of its own has no weather to fetch: the reference site's would come
    down under the study's name."""
    from . import StudyError, open_study

    study = open_study(args.dir, allow_git=args.allow_git)
    if not study.config.site.given:
        raise StudyError("no_site")
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
    if args.cim:
        from .intake import import_cim

        conversion, removed = import_cim(study)
        _print_conversion(conversion, removed)
        if conversion.blocked:
            print("Nothing written: correct the errors above and run the import again.")
            sys.exit(1)
    sids = split(study)
    reports = check(study, sids)
    for sid in sids:
        r = reports[sid]
        print(f"  {sid}: {'usable' if r.usable else 'NOT usable'}, completeness {r.score}/100")
    print(f"{len(sids)} stations imported. Next: kapacitet study build {study.root}")


def _findings(findings, indent: str) -> None:
    for f in sorted(findings, key=lambda f: (f.severity != "error", f.severity != "warning")):
        tail = f" ({f.count})" if f.count else ""
        print(f"{indent}[{f.severity:<7}] {f.check}: {f.message}{tail}")


def _print_conversion(conversion, removed: list[str]) -> None:
    files = ", ".join(f.name for f in conversion.files)
    print(f"CGMES: {len(conversion.stations)} low-voltage stations in {files}")
    _findings(conversion.findings.findings, "  ")
    for st in conversion.stations:
        n = len(st.tables["customers"]) if st.tables is not None else None
        what = f"{n} customers" if n is not None else "not written"
        print(f"  {st.sid}: {st.name}, transformer {st.trafo_mrid}, {what}")
        _findings(st.findings.findings, "      ")
    for sid in removed:
        print(f"  {sid}: no longer in the CGMES files, its contract folder removed")


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
              allow_git=args.allow_git, prefer_identified=args.prefer_identified)
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
    shared = study.shared_digests()
    for rec in recs:
        steps = " ".join(f"{s['name']}:{s['status']}" for s in rec.get("steps", []))
        why = reason(study, rec["id"], quick=bool(rec.get("quick")), shared=shared,
                     prefer_identified=bool(rec.get("prefer_identified")))
        stale = "" if why in ("unchanged", "building") else f" [{why}]"
        n = (rec.get("summary") or {}).get("customers")
        print(f"  {rec['id']}: {rec.get('status')}{stale}, {n if n is not None else '?'} customers  {steps}")
        if rec.get("error"):
            print(f"      {rec['error']}")


def _collect(args) -> None:
    from . import open_study
    from .collect import collect

    study = open_study(args.dir, allow_git=args.allow_git)
    out = collect(study, args.quarter)
    print(f"{out['rows']} stations in {out['path']}")
    for sid in out["rewritten"]:
        print(f"  {sid}: capacity.json takes the current upstream caps")


def _validate(args) -> None:
    from . import open_study
    from .portfolio import validate

    study = open_study(args.dir, allow_git=args.allow_git)
    out = validate(study, stations=args.station, allow_git=args.allow_git)
    for sid, r in out.items():
        print(f"  {sid}: {'done' if r['code'] == 0 else 'failed (see build.log)'} in {r['seconds']:.0f} s")
    if any(r["code"] != 0 for r in out.values()):
        sys.exit(1)


def _quarter(q: str | None, study=None) -> str:
    """The run given, or this quarter (this month with [publication] cadence = "monthly")."""
    from . import runs

    return q or runs.current(study.config.publication.cadence if study is not None else "quarterly")


def _review(args) -> None:
    from . import open_study
    from .review import review

    study = open_study(args.dir, allow_git=args.allow_git)
    out = review(study, _quarter(args.quarter, study))
    print(f"{len(out['rows'])} stations in {out['paths'][0]}, the least margin first")
    for f in out["findings"]:
        print(f"  [correction not applied] {f}")


def _sign(args) -> None:
    from . import open_study
    from .publish import sign

    study = open_study(args.dir, allow_git=args.allow_git)
    doc = sign(study, _quarter(args.quarter, study), args.by)
    print(f"Signed by {doc['by']}: {len(doc['outputs'])} files sealed as publication {doc['seal']['id']} "
          f"in {study.evidence}")


def _publish(args) -> None:
    from . import open_study
    from .publish import DRAFT, publish

    study = open_study(args.dir, allow_git=args.allow_git)
    out = publish(study, _quarter(args.quarter, study))
    print(f"{out['cells']} cells published in {out['folder']} ({out['status']})")
    if out["status"] == DRAFT:
        why = f": changed since the signature: {', '.join(out['changed'])}" if out["changed"] else ": not signed"
        print(f"  a draft{why}")
    for f in out["findings"]:
        print(f"  [correction not applied] {f}")


def _diff(args) -> None:
    from . import open_study
    from .diff import diff

    study = open_study(args.dir, allow_git=args.allow_git)
    out = diff(study, args.q_from, _quarter(args.q_to, study))
    print(f"{len(out['rows'])} changes in {out['paths'][0]}, since {out['from']}")


def _cases(args) -> None:
    from . import open_study
    from .cases import run

    study = open_study(args.dir, allow_git=args.allow_git)
    out = run(study, args.csv, batch=args.batch, compare=args.compare_decisions, jobs=args.jobs,
              allow_git=args.allow_git)
    counts = {p: sum(1 for r in out["rows"] if r.get("proposal") == p) for p in ("firm", "nonfirm", "review")}
    print(f"{len(out['rows'])} cases in {out['paths'][0]}: {counts['firm']} firm, {counts['nonfirm']} nonfirm, "
          f"{counts['review']} to a person, {len(out['errors'])} not checked")
    for e in out["errors"]:
        print(f"  {e['case_id']}: {e['error_text']}")
    if out["confusion"] is not None:
        print(f"  proposals against the DSO's decisions in {out['folder'] / 'confusion.csv'}")


def _sync(args) -> None:
    from . import open_study
    from .sync import run

    study = open_study(args.dir, allow_git=args.allow_git)
    out = run(study, args.approved, args.pending, batch=args.batch, jobs=args.jobs, allow_git=args.allow_git)
    approved = [r for r in out["rows"] if r.get("status") == "approved"]
    flagged = [r for r in approved if r.get("flags")]
    print(f"{len(out['stations'])} stations synced: {len(approved)} approved connections, {len(flagged)} flagged, "
          f"in {out['paths'][0]}")
    for r in flagged:
        print(f"  {r['case_id']} ({r['station']}): {r['flags']}")
    for e in out["errors"]:
        print(f"  {e['case_id']}: {e['error_text']}")


def _sign_rule(args) -> None:
    from . import open_study
    from .cases import sign_rule

    study = open_study(args.dir, allow_git=args.allow_git)
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    doc = sign_rule(study, args.by, kinds, args.max_pv_kwp, args.valid_until)
    print(f"Rule signed by {doc['signed_by']} ({doc['role']}) for {', '.join(doc['request_kinds'])} up to "
          f"{doc['max_pv_kwp']:g} kWp until {doc['valid_until']}, sealed as {doc['seal']['id']}")


def _portfolio(args) -> None:
    from . import open_study
    from .decision import portfolio

    study = open_study(args.dir, allow_git=args.allow_git)
    out = portfolio(study, _quarter(args.quarter, study))
    print(f"{len(out['rows'])} stations in {out['paths'][0]}, the least margin per customer first")


def _options(args) -> None:
    from . import open_study
    from .decision import options

    study = open_study(args.dir, allow_git=args.allow_git)
    out = options(study, _quarter(args.quarter, study), jobs=args.jobs, allow_git=args.allow_git)
    counts = {k: sum(1 for r in out["rows"] if r["recommendation"] == k) for k in ("reinforce", "condition", "wait")}
    none = sum(1 for r in out["rows"] if not r["recommendation"])
    print(f"{len(out['rows'])} stations in {out['paths'][0]} ({len(out['computed'])} computed now): "
          f"{counts['reinforce']} reinforce, {counts['condition']} condition, {counts['wait']} wait, "
          f"{none} without a proposal")
    for sid in out["failed"]:
        print(f"  {sid}: failed (see its build.log)")
    if out["failed"]:
        sys.exit(1)


def _devplan(args) -> None:
    from . import open_study
    from .devplan import devplan

    study = open_study(args.dir, allow_git=args.allow_git)
    out = devplan(study, _quarter(args.quarter, study), jobs=args.jobs, allow_git=args.allow_git)
    folder = study.runs / out["quarter"] / "devplan"
    print(f"{len(out['investments'])} stations in {folder} ({len(out['computed'])} computed now), reference year "
          f"{out['reference_year'] or '-'}")
    if out["options_missing"]:
        print("  no options for this quarter: run `kapacitet study options` for the investment list")
    for sid in out["failed"]:
        print(f"  {sid}: failed (see its build.log)")
    if out["failed"]:
        sys.exit(1)


def _scenarios(args) -> None:
    from . import open_study
    from .scenarios import rates_label, scenarios

    study = open_study(args.dir, allow_git=args.allow_git)
    out = scenarios(study, _quarter(args.quarter, study), jobs=args.jobs, allow_git=args.allow_git)
    g = out["growth"]
    stations = len({r["station_id"] for r in out["rows"]})
    print(f"{stations} stations in {out['paths'][0]} ({len(out['computed'])} computed now), "
          f"{g['start_year']}-{g['year']}, {rates_label(g)} growth rates")
    for sid in out["failed"]:
        print(f"  {sid}: failed (see its build.log)")
    if out["failed"]:
        sys.exit(1)


def cmd_station(args: argparse.Namespace) -> None:
    """One station's build or validation, in this process (started by `study build|validate`)."""
    import logging

    from . import StudyError, open_study
    from .station import build, validate

    try:
        study = open_study(args.study, allow_git=args.allow_git)
        if args.action == "validate":
            validate(study, args.sid)
            sys.exit(0)
        if args.action == "options":
            from .decision import station_options

            if not args.batch:
                raise StudyError("bad_batch", batch="")
            station_options(study, args.sid, args.batch)
            sys.exit(0)
        if args.action == "devplan":
            from .devplan import station_devplan

            if not args.batch:
                raise StudyError("bad_batch", batch="")
            station_devplan(study, args.sid, args.batch)
            sys.exit(0)
        if args.action == "scenarios":
            from .scenarios import station_scenarios

            if not args.batch:
                raise StudyError("bad_batch", batch="")
            station_scenarios(study, args.sid, args.batch)
            sys.exit(0)
        if args.action in ("cases", "sync"):
            from .cases import station_cases
            from .sync import station_sync

            if not args.batch:
                raise StudyError("bad_batch", batch="")
            (station_cases if args.action == "cases" else station_sync)(study, args.sid, args.batch)
            sys.exit(0)
        from .. import telemetry

        telemetry.setup()       # this process is the build: its spans go out when telemetry is on
        rec = build(study, args.sid, quick=args.quick, prefer_identified=args.prefer_identified)
    except StudyError as err:
        _fail(err)
    logging.getLogger("kapacitet.study").info("station %s: %s", args.sid, rec["status"])
    sys.exit(0 if rec["status"] == "ready" else 1)
