"""Command-line entry point: python -m kapacitet <command>.

Commands
  fetch     download and cache all external data (with provenance manifest)
  build     build the synthetic LV network on the OSM neighbourhood
  profiles  build hourly weather, PV, household, heat-pump and EV profiles
  compute   hosting capacity, DOE, risk, scenarios and UI payloads
  compliance  audit trail and annual filing for a computed year
  forecast  what the published envelopes owe to perfect foresight
  redispatch-report  the annual report on non-market-based redispatch (EU 2019/943, art. 13.4)
  seals     verify a sealed chain's hashes and signatures, or rotate the signing key
  db        the PostgreSQL store: migrate, status, import-files, export-files
  check     data-quality report on an operator's network export
  import    build the engine's tables from such an export
  export-grid  write the built network in that same export contract
  serve     run the API and the web UI on one port, restarted on another network when asked
  dataset   build an imported network in its own folder (the app starts it)
  bundle    pack the computed data (and optionally web/dist) to run elsewhere
  unbundle  unpack such a bundle into data/ (and web/dist), checking every file
  study     a DSO's study of many secondary substations: init, demo, fetch, import, check, build, status
  all       fetch + build + profiles + compute
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("kapacitet")

# What `fetch --only` (and `all --only`) can name, in the order cmd_fetch downloads them.
FETCH_SOURCES = ("osm", "zones", "smhi", "esett", "scb", "prices")
# The extra stages live in engine/run.py (EXTRA_STAGES), so a new stage never needs this file.
# Importing the engine takes seconds, so their list is filled into the help only when it is shown.
_STAGES = "{stages}"


class _Parser(argparse.ArgumentParser):
    """An argument parser that writes the engine's stage names into a help text on demand."""

    def format_help(self) -> str:
        for action in self._actions:
            if action.help and _STAGES in action.help:
                from .engine import run

                action.help = action.help.replace(_STAGES, ", ".join(run.EXTRA_STAGES))
        return super().format_help()


def _workers(text: str) -> int:
    """--workers: a whole number of processes, at least one, refused before anything runs."""
    try:
        n = int(text)
    except ValueError:
        n = 0
    if n < 1:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of worker processes (1 or more)")
    return n


def _days(text: str) -> int:
    """--older-than: a whole number of days, zero or more."""
    try:
        n = int(text)
    except ValueError:
        n = -1
    if n < 0:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of days (0 or more)")
    return n


def _digest(text: str) -> str:
    """--expect-sha256: 64 hexadecimal digits, as sha256sum prints them."""
    if len(text.strip()) != 64 or any(c not in "0123456789abcdefABCDEF" for c in text.strip()):
        raise argparse.ArgumentTypeError(f"{text!r} is not a sha256 (64 hexadecimal digits)")
    return text.strip().lower()


def _names(text: str) -> set[str]:
    return {s.strip() for s in (text or "").split(",") if s.strip()}


def _check_sources(only: str) -> None:
    unknown = _names(only) - set(FETCH_SOURCES)
    if unknown:
        print(f"unknown source {', '.join(sorted(unknown))}: --only takes {','.join(FETCH_SOURCES)}")
        sys.exit(2)


@contextmanager
def step(name: str):
    t0 = time.perf_counter()
    log.info("▶ %s", name)
    yield
    log.info("✔ %s (%.1f s)", name, time.perf_counter() - t0)


