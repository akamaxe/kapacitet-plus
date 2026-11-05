"""One station's build, step by step, in the process `kapacitet station build <sid> --study <dir>`.

The process runs with KAPACITET_DATA_DIR on the station's folder, KAPACITET_HOME on the study,
KAPACITET_RAW_DIR on the study's downloads and KAPACITET_STUDY on the study (portfolio.py sets
them), so every step is the existing pipeline's and writes only into `stations/<sid>/`:

    import      the station's contract folder -> processed/grid (importer.build)
    weather     the study's SMHI downloads -> processed/weather; when the study fetched none, the
                reference network's weather years, said so under provenance "synthetic"
    households  the reference network's household profile pool (CREST), linked or copied
    profiles    PV, heat pumps and EV charging on that weather (profiles.build)
    compute     static, hourly and envelope capacity per adoption order, and the nodal maps, with
                the engine's existing functions in this process (workers = 1)

Each step's outcome, the inputs' digests and what is real, synthetic or assumed go into
station.json (`kapacitet.station/1`). A failing step marks only this station.
"""

from __future__ import annotations

import logging
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


# ---------------------------------------------------------------- the real steps

def _years(quick: bool) -> tuple[int, ...]:
    from ..config import settings

    cfg = settings()
    return cfg.weather_years if not quick else (cfg.reference_year,)


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.unlink(missing_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


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
        origin = rec.get("origin") or {}
        if origin.get("synthetic"):
            note("synthetic", f"network: {origin.get('generator') or origin.get('format')}, not a DSO's records")
            for part in origin.get("synthetic_parts") or []:
                note("synthetic", part)
        else:
            note("real", f"network: the DSO's export ({origin.get('format', 'contract')})")
        for what, n in sorted(applied.items()):
            note("assumed", f"{what} ({n})")
        note("assumed", "existing equipment: no inventory, so no existing PV, battery or charger is counted")
        return {"summary": rec["summary"], "fallbacks": applied}

    def do_weather():
        from ..config import settings

        cfg = settings()
        years = cfg.weather_years                # the static study takes its minimum load over all
        if _study_weather(cfg, years):
            from ..profiles.weather import build_weather

            build_weather(years)
            lat, lon = cfg.site.strang_point
            note("real", f"weather: SMHI STRÅNG at {lat:.4f}, {lon:.4f} and metobs station "
                         f"{cfg.temperature_station}, {years[0]}–{years[-1]}")
            return {"source": "study", "years": list(years)}
        ref = paths.REFERENCE / "processed" / "weather"
        missing = [y for y in years if not (ref / f"weather_{y}.parquet").is_file()]
        if missing:
            raise RuntimeError(f"no weather for {missing[0]}"
                               f"{'–' + str(missing[-1]) if len(missing) > 1 else ''}: run `kapacitet study fetch "
                               f"{study.root}`")
        for y in years:
            _link_or_copy(ref / f"weather_{y}.parquet", paths.PROCESSED / "weather" / f"weather_{y}.parquet")
        note("synthetic", f"weather: the reference site's SMHI years {years[0]}–{years[-1]}, reused because "
                          "the study has fetched none for its own site")
        return {"source": "reference", "years": list(years)}

    def do_households():
        from ..profiles.household import POOL_YEAR

        src = paths.REFERENCE / "processed" / "households"
        if not (src / f"pool_hourly_{POOL_YEAR}.npy").is_file():
            raise RuntimeError("no household profile pool to reuse (the reference network's "
                               "processed/households is empty)")
        n = 0
        for f in sorted(src.iterdir()):
            if f.is_file():
                _link_or_copy(f, paths.PROCESSED / "households" / f.name)
                n += 1
        note("synthetic", "household load: the CREST profile pool (richardsonpy), not metered")
        return {"files": n}

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
        mv = study.config.mv
        note("assumed", f"MV voltage: the engine's 1.00 pu; the study's band {mv.band_low_pu:.2f}–"
                        f"{mv.band_high_pu:.2f} pu ({mv.source}) is not applied by this build")
        for key, o in study.config.assumptions.items():
            note("assumed", f"{key} = {o.value} {o.unit} ({o.source})")
        return compute(quick)

    return {"import": do_import, "weather": do_weather, "households": do_households,
            "profiles": do_profiles, "compute": do_compute}


def _study_weather(cfg, years) -> bool:
    """The study's own downloads hold irradiance and temperature for every weather year."""
    smhi = paths.RAW / "smhi"
    key = cfg.site.key
    need = [smhi / f"strang_{key}_p{cfg.strang[p]}_{y}.json" for y in years for p in ("ghi", "dni")]
    need.append(smhi / f"metobs_s{cfg.temperature_station}_p1_corrected.csv")
    return all(p.is_file() for p in need)


def compute(quick: bool = False) -> dict:
    """Capacity per adoption order with the engine's existing functions, in this process:
    results/nodal.json and results/hosting_summary.json, in the shape `compute` writes them."""
    import numpy as np

    from ..config import A
    from ..engine import doe, hosting, run
    from ..engine.scenario import customers

    warnings.filterwarnings("ignore")
    years = _years(quick)
    n_orders = run.N_ORDERS if not quick else 4
    z_star = A("z_star_pct")
    m = run.model()
    hp, ev = run.background_masks(m.C)
    cust = customers()
    nodal_pv = hosting.nodal_pv_hc(m)
    nodal_imp = hosting.nodal_import_hc(m, hp, ev)
    run._write("nodal.json", {
        "customers": cust["cid"].tolist(), "pv_kw": np.round(nodal_pv, 2), "import_kw": np.round(nodal_imp, 2),
        "fuse_kva": cust["fuse_kva"].tolist(), "cap_kw": np.round(doe.customer_caps(), 2),
        "winter_design": {k: v for k, v in hosting.winter_design_load().items()
                          if k in ("year", "hour", "t_air", "peak_kw")},
    })
    per_order = [run.analyze_order((o, tuple(years), z_star)) for o in range(n_orders)]

    def stats(key: str, field: str) -> dict:
        v = np.array([r[key][field] for r in per_order], dtype=float)
        return {"median": float(np.median(v)), "p10": float(np.percentile(v, 10)),
                "p90": float(np.percentile(v, 90)), "min": float(v.min()), "max": float(v.max())}

    measures = {k: {"kw_ac": stats(k, "kw_ac"), "kwp": stats(k, "kwp"), "adopters": stats(k, "adopters")}
                for k in ("static", "timeseries", "fixed_cap", "doe")}
    run._write("hosting_summary.json", {
        "z_star_pct": z_star, "years": list(years), "n_orders": n_orders, "customers": m.C,
        "total_cap_kw": float(doe.customer_caps().sum()), "total_kwp": float(cust["kwp"].sum()),
        "measures": measures, "per_order": per_order,
    })
    return {"years": list(years), "orders": n_orders,
            "median_kw_ac": {k: round(measures[k]["kw_ac"]["median"], 1) for k in measures}}
