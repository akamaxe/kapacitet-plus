"""One station's build, step by step, in the process `kapacitet station build <sid> --study <dir>`.

The process runs with KAPACITET_DATA_DIR on the station's folder, KAPACITET_HOME on the study,
KAPACITET_RAW_DIR on the study's downloads and KAPACITET_STUDY on the study (portfolio.py sets
them), so every step is the existing pipeline's and writes only into `stations/<sid>/`:

    import      the station's contract folder -> processed/grid (importer.build)
    weather     the study's SMHI downloads -> processed/weather, when the study names a place of
                its own and fetched them for it; otherwise the reference network's weather years,
                copied and said so under provenance "synthetic"
    households  the reference network's household profile pool (CREST), linked, or scaled to the
                study's household_kwh_per_year when it overrides it
    profiles    PV, heat pumps and EV charging on that weather (profiles.build)
    compute     static, hourly and envelope capacity per adoption order, and the nodal maps, with
                the engine's own stages in this process (workers = 1); provisional, since the
                study's MV band and [method] are not applied yet

Nothing a later step rewrites is ever a link to the reference network's files: a write to a
linked file is a write to the original. Each step's outcome, the inputs' digests and what is real,
synthetic or assumed go into station.json (`kapacitet.station/1`). A failing step marks only this
station.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import warnings
from pathlib import Path
from typing import Callable

from .. import paths, procs
from . import PROVENANCE, SCHEMA, Study, StudyError, code_version
from .config import METHOD_VERSION

log = logging.getLogger("kapacitet.study")
STEPS = ("import", "weather", "households", "profiles", "compute")
# what hosting_summary.json says about a station's figures until the study's method is applied
PROVISIONAL = {"method": "provisional", "not_applied": ["mv_band", "percentile", "budget", "consumption_device"]}


def isolated(study: Study, sid: str) -> bool:
    """This process reads and writes only the station's folder and the study's downloads."""
    want = study.station_dir(sid).resolve()
    return (paths.DATA == want and paths.HOME == study.root.resolve() and paths.RAW == study.raw.resolve()
            and Path(os.environ.get("KAPACITET_STUDY", "")).resolve() == study.root.resolve())


def build(study: Study, sid: str, quick: bool = False,
          steps: dict[str, Callable[[], dict | None]] | None = None) -> dict:
    """Run the station's steps, recording each one; `steps` replaces the real ones (a test's fakes)."""
    rec = study.record(sid)
    if study.building(sid):
        raise StudyError("building", id=sid)
    pinned = study.config.dso.method_version
    if pinned != METHOD_VERSION:
        raise StudyError("method_version", pinned=pinned, current=METHOD_VERSION)
    if steps is None and not isolated(study, sid):
        raise StudyError("not_isolated", id=sid, study=str(study.root))
    if steps is None:
        from ..config import set_overrides

        set_overrides(study.config.overrides(), layer="study")
    rec.update(schema=SCHEMA, inputs=study.digests(sid), method_version=METHOD_VERSION,
               kapacitet=code_version(), quick=quick, pid=os.getpid(), error=None,
               provenance={k: [] for k in PROVENANCE}, summary=rec.get("summary") or {})
    steps = steps or _real_steps(study, sid, rec, quick)
    out = procs.run_steps(rec, STEPS, steps, study.save, procs.lock_path(study.station_dir(sid)),
                          logger="kapacitet.study", what="station build")
    out["pid"] = None
    study.save(out)
    return out


# ---------------------------------------------------------------- where the profiles come from

def weather_source(study: Study) -> dict:
    """Where a station's weather years come from, as its process will see it (and as the parent
    digests it): the study's own SMHI downloads when it names a place and fetched every year for
    that very point, else the reference network's weather files. {"source": "study" | "reference",
    "years", "files": [Path], "point": (lat, lon) of the STRÅNG request, "reason" when reference}."""
    from ..config import study_settings

    cfg = study_settings(study.config, study.root)
    years = cfg.weather_years
    if study.config.site.given:
        found = _study_weather(cfg, study.raw)
        if found is not None:
            files, point = found
            return {"source": "study", "years": years, "files": files, "point": point,
                    "station": cfg.temperature_station}
        reason = "the study has not fetched its own (kapacitet study fetch)"
    else:
        reason = "the study names no place of its own ([site] lat, lon and temperature_station)"
    ref = paths.REFERENCE / "processed" / "weather"
    return {"source": "reference", "years": years, "files": [ref / f"weather_{y}.parquet" for y in years],
            "reason": reason}


def _study_weather(cfg, raw: Path) -> tuple[list[Path], tuple[float, float]] | None:
    """The study's downloads behind every weather year, when each is there and its manifest entry
    says it was fetched for the study's STRÅNG point; else None."""
    from ..ingest import smhi

    manifest_path = raw / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    except (OSError, ValueError):
        return None
    lat, lon = cfg.site.strang_point
    key = cfg.site.key
    files, point = [], None
    for y in cfg.weather_years:
        for p in ("ghi", "dni"):
            param = cfg.strang[p]
            f = raw / "smhi" / smhi.strang_name(key, param, y)
            entry = manifest.get(smhi.strang_entry(key, param, y)) or {}
            if not f.is_file() or entry.get("url") != smhi.strang_url(param, lat, lon):
                return None
            files.append(f)
            point = point or smhi.point_of(entry["url"])
    f = raw / "smhi" / smhi.metobs_name(cfg.temperature_station)
    if not f.is_file() or smhi.metobs_entry(cfg.temperature_station) not in manifest:
        return None
    return files + [f], point