def cmd_fetch(args: argparse.Namespace) -> None:
    _check_sources(args.only)
    from .config import settings
    from .ingest import esett, osm, prices, scb, smhi, zones

    cfg = settings()
    only = _names(args.only) or None
    want = (lambda k: only is None or k in only)
    refresh = args.refresh

    if want("osm"):
        with step("OpenStreetMap extract"):
            osm.fetch_osm(refresh=refresh)
    if want("zones"):
        with step("Bidding-zone geometries"):
            zones.fetch_zones(refresh=refresh)
    if want("smhi"):
        with step(f"SMHI STRÅNG irradiance {cfg.weather_years[0]}–{cfg.weather_years[-1]}"):
            for year in cfg.weather_years:
                smhi.fetch_strang(cfg.strang["ghi"], year, refresh=refresh)
                smhi.fetch_strang(cfg.strang["dni"], year, refresh=refresh)
            # Diffuse irradiance is only published recently; used to validate the closure.
            smhi.fetch_strang(cfg.strang["dhi"], cfg.reference_year, refresh=refresh)
        with step(f"SMHI temperature, station {cfg.temperature_station}"):
            smhi.fetch_metobs(cfg.temperature_station, refresh=refresh)
            # a study's meter readings are recent, and the corrected archive lags them by months
            if os.environ.get("KAPACITET_STUDY"):
                smhi.fetch_metobs_latest(cfg.temperature_station, refresh=refresh)
    if want("esett"):
        with step("eSett production/consumption per bidding zone"):
            for zone in cfg.price_zones:
                esett.fetch_production(zone, cfg.national_year, refresh=refresh)
                esett.fetch_consumption(zone, cfg.national_year, refresh=refresh)
            esett.fetch_load_profile(cfg.site.bidding_zone, cfg.site.metering_grid_area, cfg.reference_year,
                                     refresh=refresh)
    if want("scb"):
        # the household-size mix the simulated households are drawn from (profiles/household.py)
        with step("SCB household sizes (HushallT26)"):
            scb.fetch_household_sizes(refresh=refresh)
    if want("prices"):
        # every weather year, priced where a source has it: elprisetjustnu from its first whole year,
        # ENTSO-E before that when a token is set (the business case's range); the central years
        # ([prices] in site.toml) always come from elprisetjustnu
        zone_site = cfg.site.bidding_zone
        years = sorted(set(cfg.weather_years) | set(cfg.price_years))
        early = [y for y in years if prices.source_for_year(y) == "entsoe"]
        with step("Spot prices (elprisetjustnu.se)"):
            for year in years:
                if year not in early:
                    prices.fetch_elpris_year(zone_site, year, refresh=refresh)
            for zone in cfg.price_zones:
                prices.fetch_elpris_year(zone, cfg.national_year, refresh=refresh)
        if early and prices.entsoe_token():
            with step(f"ENTSO-E day-ahead prices {early[0]}–{early[-1]} (token found) and ECB EUR/SEK rates"):
                prices.fetch_ecb_eursek(early[0], early[-1], refresh=refresh)
                for year in early:
                    prices.fetch_entsoe_day_ahead(zone_site, year, refresh=refresh)
        elif early:
            log.warning("ENTSOE_API_TOKEN not set: skipping the ENTSO-E prices of %s–%s; "
                        "the business case's range covers the elprisetjustnu years only.", early[0], early[-1])


def cmd_build(args: argparse.Namespace) -> None:
    from .grid import builder

    with step("Synthetic LV network"):
        builder.build_and_save()


def cmd_profiles(args: argparse.Namespace) -> None:
    from .profiles import build

    with step("Profiles"):
        build.build_all(quick=args.quick)


def cmd_compute(args: argparse.Namespace) -> None:
    """The engine. `--workers` reaches both runs (six by default for the whole run, four for the
    extra stages); `--only` names extra stages, so it needs `--stage extra` rather than quietly
    running everything."""
    from .engine import run

    only = _names(getattr(args, "only", "")) or None
    extra = getattr(args, "stage", "all") == "extra"
    if only and not extra:
        print(f"--only needs --stage extra: python -m kapacitet compute --stage extra --only {','.join(sorted(only))}")
        sys.exit(2)
    if only and not only <= set(run.EXTRA_STAGES):
        print(f"--only takes: {', '.join(run.EXTRA_STAGES)}")
        sys.exit(2)
    workers = getattr(args, "workers", None)
    with step("Engine"):
        if extra:
            run.compute_extra(quick=args.quick, only=only, workers=4 if workers is None else workers)
        else:
            run.compute_all(quick=args.quick, workers=6 if workers is None else workers)


