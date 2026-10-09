"""Command-line entry point: python -m kapacitet <command>.

Commands
  fetch     download and cache all external data (with provenance manifest)
  build     build the synthetic LV network on the OSM neighbourhood
  profiles  build hourly weather, PV, household, heat-pump and EV profiles
  compute   hosting capacity, DOE, risk, scenarios and UI payloads
  check     data-quality report on an operator's network export
  import    build the engine's tables from such an export
  export-grid  write the built network in that same export contract
  serve     run the API and the web UI on one port
  all       fetch + build + profiles + compute
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from contextlib import contextmanager

log = logging.getLogger("kapacitet")


@contextmanager
def step(name: str):
    t0 = time.perf_counter()
    log.info("▶ %s", name)
    yield
    log.info("✔ %s (%.1f s)", name, time.perf_counter() - t0)


def cmd_fetch(args: argparse.Namespace) -> None:
    from .config import settings
    from .ingest import esett, osm, prices, smhi, zones

    cfg = settings()
    only = set(args.only.split(",")) if args.only else None
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
    if want("esett"):
        with step("eSett production/consumption per bidding zone"):
            for zone in cfg.price_zones:
                esett.fetch_production(zone, cfg.national_year, refresh=refresh)
                esett.fetch_consumption(zone, cfg.national_year, refresh=refresh)
            esett.fetch_load_profile(cfg.site.bidding_zone, cfg.site.metering_grid_area, cfg.reference_year,
                                     refresh=refresh)
    if want("prices"):
        with step("Spot prices (elprisetjustnu.se)"):
            for year in cfg.price_years:
                prices.fetch_elpris_year(cfg.site.bidding_zone, year, refresh=refresh)
            for zone in cfg.price_zones:
                prices.fetch_elpris_year(zone, cfg.national_year, refresh=refresh)
        if prices.entsoe_token():
            with step("ENTSO-E day-ahead prices (token found)"):
                for year in cfg.weather_years:
                    if year < cfg.price_years[0]:
                        prices.fetch_entsoe_day_ahead(cfg.site.bidding_zone, year, refresh=refresh)


def cmd_build(args: argparse.Namespace) -> None:
    from .grid import builder

    with step("Synthetic LV network"):
        builder.build_and_save()


def cmd_profiles(args: argparse.Namespace) -> None:
    from .profiles import build

    with step("Profiles"):
        build.build_all(quick=args.quick)


def cmd_compute(args: argparse.Namespace) -> None:
    from .engine import run

    with step("Engine"):
        if getattr(args, "stage", "all") == "extra":
            run.compute_extra(quick=args.quick)
        else:
            run.compute_all(quick=args.quick)


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run("kapacitet.api.main:app", host=args.host, port=args.port, reload=False, log_level="info")


def cmd_all(args: argparse.Namespace) -> None:
    cmd_fetch(args)
    cmd_build(args)
    cmd_profiles(args)
    cmd_compute(args)


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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="kapacitet", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", default=None, help="site key from config/site.toml, e.g. olovslund (default: the [site] table, Smedslätten)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="download external data")
    p.add_argument("--only", default="", help="comma list: osm,zones,smhi,esett,prices")
    p.add_argument("--refresh", action="store_true", help="ignore the cache")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("build", help="build the synthetic LV network")
    p.set_defaults(func=cmd_build)

    for name, func in (("profiles", cmd_profiles), ("compute", cmd_compute)):
        p = sub.add_parser(name)
        p.add_argument("--quick", action="store_true", help="fewer years/orders for a fast run")
        if name == "compute":
            p.add_argument("--stage", choices=["all", "extra"], default="all",
                           help="extra: only request baselines, 2030 scenarios, tornado and validation")
        p.set_defaults(func=func)

    p = sub.add_parser("import", help="build the engine's tables from a network export")
    p.add_argument("directory", help="directory holding the contract CSV files")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("check", help="data-quality report on a network export")
    p.add_argument("directory", help="directory holding buses.csv, lines.csv, transformer.csv, customers.csv")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("export-grid", help="write the built network in the export contract")
    p.add_argument("directory", help="directory to write the CSV files into")
    p.set_defaults(func=cmd_export_grid)

    p = sub.add_parser("serve", help="serve API + web UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("all", help="fetch + build + profiles + compute")
    p.add_argument("--only", default="")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--quick", action="store_true")
    p.set_defaults(func=cmd_all)

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