def households_source() -> Path:
    """The reference network's household profile pool, which every station starts from."""
    return paths.REFERENCE / "processed" / "households"


def _clear(folder: Path) -> None:
    """Remove the files of `folder` (an earlier build's, links included) so nothing is written
    through a link into the folder it points to."""
    folder.mkdir(parents=True, exist_ok=True)
    for f in folder.iterdir():
        if f.is_file() or f.is_symlink():
            f.unlink()


def place_weather(study: Study, note: Callable[[str, str], None]) -> dict:
    """The weather step: processed/weather from the study's downloads, or copies of the reference
    network's weather years (never links: building the study's own would write through them)."""
    src = weather_source(study)
    years = src["years"]
    folder = paths.PROCESSED / "weather"
    _clear(folder)
    span = f"{years[0]}–{years[-1]}"
    if src["source"] == "study":
        from ..profiles.weather import build_weather

        build_weather(years)
        lat, lon = src["point"]
        note("real", f"weather: SMHI STRÅNG at {lat:.4f}, {lon:.4f} (as fetched) and metobs station "
                     f"{src['station']}, {span}")
        return {"source": "study", "years": list(years)}
    missing = [y for y, f in zip(years, src["files"]) if not f.is_file()]
    if missing:
        raise RuntimeError(f"no weather for {missing[0]}{'–' + str(missing[-1]) if len(missing) > 1 else ''}: "
                           f"run `kapacitet study fetch {study.root}`")
    for f in src["files"]:
        shutil.copyfile(f, folder / f.name)
    note("synthetic", f"weather: the reference site's SMHI years {span}, reused because {src['reason']}")
    return {"source": "reference", "years": list(years)}


POOL_ARRAYS = ("pool_hourly_{y}.npy", "pool_10min_{y}.npy")
POOL_META_SCALED = ("annual_kwh", "peak_hourly_kw", "peak_10min_kw", "peak_1min_kw")


def place_households(note: Callable[[str, str], None]) -> dict:
    """The households step: the reference pool, linked as it is (no step rewrites it), or written
    scaled when the study's household_kwh_per_year differs from the mean it was scaled to (the
    pool is generated by scaling, so scaling it again is what generating it anew would give)."""
    import numpy as np
    import pandas as pd

    from ..config import A, assumptions
    from ..profiles.household import POOL_YEAR

    src = households_source()
    if not (src / f"pool_hourly_{POOL_YEAR}.npy").is_file():
        raise RuntimeError("no household profile pool to reuse (the reference network's "
                           "processed/households is empty)")
    dst = paths.PROCESSED / "households"
    _clear(dst)
    want = float(A("household_kwh_per_year"))
    info_file = src / "pool_info.json"
    info = json.loads(info_file.read_text(encoding="utf-8")) if info_file.is_file() else {}
    have = info.get("scale_to_mean_kwh")
    if have is None:
        if want != float(assumptions()["household_kwh_per_year"].value):
            raise RuntimeError("the reference household pool does not say what mean it was scaled to "
                               "(pool_info.json), so it cannot be scaled to the study's household_kwh_per_year")
        have = want
    have = float(have)
    if math.isclose(want, have, rel_tol=1e-9):
        n = procs.link_files(src, dst)
        note("synthetic", "household load: the CREST profile pool (richardsonpy), not metered")
        return {"files": n}
    factor = want / have
    arrays = {name.format(y=POOL_YEAR) for name in POOL_ARRAYS}
    for f in sorted(src.iterdir()):
        if not f.is_file():
            continue
        if f.name in arrays:
            np.save(dst / f.name, (np.load(f) * factor).astype(np.float32))
        elif f.name == "pool_meta.parquet":
            meta = pd.read_parquet(f)
            for col in POOL_META_SCALED:
                if col in meta.columns:
                    meta[col] = meta[col] * factor
            meta.to_parquet(dst / f.name, index=False)
        elif f.name == "pool_info.json":
            out = dict(info, scale_to_mean_kwh=want, rescaled_from_mean_kwh=have,
                       scale_factor=float(info.get("scale_factor", 1.0)) * factor)
            (dst / f.name).write_text(json.dumps(out, indent=1), encoding="utf-8")
        else:
            shutil.copyfile(f, dst / f.name)
    note("synthetic", f"household load: the CREST profile pool (richardsonpy), not metered, scaled from a "
                      f"mean of {have:g} to the study's {want:g} kWh/yr")
    return {"files": sum(1 for f in dst.iterdir() if f.is_file()), "scale": round(factor, 6)}