def cmd_bundle(args: argparse.Namespace) -> None:
    from .bundle import RELEASE, BundleError, make_bundle, pin_release

    try:
        m = make_bundle(args.out, level=args.level, web=args.web)
    except BundleError as err:
        print(err)
        sys.exit(2)
    size = os.path.getsize(args.out) / 1e6
    print(f"{args.out}: {len(m['files'])} files, {size:.0f} MB ({args.level}{', with web/dist' if args.web else ''}).")
    print("The data derive from OpenStreetMap (ODbL): never commit this file.")
    pin = RELEASE.relative_to(RELEASE.parents[1]).as_posix()
    if args.pin:
        rel = pin_release(args.out, m)
        print(f"Pinned it in {pin} (sha256 {rel['sha256']}, {rel['size']} bytes). Publish it on the data release:")
        print(f"  gh release upload data {args.out} --clobber")
        print(f"and commit {pin} in the same push: the codespace, `unbundle --sha256` and the setup page "
              "take only the pinned file.")
    else:
        print(f"Not pinned: the codespace, `unbundle --sha256` and the setup page refuse this file, so it is "
              f"not one to publish. To publish it, run again with --pin (it rewrites {pin}).")


def cmd_unbundle(args: argparse.Namespace) -> None:
    from .bundle import RELEASE, BundleError, pinned_release, unpack_bundle

    expected = args.expect_sha256
    if args.sha256:
        pin = pinned_release()
        if pin is None:
            print(f"no pinned data release in {RELEASE}: pass the digest itself, --expect-sha256 <hex>")
            sys.exit(2)
        expected = pin["sha256"]
    try:
        r = unpack_bundle(args.file, force=args.force, expected_sha256=expected)
    except BundleError as err:
        print(err)
        if err.code == "not_pinned":
            print(f"  expected  {err.params['expected']}")
            print(f"  this file {err.params['sha256']}")
        sys.exit(2)
    print(f"Unpacked {r['written']} files from a {r['level']} bundle made by Kapacitet+ {r['kapacitet']} "
          f"on {(r['created'] or '?')[:10]}" + (f"; kept {r['skipped']} existing files (use --force to replace them)" if r["skipped"] else "") + ".")
    print("Start the interface with: python -m kapacitet serve")


def cmd_serve(args: argparse.Namespace) -> None:
    """The server runs as a child of a small supervisor, which restarts it on another network when
    it exits with code 75 (datasets.RESTART). KAPACITET_SUPERVISE=0 runs it directly."""
    if args.child or os.environ.get("KAPACITET_SUPERVISE", "1") == "0":
        _serve_child(args)
        return
    sys.exit(supervise(args))


def _wait_for_port(host: str, port: int, seconds: float = 15.0) -> None:
    """A restarted server can find its port still held for a moment by the one it replaces."""
    import socket

    deadline = time.monotonic() + seconds
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return
            except OSError:
                if time.monotonic() > deadline:
                    return          # let uvicorn report it
        time.sleep(0.3)


def git_working_tree(path: Path) -> Path | None:
    """The git working tree `path` is inside, or None: the nearest folder, itself included, that
    holds a .git (a folder, or the file of a worktree or a submodule). Any repository counts, not
    only this project's: one it is vendored into, or another one KAPACITET_DATA_DIR points into.
    Walked by hand rather than asked of git, which the server does not need."""
    path = Path(path).resolve()
    for folder in (path, *path.parents):
        if (folder / ".git").exists():
            return folder
    return None


