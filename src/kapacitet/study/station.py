"""One station's build, step by step, in the process `kapacitet station build <sid> --study <dir>`.

The process runs with KAPACITET_DATA_DIR on the station's folder, KAPACITET_HOME on the study,
KAPACITET_RAW_DIR on the study's downloads and KAPACITET_STUDY on the study (portfolio.py sets
them), so every step is the existing pipeline's and writes only into `stations/<sid>/`:

    import      the station's contract folder -> processed/grid (importer.build)
    equipment   what is already connected behind its meters -> the existing_* columns of
                processed/grid/customers.parquet: the station's rows of the study's
                inputs/equipment.csv, or the equipment.csv of its own contract folder
    meters      its customers' readings in the study's inputs/meters/ -> processed/meters
                (ingest/meters.py: checked, and summed to complete hours)
    phases      each meter's phase from its voltages (ingest/phase_id.py) -> processed/meters/
                phase_id.parquet, against the transformer secondary meter's v_l1..v_l3 in
                inputs/mv/<sid>.csv when given; the identified phases at or above
                phase_id_min_confidence go into customers.parquet where the DSO recorded none
                (and over a recorded one only with --prefer-identified)
    weather     the study's SMHI downloads -> processed/weather, when the study names a place of
                its own and fetched them for it; otherwise the reference network's weather years,
                copied and said so under provenance "synthetic"
    households  the reference network's household profile pool (CREST), linked, or scaled to the
                study's household_kwh_per_year when it overrides it
    load        the metered customers' load, temperature-corrected and laid over every weather
                year (profiles/metered.py), in place of the pool's profile for them
    profiles    PV, heat pumps and EV charging on that weather (profiles.build)
    compute     Module 1 (engine/station.py): the generation capacity at both ends of the MV band,
                the lower one published; the consumption capacity at the low end; the weakest
                connection point; the peak check; the "existing" request baseline at each end of
                the band (what `study cases` checks against); and the upstream caps applied
                (study/upstream.py), into processed/results/capacity.json

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
from . import PROVENANCE, SCHEMA, Study, StudyError, code_version, value_digest
from .config import METHOD_VERSION

log = logging.getLogger("kapacitet.study")
STEPS = ("import", "equipment", "meters", "phases", "weather", "households", "load", "profiles", "compute")
POOL_NOTE = "household load: the CREST profile pool (richardsonpy), not metered"


def isolated(study: Study, sid: str) -> bool:
    """This process reads and writes only the station's folder and the study's downloads."""
    want = study.station_dir(sid).resolve()
    return (paths.DATA == want and paths.HOME == study.root.resolve() and paths.RAW == study.raw.resolve()
            and Path(os.environ.get("KAPACITET_STUDY", "")).resolve() == study.root.resolve())


def build(study: Study, sid: str, quick: bool = False,
          steps: dict[str, Callable[[], dict | None]] | None = None, prefer_identified: bool = False) -> dict:
    """Run the station's steps, recording each one; `steps` replaces the real ones (a test's fakes).
    `prefer_identified`: a phase identified from the meters' voltages replaces the one the DSO
    recorded (by default the recorded one wins)."""
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
               kapacitet=code_version(), quick=quick, prefer_identified=bool(prefer_identified),
               pid=os.getpid(), error=None,
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
    # the latest months, which the meter readings' temperature correction reads too
    latest = raw / "smhi" / smhi.metobs_latest_name(cfg.temperature_station)
    return files + [f] + ([latest] if latest.is_file() else []), point


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
        note("synthetic", POOL_NOTE)
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
    note("synthetic", f"{POOL_NOTE}, scaled from a "
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

        # the equipment, its own folder's included, is the next step's
        network, report, applied = import_directory(study.station_dir(sid) / "import", equipment=False)
        s = network.summary
        rec["quality"] = quality(report)
        rec["summary"] = {"customers": s["customers"], "feeders": s["feeders"], "buses": s["buses"],
                          "lines": s["lines"], "trafo_kva": round(float(network.substation["trafo_sn_mva"]) * 1000.0, 1),
                          "existing_pv_kw": None}
        for kind, text in network_notes(rec.get("origin") or {}, report):
            note(kind, text)
        for what, n in sorted(applied.items()):
            note("assumed", f"{what} ({n})")
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
        note("assumed", f"MV voltage: the band {mv.band_low_pu:.2f}–{mv.band_high_pu:.2f} pu ({mv.source}); "
                        "generation is computed at both ends and the lower value published, consumption at "
                        "the low end")
        approval = "" if method.approval_reference.strip() else "; no envelope capacity (no approval_reference)"
        note("assumed", f"method: percentile {method.percentile:g} of the adoption orders, consumption device "
                        f"{method.consumption_device}, curtailment budget basis {method.budget}{approval}")
        for key, o in study.config.assumptions.items():
            note("assumed", f"{key} = {o.value} {o.unit} ({o.source})")
        return compute(study, sid, rec, quick)

    return {"import": do_import, "equipment": lambda: place_equipment(study, sid, rec, note),
            "meters": lambda: place_meters(study, sid, rec, note),
            "phases": lambda: place_phases(study, sid, rec, note), "weather": lambda: place_weather(study, note),
            "households": lambda: place_households(note), "load": lambda: place_load(rec, note),
            "profiles": do_profiles, "compute": do_compute}


# ---------------------------------------------------------------- equipment, meters and load

def _customers_csv(study: Study, sid: str):
    import pandas as pd

    return pd.read_csv(study.station_dir(sid) / "import" / "customers.csv", dtype={"customer_id": str})


def _write_parquet(df, path: Path) -> None:
    """Written beside and moved in, never through a link."""
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def place_equipment(study: Study, sid: str, rec: dict, note: Callable[[str, str], None]) -> dict:
    """The equipment step: the station's equipment records, checked against its customers and
    merged into processed/grid/customers.parquet as the existing_* columns; without records, those
    columns are not there and the provenance says nothing existing is counted."""
    import pandas as pd

    from ..importer.build import EXISTING, merge_equipment
    from ..importer.validate import check_equipment
    from .intake import station_equipment

    path = paths.PROCESSED / "grid" / "customers.parquet"
    cust = pd.read_parquet(path)
    contract = _customers_csv(study, sid)
    eq, where = station_equipment(study, sid, contract)
    quality = rec.setdefault("quality", {})
    if eq is None:
        quality.pop("equipment", None)
        if any(c in cust.columns for c in EXISTING):
            _write_parquet(cust.drop(columns=[c for c in EXISTING if c in cust.columns]), path)
        note("assumed", "existing equipment: no inventory, so no existing PV, battery or charger is counted")
        return {"rows": 0}
    report = check_equipment(eq, contract)
    quality["equipment"] = report.to_dict()
    if not report.usable:
        raise RuntimeError(report.text())
    merged, applied = merge_equipment(cust, eq)
    _write_parquet(merged, path)
    pv = merged["existing_pv_kw_ac"] > 0
    kw = round(float(merged.loc[pv, "existing_pv_kw_ac"].sum()), 1)
    rec.setdefault("summary", {})["existing_pv_kw"] = kw
    counts = {"rows": int(len(eq)), "pv": int(pv.sum()), "pv_kw_ac": kw,
              "batteries": int((merged["existing_battery_kw"] > 0).sum()),
              "chargers": int((merged["existing_ev_charger_kw"] > 0).sum()),
              "heat_pumps": int(merged["existing_heat_pump"].fillna(False).astype(bool).sum())}
    sources = sorted(set(merged["existing_source"]) - {""})
    said = f"; source: {', '.join(sources)}" if sources else "; source not stated"
    note("real", f"existing equipment: the DSO's records ({where}), {counts['pv']} PV systems ({kw:g} kW AC), "
                 f"{counts['batteries']} batteries, {counts['chargers']} EV chargers and {counts['heat_pumps']} "
                 f"heat pumps{said}")
    for what, n in sorted(applied.items()):
        note("assumed", f"existing equipment: {what} ({n})")
    note("assumed", "existing equipment: the existing PV is firm in every scenario; a recorded heat pump or EV "
                    "of a metered customer is taken to be in the metered load; batteries and the chargers' ratings "
                    "are not modelled, and only the inverters' recorded phases are used (by the unbalanced model)")
    return counts


def _study_customer_ids(study: Study) -> set[str]:
    """Every customer_id of every station of the study (a meter outside them is an unmapped one)."""
    import pandas as pd

    out: set[str] = set()
    for p in sorted(study.stations_dir.glob("*/import/customers.csv")) if study.stations_dir.is_dir() else []:
        try:
            out |= set(pd.read_csv(p, usecols=["customer_id"], dtype=str)["customer_id"].astype(str).str.strip())
        except (OSError, ValueError):
            continue
    return out


def place_meters(study: Study, sid: str, rec: dict, note: Callable[[str, str], None]) -> dict:
    """The meters step: the station's customers' readings in the study's inputs/meters/, checked
    (a finding that stops it fails the step) and summed to complete hours in processed/meters."""
    from ..ingest import meters

    folder = paths.PROCESSED / "meters"
    _clear(folder)
    quality = rec.setdefault("quality", {})
    quality.pop("meters", None)
    files = meters.meter_files(study.inputs / "meters")
    if not files:
        return {"files": 0}
    mine = set(_customers_csv(study, sid)["customer_id"].astype(str).str.strip())
    report, hourly = meters.ingest(files, folder, keep=mine, known=_study_customer_ids(study) | mine)
    quality["meters"] = report.to_dict()
    if hourly is None:
        raise RuntimeError(report.text())
    n = int(hourly["customer_id"].nunique())
    if n:
        first, last = hourly["start"].min(), hourly["start"].max()
        note("real", f"meter readings: {n} of {len(mine)} customers, {len(hourly)} complete hours from "
                     f"{first:%Y-%m-%d} to {last:%Y-%m-%d} (inputs/meters)")
    return {"files": len(files), "meters": n, "hours": int(len(hourly))}


def place_phases(study: Study, sid: str, rec: dict, note: Callable[[str, str], None]) -> dict:
    """The phases step: each meter's phase from the voltages the meters step kept at 15 minutes
    (ingest/phase_id.py), against the transformer secondary meter's v_l1..v_l3 in inputs/mv/<sid>.csv
    when the file has them, into processed/meters/phase_id.parquet; the identified single-phase
    phases go into processed/grid/customers.parquet where the DSO recorded none, and over a
    recorded one only with --prefer-identified. Without voltages nothing changes."""
    import pandas as pd

    from ..config import A
    from ..ingest import phase_id

    quality = rec.setdefault("quality", {})
    quality.pop("phases", None)
    volt_path = paths.PROCESSED / "meters" / "voltage_15min.parquet"
    if not volt_path.is_file():
        return {"meters": 0}
    grid = paths.PROCESSED / "grid" / "customers.parquet"
    cust = pd.read_parquet(grid)
    pv = (cust.set_index(cust["osm_id"].astype(str))["existing_pv_phase"]
          if "existing_pv_phase" in cust.columns else None)
    ref = phase_id.read_reference(study.inputs / "mv" / f"{sid}.csv")
    out, report = phase_id.identify(pd.read_parquet(volt_path), ref, pv_phase=pv)
    prefer = bool(rec.get("prefer_identified"))
    merged, counts = phase_id.apply(cust, out, prefer_identified=prefer, report=report)
    phase_id.write(out, report, paths.PROCESSED / "meters")
    quality["phases"] = report.to_dict()
    if counts["written"]:
        _write_parquet(merged, grid)
    source = next((r for r in out["reference"] if r), "")
    against = {"station": "the transformer secondary meter (inputs/mv)",
               "three_phase_meters": "the three-phase meters (the order most share named a, b, c)",
               "clusters": "groups of the single-phase meters (names a, b, c arbitrary)"}.get(source)
    if counts["identified"]:
        kept = (f"; {counts['conflict']} differ from the recorded phase, "
                f"{'the identified one used (--prefer-identified)' if prefer else 'the recorded one kept'}"
                if counts["conflict"] else "")
        note("real", f"phases: {counts['identified']} single-phase services identified from the meters' voltages "
                     f"against {against}, at a confidence of {A('phase_id_min_confidence'):g} or more; {counts['written']} "
                     f"written where none was recorded{' or preferred' if prefer else ''}{kept}"
                     f"{'' if counts['applied'] else '; none written, the names are relative to this reference'}")
    return {"meters": int(len(out)), "reference": source, **counts,
            "by_status": {str(k): int(v) for k, v in out["status"].value_counts().items()}}


def place_load(rec: dict, note: Callable[[str, str], None]) -> dict:
    """The load step: the metered customers' load over every weather year (profiles/metered.py),
    which the engine takes in place of their pool profile; the provenance says how many."""
    from ..profiles import metered

    (paths.PROCESSED / "validation" / "metered_fit.json").unlink(missing_ok=True)
    if not (paths.PROCESSED / "meters" / "hourly.parquet").is_file():
        return {"fitted": 0}
    report = metered.build()
    c = report["customers"]
    prov = rec["provenance"]
    if c["fitted"]:
        r2 = report["median"]["r2"]
        note("real", f"household load: metered at {c['fitted']} of {c['total']} customers, corrected for temperature "
                     f"(a + b·max({report['base_temp_c']:g} °C − T, 0) by hour and daytype; median R² "
                     f"{'n/a' if r2 is None else f'{r2:.2f}'}) and laid over every weather year with the residual "
                     "of the nearest metered day")
        rest = c["total"] - c["fitted"]
        for i, text in enumerate(prov["synthetic"]):
            if text.startswith(POOL_NOTE):
                prov["synthetic"][i] = (f"household load of the {rest} customers without enough meter readings: "
                                        + text[len("household load: "):]) if rest else None
        prov["synthetic"] = [t for t in prov["synthetic"] if t]
    if c["too_few_days"]:
        note("assumed", f"meter readings: {c['too_few_days']} customers with fewer than {report['min_complete_days']} "
                        "complete days, too few to fit, keep a synthetic household load")
    if c["export_seen"]:
        note("assumed", f"metered load: {c['export_seen']} customers export, so their daytime import understates "
                        "what they consume")
    if c["fitted"]:
        lost = report.get("hours_without_temperature", 0)
        seen = sum(r["hours"] for r in report.get("per_customer", [])) + lost
        if lost and lost > 0.03 * seen:
            note("assumed", f"metered load: {lost} of {seen} metered hours ({100 * lost / seen:.0f} %) had no "
                            "temperature and were left out of the fit; refresh the weather download")
        note("assumed", "metered load: peak and 10-minute statistics still describe the CREST pool, "
                        "not the meters (the readings are hourly)")
    return {"fitted": c["fitted"], "too_few_days": c["too_few_days"], "median_r2": report["median"]["r2"]}


def capacity_path() -> Path:
    return paths.PROCESSED / "results" / "capacity.json"


def fingerprint(study: Study, rec: dict, quick: bool) -> str:
    """What a station's figures were computed from: its inputs' digests, the computation's part of
    study.toml, the method version, the code and the mode (quick, and --prefer-identified when set)."""
    what = {"inputs": rec.get("inputs"), "computation": study.config.computation(),
            "method_version": METHOD_VERSION, "code": code_version(), "quick": bool(quick)}
    if rec.get("prefer_identified"):
        what["prefer_identified"] = True
    return value_digest(what)


def worst_generation(band: tuple[float, float], years, hp, ev) -> dict:
    """The weakest point for generation at both ends of the band, the lower one kept (with both)."""
    from ..engine import station as es
    from ..engine.netmodel import get_model

    ends = {f"{v:.2f}": es.worst_point(get_model(vm_slack=v), "export", years, hp, ev) for v in sorted(set(band))}
    ok = [r for r in ends.values() if r["status"] == "ok"]
    if len(ok) < len(ends):
        out = dict(next(r for r in ends.values() if r["status"] != "ok"))
    else:
        out = dict(min(reversed(ok), key=lambda r: r["kw"]))       # a tie goes to the high end
    out["by_end"] = {k: {key: r.get(key) for key in ("status", "kw", "cid", "customer_id", "baseline_violation_hours")}
                     for k, r in ends.items()}
    return out


def compute(study: Study, sid: str, rec: dict, quick: bool = False) -> dict:
    """Module 1 for this station (engine/station.py) into processed/results/capacity.json, with
    peak_check.json beside it and the "existing" request baselines, the upstream caps of
    inputs/upstream.csv applied (study/upstream.py). Quick: the reference year and fewer orders."""
    import pandas as pd

    from ..atomic import write_json_atomic
    from ..engine import request, run
    from ..engine import station as es
    from . import upstream

    warnings.filterwarnings("ignore")
    cfg = study.config
    method, band = cfg.method, (cfg.mv.band_low_pu, cfg.mv.band_high_pu)
    years = _years(quick)
    n_orders = es.QUICK_ORDERS if quick else es.STUDY_ORDERS
    m = run.model()
    hp, ev = run.background_masks(m.C)
    gen = es.generation_capacity(band, years, n_orders, method.percentile, hp, ev,
                                 approval_reference=method.approval_reference, budget=method.budget)
    low = request.model_at(band[0])
    cons = es.consumption_capacity(low, es.engine_device(method.consumption_device), years, n_orders,
                                   method.percentile, hp, ev)
    cons["consumption_device"] = method.consumption_device
    worst = {"generation": worst_generation(band, years, hp, ev),
             "consumption": es.worst_point(low, "import", years, hp, ev)}
    hourly = paths.PROCESSED / "meters" / "hourly.parquet"
    peak = es.peak_check(pd.read_parquet(hourly) if hourly.is_file() else None, years, hp, ev)
    write_json_atomic(paths.PROCESSED / "results" / "peak_check.json", peak, default=run._default)
    # the "existing" request baseline at each end of the band, on the model the case check uses
    # there (request.check): a baseline at 1.00 pu would meet a check made at 0.98 or 1.06
    baselines = []
    for v in sorted(set(band)):
        name = request.band_name("existing", v)
        request.precompute(request.model_at(v), name, years, hp, ev)
        baselines.append(name)
    up, published = upstream.apply(upstream.read(study), sid, gen.get("kw"), cons.get("kw"))
    checks = [x["verification"] for x in (gen, cons) if "verification" in x]
    out = {
        "schema": es.SCHEMA, "station": sid, "computed_at": procs.now(), "method_version": METHOD_VERSION,
        "fingerprint": fingerprint(study, rec, quick), "mv_band_pu": [float(band[0]), float(band[1])],
        "years": list(years), "orders": n_orders, "percentile": float(method.percentile),
        "generation": gen, "consumption": cons, "worst_point": worst, "upstream": up, "published": published,
        "peak_check": peak, "request_baselines": baselines,
        "verification": {"full_year_violations": sum(c["full_year_violations"] for c in checks),
                         "nonconverged_hours": sum(c["nonconverged_hours"] for c in checks),
                         "hours_checked": sum(c["hours_checked"] for c in checks)},
    }
    write_json_atomic(capacity_path(), out, default=run._default)
    rec.setdefault("summary", {}).update(generation_kw=gen.get("kw"), consumption_kw=cons.get("kw"),
                                         binding_mv_pu=gen.get("binding_mv_pu"))
    return {"years": list(years), "orders": n_orders, "generation_kw": gen.get("kw"),
            "generation_status": gen["status"], "binding_mv_pu": gen.get("binding_mv_pu"),
            "consumption_kw": cons.get("kw"), "consumption_status": cons["status"]}


# ---------------------------------------------------------------- measured validation

def validate(study: Study, sid: str) -> dict:
    """The station's model against the DSO's measurements and static results
    (engine/validation_measured.py) into processed/results/validation_measured.json: the metered
    hours replayed at the measured MV (inputs/mv/<sid>.csv) or at the band's ends, the dpPower
    snapshot (inputs/dppower/snapshot.csv) by mRID, and the DSO's static results
    (inputs/dppower/static.csv) against this model's. Runs in the station's own process."""
    import pandas as pd

    from ..atomic import write_json_atomic
    from ..engine import hosting, run
    from ..engine import validation_measured as vm
    from ..engine.netmodel import get_model
    from ..importer.build import bus_index

    if not isolated(study, sid):
        raise StudyError("not_isolated", id=sid, study=str(study.root))
    warnings.filterwarnings("ignore")
    cfg = study.config
    band = (cfg.mv.band_low_pu, cfg.mv.band_high_pu)
    years = _years(False)
    m = run.model()
    hp, ev = run.background_masks(m.C)
    out: dict = {"station": sid, "computed_at": procs.now(), "method_version": METHOD_VERSION, "band_pu": list(band)}
    hourly = paths.PROCESSED / "meters" / "hourly.parquet"
    volt_file = paths.PROCESSED / "meters" / "voltage_15min.parquet"
    mv_file = study.inputs / "mv" / f"{sid}.csv"
    mv = vm.read_mv(mv_file) if mv_file.is_file() else None
    trafo_kva = float(m.net.trafo["sn_mva"].iloc[0]) * 1000.0
    meters = pd.read_parquet(hourly) if hourly.is_file() else None
    if meters is not None and len(meters):
        volt = pd.read_parquet(volt_file) if volt_file.is_file() else None
        out["measured"] = vm.validate(lambda v: get_model(vm_slack=v), meters, years, hp, ev, band, volt=volt, mv=mv,
                                      trafo_kva=trafo_kva)
    else:
        out["measured"] = {"status": "no meter readings"}
    snap_file = study.inputs / "dppower" / "snapshot.csv"
    if snap_file.is_file():
        out["dppower"] = _snapshot(study, sid, vm, snap_file, meters, mv, hp, ev, band, bus_index)
    static_file = study.inputs / "dppower" / "static.csv"
    if static_file.is_file():
        dso = pd.read_csv(static_file, sep=None, engine="python", dtype=str, encoding="utf-8-sig")
        out["calibrate_static"] = vm.calibrate_static(dso, hosting.nodal_pv_hc(get_model(vm_slack=band[1])),
                                                      hosting.nodal_import_hc(get_model(vm_slack=band[0]), hp, ev))
        out["calibrate_static"]["mv_pu"] = {"gen_kw": band[1], "load_kw": band[0]}
    write_json_atomic(paths.PROCESSED / "results" / "validation_measured.json", out, default=run._default)
    return out


def _snapshot(study: Study, sid: str, vm, path: Path, meters, mv, hp, ev, band, bus_index) -> dict:
    """The dpPower snapshot's hours, each solved with that hour's replayed injection."""
    import numpy as np
    import pandas as pd

    from ..engine.netmodel import get_model
    from ..timeindex import local_standard, year_index

    snap = pd.read_csv(path, sep=None, engine="python", dtype=str, encoding="utf-8-sig")
    snap.columns = [c.strip().lower() for c in snap.columns]
    for c in ("vm_pu", "loading_pct"):
        if c in snap.columns:
            snap[c] = pd.to_numeric(snap[c].str.replace(",", ".", regex=False), errors="coerce")
    folder = study.station_dir(sid) / "import"
    buses = pd.read_csv(folder / "buses.csv", dtype=str)
    trafo = pd.read_csv(folder / "transformer.csv", dtype=str).iloc[0]
    lines = pd.read_csv(folder / "lines.csv", dtype=str)["line_id"].astype(str).tolist()
    index = bus_index(buses, str(trafo["lv_bus"]))
    origin = (study.record(sid).get("origin") or {})
    vm.require_offset(snap["start"], path)
    start = pd.to_datetime(snap["start"], utc=True, errors="coerce", format="ISO8601").dt.floor("h")
    out = {}
    for t, rows in snap.groupby(start):
        year = int(local_standard(pd.DatetimeIndex([t])).year[0])
        r = vm.replay(year, meters if meters is not None else pd.DataFrame(columns=["customer_id", "year", "hour",
                                                                               "kwh_import", "kwh_export"]), hp, ev)
        h = int((t - year_index(year)[0]) // pd.Timedelta(hours=1))
        vm_h, _ = vm.mv_on(mv, year)
        level = float(vm_h[h]) if np.isfinite(vm_h[h]) else None
        runs = {"measured": level} if level is not None else {"band_low": band[0], "band_high": band[1]}
        out[t.isoformat()] = {name: vm.compare_snapshot(get_model(vm_slack=v), rows, index, lines,
                                                        origin.get("trafo_mrid"), r["p"][:, h], r["q"][:, h], None)
                              for name, v in runs.items()}
        out[t.isoformat()]["metered_injection"] = bool(r["complete"][h])
    return out