def network_notes(origin: dict, report) -> list[tuple[str, str]]:
    """What the station's network and its phases are, for the provenance: synthetic when its
    origin.json says so, the DSO's when one declares it, and unverified (assumed) without one."""
    out: list[tuple[str, str]] = []
    if origin.get("synthetic"):
        kind = "synthetic"
        out.append((kind, f"network: {origin.get('generator') or origin.get('format')}, not a DSO's records"))
        out += [(kind, part) for part in origin.get("synthetic_parts") or []]
    elif origin.get("declared"):
        kind = "real"
        out.append((kind, f"network: the DSO's export ({origin.get('format', 'contract')})"))
    else:
        kind = "assumed"
        out.append((kind, "network: taken to be the DSO's export, unverified: no origin.json beside its files "
                          "says where it came from"))
    codes = {f.code for f in report.findings}
    if codes & {"phase_not_recorded", "phase_not_recorded_cost"}:
        out.append(("assumed", "phases: not recorded, so every service is taken as three-phase"))
    else:
        out.append((kind, "phases: as recorded in customers.csv (a blank one is three-phase)"))
    return out


# ---------------------------------------------------------------- the real steps

def _years(quick: bool) -> tuple[int, ...]:
    from ..config import settings

    cfg = settings()
    return cfg.weather_years if not quick else (cfg.reference_year,)


def _real_steps(study: Study, sid: str, rec: dict, quick: bool) -> dict:
    prov = rec["provenance"]

    def note(kind: str, text: str) -> None:
        if text not in prov[kind]:
            prov[kind].append(text)

    def do_import():
        from ..importer.build import import_directory
        from .intake import quality

        network, report, applied = import_directory(study.station_dir(sid) / "import")
        s = network.summary
        rec["quality"] = quality(report)
        rec["summary"] = {"customers": s["customers"], "feeders": s["feeders"], "buses": s["buses"],
                          "lines": s["lines"], "trafo_kva": round(float(network.substation["trafo_sn_mva"]) * 1000.0, 1),
                          "existing_pv_kw": None}
        for kind, text in network_notes(rec.get("origin") or {}, report):
            note(kind, text)
        for what, n in sorted(applied.items()):
            note("assumed", f"{what} ({n})")
        note("assumed", "existing equipment: no inventory, so no existing PV, battery or charger is counted")
        return {"summary": rec["summary"], "fallbacks": applied}

    def do_profiles():
        from ..profiles import build as profiles

        # every weather year, quick or not: the static study takes its minimum load over all of
        # them, and the profiles are the cheap part; "quick" trims the computation that follows
        profiles.build_all(quick=False, weather=False)
        note("synthetic", "PV production: pvlib on the roof areas and the weather above")
        note("synthetic", "heat pumps and EV charging: simulated profiles")
        note("assumed", "heat pumps and EVs today: the shares of config/assumptions.toml "
                        "(today_hp_share, today_ev_share)")
        return {}

    def do_compute():
        mv, method = study.config.mv, study.config.method
        note("assumed", f"MV voltage: the engine's 1.00 pu; the study's band {mv.band_low_pu:.2f}–"
                        f"{mv.band_high_pu:.2f} pu ({mv.source}) is not applied by this build")
        note("assumed", f"method: the median of the adoption orders and the aggregate curtailment budget; "
                        f"the study's percentile = {method.percentile:g}, budget = {method.budget} and "
                        f"consumption_device = {method.consumption_device} are not applied by this build, "
                        "whose figures are provisional")
        for key, o in study.config.assumptions.items():
            note("assumed", f"{key} = {o.value} {o.unit} ({o.source})")
        return compute(quick)

    return {"import": do_import, "weather": lambda: place_weather(study, note),
            "households": lambda: place_households(note), "profiles": do_profiles, "compute": do_compute}


def compute(quick: bool = False) -> dict:
    """Capacity per adoption order with the engine's own first stages (engine.run.capacity), in
    this process: results/nodal.json and results/hosting_summary.json, the summary marked
    provisional until the study's MV band and [method] are applied."""
    from ..config import A
    from ..engine import run

    warnings.filterwarnings("ignore")
    years = _years(quick)
    n_orders = run.N_ORDERS if not quick else 4
    _per_order, summary = run.capacity(years, n_orders, A("z_star_pct"), workers=1, extra=PROVISIONAL)
    measures = summary["measures"]
    return {"years": list(years), "orders": n_orders,
            "median_kw_ac": {k: round(measures[k]["kw_ac"]["median"], 1) for k in measures}}