def data_in_checkout_warning() -> str | None:
    """What to say when an operator's network is kept inside a git working tree.

    An imported network is an operator's own data: inside a checkout only .gitignore keeps it out
    of a commit (a `git add -A` once committed one). Two cases are said:

    - the network in use is imported, served as a network of the app (datasets.current()) or
      written over the reference network by `kapacitet import` (grid/summary.json says
      `imported`), and its data folder is inside a working tree;
    - the folder of the app's networks (HOME/datasets) is inside one and holds any network, in
      use or not: each keeps the operator's four files in its import/.

    Without a git checkout (the container: the image has no .git, and it cannot tell where a
    mounted folder comes from) nothing is said."""
    from . import datasets
    from .paths import DATA, DATASETS, processed_ro

    imported = datasets.current() != datasets.REFERENCE
    if not imported:
        try:
            summary = json.loads(processed_ro("grid", "summary.json").read_text(encoding="utf-8"))
            imported = isinstance(summary, dict) and summary.get("imported") is True
        except (OSError, ValueError):
            pass
    places = [DATA] if imported and git_working_tree(DATA) is not None else []
    if git_working_tree(DATASETS) is not None and any(DATASETS.glob("*/dataset.json")):
        # a network of the app in use lives in it: one folder says both
        places = [p for p in places if not p.resolve().is_relative_to(DATASETS.resolve())] + [DATASETS]
    if not places:
        return None
    return (f"an operator's network is kept inside a git working tree ({', '.join(map(str, places))}): "
            "set KAPACITET_HOME and KAPACITET_DATA_DIR to a folder outside it, so that it "
            "cannot end up in a commit")


def _serve_child(args: argparse.Namespace) -> None:
    import uvicorn

    # here, not in the supervisor: the child runs on the network's own folder, once per switch
    warning = data_in_checkout_warning()
    if warning:
        log.warning(warning)
    _wait_for_port(args.host, args.port)
    uvicorn.run("kapacitet.api.main:app", host=args.host, port=args.port, reload=False, log_level="info")


def supervise(args: argparse.Namespace, command=None) -> int:
    """Run the server as a child and start it again, on the network active.json names, whenever it
    exits with datasets.RESTART. Ctrl+C and SIGTERM stop the child and the supervisor together.
    `command` builds the child's command line from its environment (a test's stand-in)."""
    import signal
    import subprocess

    from .datasets import RESTART, active, child_env

    state: dict = {"proc": None}

    def stop(signum, frame):  # noqa: ARG001
        proc = state["proc"]
        if proc is not None and proc.poll() is None:
            proc.terminate()
        raise SystemExit(0)

    for name in ("SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, stop)
            except (ValueError, OSError):
                pass
    while True:
        env = {**os.environ, "KAPACITET_SUPERVISED": "1", **child_env()}
        if command is not None:
            cmd = command(env)
        else:
            site = ["--site", args.site] if getattr(args, "site", None) else []
            cmd = [sys.executable, "-m", "kapacitet", *site, "serve", "--child", "--host", args.host, "--port", str(args.port)]
        proc = subprocess.Popen(cmd, env=env)
        state["proc"] = proc
        try:
            code = proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            proc.wait()
            return 0
        if code != RESTART:
            return code
        log.info("restarting the server on network %s", active())


def cmd_dataset(args: argparse.Namespace) -> None:
    """Build an imported network in this process (KAPACITET_DATA_DIR is its folder)."""
    from .datasets import DatasetError, build

    try:
        rec = build(args.id, workers=args.workers, quick=args.quick)
    except DatasetError as err:
        print(err)
        sys.exit(2)
    log.info("network %s: %s", args.id, rec["status"])
    sys.exit(0 if rec["status"] == "ready" else 1)


def cmd_purge(args: argparse.Namespace) -> None:
    """Remove the saved assessments older than N days and erase their seals (api/records.purge).
    A running server holds the data folder, and purges by itself once a day when
    KAPACITET_RETENTION_DAYS is set, so this refuses to run beside one."""
    from .api import records

    days = args.older_than if args.older_than is not None else records.retention_days()
    if days is None:
        print("say how old: --older-than N (days), or set KAPACITET_RETENTION_DAYS")
        sys.exit(2)
    try:
        lock = None if args.dry_run else records.claim_server_lock(wait_s=0)
    except records.FolderInUse as exc:
        print(f"{exc}. A running server purges by itself once a day when KAPACITET_RETENTION_DAYS is set.")
        sys.exit(2)
    try:
        out = records.purge(days, dry_run=args.dry_run)
    finally:
        records.release_server_lock(lock)
    verb = "would remove" if args.dry_run else "removed"
    log.info("%s %d assessments saved before %s (%d kept: a decision or an open application rests on them)",
             verb, out["assessments"], out["cutoff"][:10], out["kept_for_decisions"])


def cmd_all(args: argparse.Namespace) -> None:
    """`--only` here narrows the download; the compute that follows always runs whole."""
    _check_sources(args.only)
    cmd_fetch(args)
    cmd_build(args)
    cmd_profiles(args)
    cmd_compute(argparse.Namespace(**{**vars(args), "only": "", "stage": "all"}))


def cmd_import(args: argparse.Namespace) -> None:
    """Replace the built network with an operator's own, from an export directory."""
    from .importer.build import ImportError_, import_directory

    try:
        with step(f"Import network from {args.directory}"):
            network, report, fallbacks = import_directory(args.directory)
    except ImportError_ as err:
        print(err)
        sys.exit(1)
    print(report.text())
    log.info("Imported: %s", network.summary)
    for what, n in sorted(fallbacks.items()):
        log.warning("  fallback applied: %s (%d)", what, n)
    log.info("Next: python -m kapacitet profiles && python -m kapacitet compute")


def cmd_compliance(args: argparse.Namespace) -> None:
    """The audit trail and the annual filing for a computed year."""
    from .paths import processed

    path = processed("results", "compliance.txt")
    if not path.exists():
        print("No compliance report yet. Run: python -m kapacitet compute")
        sys.exit(1)
    print(path.read_text(encoding="utf-8"))
    trail = processed("results", "audit_trail.csv")
    if trail.exists():
        rows = sum(1 for _ in trail.open(encoding="utf-8")) - 1
        print(f"\nEvent-level audit trail: {rows} rows in {trail}")


def cmd_forecast(args: argparse.Namespace) -> None:
    """What the published envelopes owe to perfect foresight."""
    from .engine import forecast
    from .paths import processed

    if not processed("results", "showcase.json").exists():
        print("No showcase yet. Run: python -m kapacitet compute")
        sys.exit(1)
    with step("Issuing envelopes a day ahead"):
        rows, show, capacity = forecast.write()
    print(forecast.ladder_text(rows, show["year"], capacity))
    if capacity is None:
        print("\n(Capacity under each scheme needs: python -m kapacitet compute --stage extra)")


def cmd_redispatch_report(args: argparse.Namespace) -> None:
    """The year's non-market-based redispatch, on the day-ahead basis that is issued."""
    from .engine import redispatch
    from .paths import processed

    try:
        report = redispatch.write(args.year)
    except redispatch.RedispatchError as err:
        print(f"No redispatch report: {err}")
        sys.exit(1)
    print(redispatch.text_sv(report))
    print(f"Written: {processed('results', 'redispatch.json')} and redispatch_sv.txt")


def cmd_check(args: argparse.Namespace) -> None:
    """Data-quality report on a network export, before anybody promises a capacity."""
    from .importer import check_directory

    report = check_directory(args.directory)
    print(report.text())
    if not report.usable:
        sys.exit(1)


def cmd_export_grid(args: argparse.Namespace) -> None:
    """Write the built network in the export contract, as a worked example."""
    from .importer import export_grid

    with step(f"Export contract to {args.directory}"):
        tables = export_grid(args.directory)
    for name, df in tables.items():
        log.info("  %s.csv: %d rows", name, len(df))


def _chain_folder(path: str | None) -> Path | None:
    """The folder of a chain: the one given (or the folder of a seals.jsonl given), else
    data/requests; None for the server's chain when it is kept in the database."""
    from . import seals, store

    if not path:
        return None if store.is_pg() else seals.folder()
    where = Path(path)
    return where.parent if where.is_file() else where


def cmd_seals_verify(args: argparse.Namespace) -> None:
    """Check a chain's hashes and every signature; exit 1 when it does not hold."""
    from . import seals

    from contextlib import nullcontext

    folder = _chain_folder(args.path)
    if folder is not None and not (folder / "seals.jsonl").is_file():
        print(f"no chain in {folder} (seals.jsonl is missing)")
        sys.exit(2)
    trusted = None
    if args.key:
        trusted = {}
        for name in args.key:
            pub = seals.load_public(Path(name).read_bytes()) if Path(name).is_file() else None
            if pub is None:
                print(f"{name} is not an Ed25519 public key (PEM)")
                sys.exit(2)
            trusted[seals.key_id_of(pub)] = pub
    with seals.chain_at(folder) if folder is not None else nullcontext():
        where = seals._chain().where()
        out = seals.verify_chain(trusted, require_signed=args.require_signed)
    trust = {"given": "the key given", "installation": "this installation's keys",
             "folder": f"{seals.PUBLISHED} in the chain's own folder"}[out["trust"]]
    print(f"chain {where}: {out['length']} lines, head {out['head']}")
    print(f"  {out['signed']} signed, checked against {trust}; {out['legacy']} legacy (unsigned, from before signing began)")
    if out["keys"]:
        print("  keys: " + ", ".join(out["keys"]))
    if out["torn_tail"]:
        print("  the last line was cut short by a crash: the lines before it count, the next seal cuts it off")
    if "legacy_unsigned" in out["warnings"]:
        print("  warning: the legacy lines are held by the hash chain only")
    if "key_from_folder" in out["warnings"]:
        print(f"  warning: the key came from the chain's own folder; pass --key with the published {seals.PUBLISHED}")
    if not out["ok"]:
        why = {"chain": "the hash chain is broken", "signature": "a signature does not hold",
               "unknown_key": "signed by a key that is not trusted", "unsigned": "a line has lost its signature"}
        print(f"NOT VALID at line {out['broken_at']}: {why[out['reason']]}")
        sys.exit(1)
    print("valid (key unchecked: pass --key)" if out["trust"] == "folder" and out["signed"] else "valid")


def cmd_seals_rotate(args: argparse.Namespace) -> None:
    """Replace the signing key; the rotation record, signed by both keys, goes into the chain."""
    from . import seals

    try:
        out = seals.rotate_key(_chain_folder(args.chain) if args.chain else None)
    except seals.SealKeyError as exc:
        print(exc)
        sys.exit(2)
    print(f"key {out['old_key_id']} retired; signing now with {out['new_key_id']}")
    print(f"rotation sealed as line {out['entry']['seq']} of the chain; other chains get it on their next seal")


def cmd_db(args: argparse.Namespace) -> None:
    """The PostgreSQL store (kapacitet.store): its schema, and copies to and from data/requests."""
    from . import store

    if args.url:
        os.environ[store.ENV_URL] = args.url
    if args.scope:
        os.environ[store.ENV_SCOPE] = args.scope
    if not store.database_url():
        print("set KAPACITET_DATABASE_URL (or pass --url), e.g. postgresql://kapacitet@127.0.0.1:5433/kapacitet")
        sys.exit(2)
    from .store import migrate, transfer

    try:
        if args.action == "migrate":
            done = migrate.migrate()
            print(f"applied {', '.join(f'{v:03d}' for v in done)}" if done else "the schema is up to date")
        elif args.action == "status":
            out = migrate.status()
            for row in out["migrations"]:
                state = ("CHANGED since applied" if row["changed"] else f"applied {row['applied_at']}")                     if row["applied"] else "pending"
                print(f"{row['version']:03d}_{row['name']}: {state}")
            if out["unknown"]:
                print(f"applied but unknown to this code: {out['unknown']}")
            print(f"scope of this data folder: {store.scope()}")
            if out["pending"] or out["changed"]:
                sys.exit(1)
        elif args.action == "import-files":
            from .api import records

            root = Path(args.path) if args.path else records.folder()
            out = transfer.import_files(root)
            print(json.dumps(out, indent=1))
        else:
            if not args.path:
                print("export-files needs a folder to write into (empty or new)")
                sys.exit(2)
            print(json.dumps(transfer.export_files(Path(args.path)), indent=1))
    except (migrate.MigrationError, transfer.TransferError) as exc:
        print(exc)
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    parser = _Parser(prog="kapacitet", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", default=None, help="site key from config/site.toml, e.g. olovslund (default: the [site] table, Smedslätten)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="download external data")
    p.add_argument("--only", default="", help=f"comma list of sources: {','.join(FETCH_SOURCES)}")
    p.add_argument("--refresh", action="store_true", help="ignore the cache")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("build", help="build the synthetic LV network")
    p.set_defaults(func=cmd_build)

    for name, func in (("profiles", cmd_profiles), ("compute", cmd_compute)):
        p = sub.add_parser(name)
        p.add_argument("--quick", action="store_true", help="fewer years/orders for a fast run")
        if name == "compute":
            p.add_argument("--stage", choices=["all", "extra"], default="all",
                           help=f"extra: only the stages that build on the showcase ({_STAGES})")
            p.add_argument("--only", default="", help=f"with --stage extra: comma list of {_STAGES}")
            p.add_argument("--workers", type=_workers, default=None,
                           help="worker processes for the parallel stages (default 6, or 4 with --stage extra)")
        p.set_defaults(func=func)

    p = sub.add_parser("import", help="build the engine's tables from a network export")
    p.add_argument("directory", help="directory holding the contract CSV files")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("compliance", help="audit trail and annual filing for a computed year")
    p.set_defaults(func=cmd_compliance)

    p = sub.add_parser("forecast", help="what the published envelopes owe to perfect foresight")
    p.set_defaults(func=cmd_forecast)

    p = sub.add_parser("redispatch-report", help="the annual report on non-market-based redispatch "
                                                 "(EU 2019/943, art. 13.4), on the day-ahead basis")
    p.add_argument("--year", type=int, default=None, help="the report year (default: the operation stage's)")
    p.set_defaults(func=cmd_redispatch_report)

    p = sub.add_parser("purge", help="remove saved assessments older than N days, and erase their seals")
    p.add_argument("--older-than", type=_days, default=None, metavar="N",
                   help="days to keep (default: KAPACITET_RETENTION_DAYS)")
    p.add_argument("--dry-run", action="store_true", help="say what would go, and remove nothing")
    p.set_defaults(func=cmd_purge)

    p = sub.add_parser("seals", help="check the sealed chain's hashes and signatures, or rotate the signing key")
    seals_sub = p.add_subparsers(dest="seals_command", required=True)
    q = seals_sub.add_parser("verify", help="check the hash chain and every signature")
    q.add_argument("path", nargs="?", default=None,
                   help="a chain's folder or its seals.jsonl (default: data/requests)")
    q.add_argument("--key", action="append", default=None, metavar="PUB",
                   help="a published public key (seal_key.pub) to trust instead of this installation's; repeatable")
    q.add_argument("--require-signed", action="store_true", help="fail on legacy unsigned lines too")
    q.set_defaults(func=cmd_seals_verify)
    q = seals_sub.add_parser("rotate-key", help="replace the signing key, with a record signed by the old and the new")
    q.add_argument("--chain", default=None, help="the chain that records the rotation (default: data/requests)")
    q.set_defaults(func=cmd_seals_rotate)

    p = sub.add_parser("db", help="the PostgreSQL store: schema migrations and copies to and from data/requests")
    p.add_argument("action", choices=["migrate", "status", "import-files", "export-files"])
    p.add_argument("path", nargs="?", default=None,
                   help="import-files: the folder to copy (default: data/requests); export-files: an empty or new folder")
    p.add_argument("--url", default=None, help="the database (default: KAPACITET_DATABASE_URL)")
    p.add_argument("--scope", default=None, help="the data folder's key in the database (default: from the data folder)")
    p.set_defaults(func=cmd_db)

    p = sub.add_parser("check", help="data-quality report on a network export")
    p.add_argument("directory", help="directory holding buses.csv, lines.csv, transformer.csv, customers.csv")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("export-grid", help="write the built network in the export contract")
    p.add_argument("directory", help="directory to write the CSV files into")
    p.set_defaults(func=cmd_export_grid)

    p = sub.add_parser("serve", help="serve API + web UI")
    p.add_argument("--host", default=os.environ.get("KAPACITET_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("KAPACITET_PORT", "8000")))
    p.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("dataset", help="build an imported network (started by the app)")
    p.add_argument("action", choices=["build"])
    p.add_argument("id", help="the network's id (data/datasets/<id>)")
    p.add_argument("--workers", type=_workers, default=2, help="worker processes for the parallel stages")
    p.add_argument("--quick", action="store_true", help="one weather year and fewer adoption orders")
    p.set_defaults(func=cmd_dataset)

    p = sub.add_parser("bundle", help="pack the computed data to run it elsewhere")
    p.add_argument("--out", default="kapacitet-data.zip", help="archive to write")
    p.add_argument("--level", choices=["lite", "full"], default="full",
                   help="lite: the published results only; full: also what live requests need")
    p.add_argument("--web", action="store_true", help="include web/dist, so the receiver needs no Node")
    p.add_argument("--pin", action="store_true",
                   help="record the archive's sha256 and size in config/data-release.json, as the published release")
    p.set_defaults(func=cmd_bundle)

    p = sub.add_parser("unbundle", help="unpack a data bundle into data/ (and web/dist)")
    p.add_argument("file", help="the bundle (a .zip made by `kapacitet bundle`)")
    p.add_argument("--force", action="store_true", help="replace files that already exist")
    pin = p.add_mutually_exclusive_group()
    pin.add_argument("--sha256", action="store_true",
                     help="refuse the file unless it is the pinned data release (config/data-release.json)")
    pin.add_argument("--expect-sha256", default=None, metavar="HEX", type=_digest,
                     help="refuse the file unless its sha256 is HEX")
    p.set_defaults(func=cmd_unbundle)

    from .study.cli import register as register_study    # `study ...` and the hidden `station build`
    register_study(sub)

    p = sub.add_parser("all", help="fetch + build + profiles + compute")
    p.add_argument("--only", default="",
                   help=f"fetch only these sources (comma list of {','.join(FETCH_SOURCES)}); the rest still runs whole")
    p.add_argument("--refresh", action="store_true", help="ignore the download cache")
    p.add_argument("--quick", action="store_true", help="fewer years/orders for a fast run")
    p.add_argument("--workers", type=_workers, default=None, help="worker processes for the parallel stages (default 6)")
    p.set_defaults(func=cmd_all)

    # the commands as `--help` lists them: the hidden ones (help=SUPPRESS) left out
    hidden = {a.dest for a in sub._choices_actions if a.help == argparse.SUPPRESS}
    sub.metavar = "{" + ",".join(name for name in sub.choices if name not in hidden) + "}"

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S",
                        stream=sys.stdout)
    if args.site:
        os.environ["KAPACITET_SITE"] = args.site
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    args.func(args)


if __name__ == "__main__":
    main()
