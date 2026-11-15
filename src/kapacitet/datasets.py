"""Imported networks: each one a data folder of its own, built by its own process.

    data/datasets/<id>/
        dataset.json   what it is and how its build went, step by step (written atomically)
        import/        the operator's four CSV files, as validated
        processed/     what the engine computed for it (KAPACITET_DATA_DIR points here)
        requests/      what the API writes while it is in use
        build.log      the build process's output
        build.lock     the PID of a build in progress
    data/active.json   which network the server uses: "reference" or an id

The interface calls a network a project. `summary` is what its list shows of one, read from the
network's own files, and `project_archive` is a network as one file: its four tables and what it
is, to be opened on another installation by the same door as any export.

A build runs as `python -m kapacitet dataset build <id>`, separate from the server, with
KAPACITET_DATA_DIR on the network's folder and KAPACITET_RAW_DIR on the reference network's
downloads (weather and prices are the same), and the reference network's household profiles
copied in. The server uses one network at a time and switches only when it runs under the
supervisor (`kapacitet serve`), which restarts it on the new folder: no cache survives a switch.

At most KAPACITET_MAX_BUILDS builds run at once (1: a build takes the machine's cores), each
for at most KAPACITET_BUILD_TIMEOUT_S (4 h) before it is stopped (`reap`), and one can be stopped
at any time (`cancel`). The network in use is never rebuilt under the server that reads it.

The record, the lock, the log and the step runner are procs.py's, shared with a study's stations.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import procs
from .paths import DATASETS, HOME, RAW, ROOT, inside

REFERENCE = "reference"
RESTART = 75                       # the exit code that asks the supervisor for a restart
PROJECT_SCHEMA = "kapacitet.project/1"
ORIGIN_KINDS = ("csv", "template", "project_file")
STEPS = ("import", "households", "site", "profiles", "meters", "compute")
# the envelope studies of a network already built: a second build, offered where the envelopes are on
ENVELOPE_STEPS = ("envelopes",)
PARTS = ("capacity", "envelopes")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,40}\Z")      # \Z: `$` would let a final newline through
MAX_BUILDS = int(os.environ.get("KAPACITET_MAX_BUILDS", "1"))
BUILD_TIMEOUT_S = float(os.environ.get("KAPACITET_BUILD_TIMEOUT_S", str(4 * 3600)))
# launching checks how many builds run and then starts one: two at once could both pass the check
_LAUNCH_LOCK = threading.Lock()


class DatasetError(ValueError):
    """A request about a network that cannot be honoured: a code the interface translates."""

    def __init__(self, code: str, **params):
        super().__init__(ERRORS[code].format(**params))
        self.code = code
        self.params = params


ERRORS = {
    "unknown_dataset": "unknown network {id}",
    "not_usable": "the export does not pass the check, so it cannot be built",
    "not_an_origin": "{kind} is not a way a network is made",
    "unknown_template": "there is no example network {id}",
    "not_in_contract": "not part of the contract: {tables}",
    "building": "network {id} is being built",
    "not_ready": "network {id} is not ready: its build has not finished",
    "in_use": "network {id} is in use; switch to another one first",
    "not_supervised": "the server was not started with `kapacitet serve`, so it cannot switch networks",
    "jobs_running": "an evaluation or a decision is running; switch when it has finished",
    "too_many_builds": "{n} network builds are already running; start this one when one has finished",
    "not_building": "network {id} is not being built",
    "wrong_folder": "the build of network {id} would write into {data}, which is not its own folder",
    "bad_site": "the site needs a latitude and a longitude in degrees",
    "site_zone": "the site's bidding zone is SE1, SE2, SE3 or SE4, and it is chosen: nothing guesses it",
    "site_outside": "the site lies outside Sweden: latitude {lat}, longitude {lon}",
}

# ---------------------------------------------------------------- a project's own site (task 2.3)

ZONES = ("SE1", "SE2", "SE3", "SE4")
# the downloads of the reference folder that do not depend on the place, linked into a project with a
# site of its own: everything but its weather and its prices, which are fetched for the project
_SHARED_RAW = ("esett", "osm", "scb", "zones", "prices", "smhi")


def clean_site(site: dict | None) -> dict | None:
    """A project's site as the record keeps it ({name, lat, lon, zone}), or None for none. The zone is
    the user's choice: a site without one is refused, never guessed."""
    if not site:
        return None
    try:
        lat, lon = float(site["lat"]), float(site["lon"])
    except (KeyError, TypeError, ValueError):
        raise DatasetError("bad_site") from None
    if site.get("zone") not in ZONES:
        raise DatasetError("site_zone")
    if not (55.0 <= lat <= 69.5 and 10.5 <= lon <= 24.5):
        raise DatasetError("site_outside", lat=f"{lat:g}", lon=f"{lon:g}")
    return {"name": str(site.get("name") or "").strip()[:80], "lat": round(lat, 5), "lon": round(lon, 5),
            "zone": site["zone"]}


def share_raw(root: Path) -> int:
    """Link the reference downloads that do not depend on the place into a project with a site of its
    own (<project>/raw), and copy their manifest, which each download of the project then adds to.
    Run by the server, whose downloads are the reference network's (HOME/raw when it is on another)."""
    src = RAW if current() == REFERENCE else HOME / "raw"
    linked = sum(procs.link_files(src / part, root / "raw" / part, replace=False) for part in _SHARED_RAW)
    manifest = root / "raw" / "manifest.json"
    if not manifest.exists() and (src / "manifest.json").exists():
        manifest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / "manifest.json", manifest)     # copied, never linked: the project writes it
    return linked


def site_key(ds_id: str) -> str:
    """The site key of a project (study.toml's [site] key): its id, as long as the key may be."""
    return ds_id[:41]


def write_study_toml(root: Path, ds_id: str, name: str, site: dict, station: dict | None = None) -> Path:
    """The project's study.toml: its [site] layer, which config._merge_study lays over site.toml in its
    build and in the server that opens it. Before the station is found only the key, the name and the
    zone (study/config.Site needs a temperature station beside a position)."""
    lines = ["# The site of this project (kapacitet/datasets.py), read as a study's [site] layer.", "",
             "[site]", f'key = "{site_key(ds_id)}"', f"name = {json.dumps(site.get('name') or name)}",
             f'zone = "{site["zone"]}"']
    if station is not None:
        lines += [f"lat = {site['lat']}", f"lon = {site['lon']}", f"temperature_station = {int(station['id'])}"]
    path = root / "study.toml"
    from .atomic import write_text_atomic

    write_text_atomic(path, "\n".join(lines) + "\n")
    return path


_now = procs.now
_write_json = procs.write_json
_alive = procs.alive


def folder(ds_id: str) -> Path:
    """The folder of a network: an id of the form the app gives, inside the networks' folder."""
    if not _ID.match(ds_id or ""):
        raise DatasetError("unknown_dataset", id=ds_id)
    try:
        return inside(DATASETS, ds_id)
    except ValueError:
        raise DatasetError("unknown_dataset", id=ds_id) from None


def record(ds_id: str) -> dict:
    path = folder(ds_id) / "dataset.json"
    if not path.exists():
        raise DatasetError("unknown_dataset", id=ds_id)
    return json.loads(path.read_text(encoding="utf-8"))


def _save(rec: dict) -> dict:
    _write_json(folder(rec["id"]) / "dataset.json", rec)
    return rec


def listing() -> list[dict]:
    """The imported networks, newest first, each with its build's state."""
    if not DATASETS.exists():
        return []
    out = []
    for path in DATASETS.glob("*/dataset.json"):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        rec["building"] = building(rec["id"])
        rec["summary"] = summary(path.parent)
        out.append(rec)
    return sorted(out, key=lambda r: r.get("created", ""), reverse=True)


def _read(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


# What a build assumed, as importer/build.py counts it (a fallback's label and how many elements it
# touched), turned into what the interface says: a code, the count and what it is counted against.
# The weighty ones change the capacity itself, a cable's or the transformer's impedance, so a project
# built on one is not presented as simply ready (the technical review's task 1.2).
ASSUMPTIONS = {
    "cable type assumed": ("cable_default", "lines", True),
    "transformer vk assumed": ("trafo_vk", None, True),
    "transformer vkr assumed": ("trafo_vkr", None, True),
    "bus without coordinates": ("bus_no_coordinates", "buses", False),
    "coordinates converted from SWEREF 99 TM": ("coordinates_converted", "buses", False),
    "customer without a roof area": ("customer_no_roof", "customers", False),
    "PV AC rating taken from its kWp": ("equipment_pv_ac", None, False),
    "EV charger phases taken as three": ("equipment_ev_three", None, False),
    "EV charger phases taken as one (single-phase service)": ("equipment_ev_one", None, False),
    "battery with only one rating not counted": ("equipment_battery", None, False),
}


def assumptions(grid: dict) -> list[dict]:
    """The fallbacks a network was built on (its grid/summary.json), the weighty ones first: each as
    {code, count, total, weighty}, and for the default cable which cable and which type names."""
    out = []
    for label, n in (grid.get("fallbacks") or {}).items():
        if not isinstance(n, (int, float)) or n <= 0:
            continue
        code, of, weighty = ASSUMPTIONS.get(label, ("other", None, False))
        total = grid.get(of) if of else None
        if of == "buses" and isinstance(total, int):
            total -= 1                     # the medium-voltage bus the build adds has no place to draw
        item = {"code": code, "count": int(n), "total": int(total) if isinstance(total, int) else None,
                "weighty": weighty}
        if code == "cable_default":
            detail = grid.get("assumed_cable") or {}
            item["cable"] = str(detail.get("type") or "")
            item["names"] = {str(k): int(v) for k, v in (detail.get("names") or {}).items()}
        if code == "other":
            item["label"] = str(label)
        out.append(item)
    return sorted(out, key=lambda a: (not a["weighty"], a["code"]))


def summary(root: Path | None = None) -> dict:
    """What a project list shows of the network in `root` (the reference network's folder by
    default): its size, its transformer, when and how it was computed, the capacity of the static
    and of the hourly study for solar and for EV chargers, and what its build assumed. Read from the
    network's own files; what is not there is None, never a guess."""
    base = (HOME if root is None else Path(root)) / "processed"
    grid = _read(base / "grid" / "summary.json")
    meta = _read(base / "results" / "meta.json")
    measures = _read(base / "results" / "hosting_summary.json").get("measures") or {}
    devices = _read(base / "results" / "consumption.json").get("devices") or {}

    def median_kw(name: str) -> float | None:
        value = ((measures.get(name) or {}).get("kw_ac") or {}).get("median")
        return float(value) if isinstance(value, (int, float)) else None

    def chargers(device: str) -> float | None:
        """New devices the street takes, median over the orders (results/consumption.json)."""
        value = ((devices.get(device) or {}).get("adopters") or {}).get("median")
        return float(value) if isinstance(value, (int, float)) else None

    years = [y for y in meta.get("years") or [] if isinstance(y, int)]
    return {"customers": grid.get("customers"), "feeders": grid.get("feeders"), "buses": grid.get("buses"),
            "lines": grid.get("lines"), "trafo_type": grid.get("trafo_type"),
            "computed_at": meta.get("computed_at"), "quick": meta.get("quick"),
            "reference_year": meta.get("reference_year"),
            "years": [min(years), max(years)] if years else None, "n_years": len(years) or None,
            "static_kw": median_kw("static"), "hourly_kw": median_kw("timeseries"),
            # whether the envelope studies are computed (the showcase is on disk): nothing to say
            # before the capacity is
            "envelopes": (base / "results" / "showcase.json").exists() if measures else None,
            # where its weather and prices come from (site.json, the build's site step); None: the
            # reference site's
            "site": _read(base / "site.json") or None,
            # new EV chargers by the hourly study, and by the static convention (a constant load)
            "ev_chargers": chargers("ev"), "ev_chargers_static": chargers("flat"),
            "assumptions": assumptions(grid),
            # the meter readings the build took (processed/meters/summary.json, the meters step)
            "meters": _read(base / "meters" / "summary.json") or None}


# ---------------------------------------------------------------- which network is in use

def active() -> str:
    path = HOME / "active.json"
    if not path.exists():
        return REFERENCE
    try:
        ds = json.loads(path.read_text(encoding="utf-8")).get("dataset", REFERENCE)
    except ValueError:
        return REFERENCE
    return ds if ds == REFERENCE or (DATASETS / ds / "dataset.json").exists() else REFERENCE


def set_active(ds_id: str) -> None:
    if ds_id != REFERENCE:
        rec = record(ds_id)
        if rec.get("status") != "ready":
            raise DatasetError("not_ready", id=ds_id)
    _write_json(HOME / "active.json", {"dataset": ds_id, "since": _now()})


def child_env(ds_id: str | None = None) -> dict[str, str]:
    """The environment the server runs with for a network: its data folder and the shared downloads."""
    ds_id = ds_id or active()
    if ds_id == REFERENCE:
        return procs.child_env(home=HOME, dataset=ds_id)
    root = folder(ds_id)
    if (root / "study.toml").exists():
        # a site of its own: its study.toml is the site layer, and its downloads are its own
        return procs.child_env(root, home=HOME, raw=root / "raw", dataset=ds_id, study=root)
    return procs.child_env(root, home=HOME, raw=RAW, dataset=ds_id)


def current() -> str:
    """The network this process runs on."""
    return os.environ.get("KAPACITET_DATASET", REFERENCE)


def restart_soon(delay: float = 0.5) -> None:
    """Hand the server back to its supervisor in a moment (after the answer has gone out), which
    starts it again: on another network after a switch, on new data after a bundle was loaded."""
    def leave() -> None:
        procs.let_go_all()            # the server's lock is free for the server that replaces it
        os._exit(RESTART)

    threading.Timer(delay, leave).start()


# ---------------------------------------------------------------- creating one

def is_synthetic(origin: dict | None) -> bool:
    """Whether a network is a made-up one: built from an example network, or from a project file
    that says so of itself. Its coordinates are then a schematic layout, not places."""
    origin = origin or {}
    return origin.get("kind") == "template" or origin.get("synthetic") is True


def current_origin() -> dict:
    """Where the network this process runs on came from: {"kind": "reference"} for the reference
    network, the record's origin for an imported one (an export, when the record says nothing)."""
    ds_id = current()
    if ds_id == REFERENCE:
        return {"kind": "reference"}
    try:
        return dict(record(ds_id).get("origin") or {"kind": "csv"})
    except DatasetError:
        return {"kind": "csv"}


def clean_origin(origin: dict | None) -> dict:
    """Where a network came from, as its record keeps it. A template's facts are the catalogue's,
    never the caller's; a file's name is kept short and on one line."""
    if not origin:
        return {"kind": "csv"}
    kind = origin.get("kind")
    if kind not in ORIGIN_KINDS:
        raise DatasetError("not_an_origin", kind=str(kind)[:40])
    if kind == "template":
        from .importer import templates

        try:
            return templates.origin(templates.get(str(origin.get("template"))))
        except KeyError:
            raise DatasetError("unknown_template", id=str(origin.get("template"))[:40]) from None
    out: dict = {"kind": kind}
    name = " ".join(str(origin.get("name") or "").split())[:120]
    if name:
        out["name"] = name
    if kind == "project_file" and origin.get("synthetic") is not None:
        out["synthetic"] = bool(origin.get("synthetic"))
    return out


def create(name: str, files: dict[str, str], quick: bool = True, origin: dict | None = None,
           decoded_as: dict[str, str] | None = None, options: dict | None = None, site: dict | None = None) -> dict:
    """Validate an export and keep it as a new network, ready to build. Nothing is written for an
    export that does not pass the check. `options` are what the user chose in the check
    (validate.validate's `default_cable`); the build applies the same ones."""
    from .importer.contract import BY_NAME
    from .importer.validate import read_files, validate

    # the API refuses these first; whoever calls this directly gets the same answer
    unknown = sorted(set(files) - set(BY_NAME))
    if unknown:
        raise DatasetError("not_in_contract", tables=", ".join(unknown))
    origin = clean_origin(origin)             # before anything is written
    site = clean_site(site)
    chosen = {"default_cable": True} if (options or {}).get("default_cable") else {}
    data = read_files(files, decoded_as)
    report = validate(data, default_cable=bool(chosen.get("default_cable")))
    if not report.usable:
        raise DatasetError("not_usable")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "network"
    ds_id = f"{slug}-{secrets.token_hex(3)}"
    root = folder(ds_id)
    (root / "import").mkdir(parents=True, exist_ok=False)
    for table in BY_NAME:                    # by the contract's own names, never the caller's keys
        if table in files:
            # as bytes: in text mode Windows would turn each line end of an export made there
            # (CR LF) into CR CR LF, a blank line between every two rows
            inside(root / "import", f"{table}.csv").write_bytes(files[table].encode("utf-8"))
    rec = {"id": ds_id, "name": name.strip()[:80] or ds_id, "created": _now(), "status": "uploaded", "quick": quick,
           "origin": origin, "report": report.to_dict(), "customers": int(len(data["customers"])),
           "steps": [{"name": s, "status": "waiting"} for s in STEPS]}
    if chosen:
        rec["options"] = chosen
    if site is not None:
        rec["site"] = site
        write_study_toml(root, ds_id, rec["name"], site)
        share_raw(root)
    return _save(rec)


def project_archive(name: str, files: dict[str, str], origin: dict, customers: int | None = None) -> bytes:
    """A network as one file: project.json (what it is), its tables as the contract's CSV files and
    the contract itself as README.md. Results are not in it: they are computed again where it is
    opened, by that installation's own version."""
    import io
    import zipfile

    from . import __version__
    from .importer.contract import BY_NAME
    from .importer.export import readme

    tables = [t for t in BY_NAME if t in files]
    doc = {"schema": PROJECT_SCHEMA, "name": name, "exported_at": _now(), "kapacitet": __version__,
           "customers": customers, "origin": origin, "tables": tables}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json", json.dumps(doc, ensure_ascii=False, indent=1))
        for table in tables:
            z.writestr(f"{table}.csv", files[table])
        z.writestr("README.md", readme())
    return buf.getvalue()


def import_files(ds_id: str) -> dict[str, str]:
    """The tables an imported network was made from, as CSV text."""
    from .importer.contract import BY_NAME

    root = folder(ds_id) / "import"
    out = {}
    for table in BY_NAME:
        path = inside(root, f"{table}.csv")
        if path.is_file():
            # one kind of line end, whatever the export had (and whatever an older version stored)
            text = path.read_bytes().decode("utf-8")
            out[table] = text.replace("\r\r\n", "\n").replace("\r\n", "\n")
    return out


def delete(ds_id: str) -> None:
    rec = record(ds_id)
    if building(ds_id):
        raise DatasetError("building", id=ds_id)
    if ds_id in (active(), current()):
        raise DatasetError("in_use", id=ds_id)
    shutil.rmtree(folder(rec["id"]))


# ---------------------------------------------------------------- building one

def _lock(ds_id: str) -> Path:
    return procs.lock_path(folder(ds_id))


def building(ds_id: str) -> bool:
    """A build holds the lock and its process is alive (a stale lock does not count)."""
    return procs.building(folder(ds_id))


def _launched(rec: dict) -> bool:
    """A build was started and its process is alive, whether or not it has taken its lock yet."""
    return rec.get("status") == "building" and procs.alive(int(rec.get("pid") or 0))


def _running(rec: dict) -> bool:
    return building(rec["id"]) or _launched(rec)


def builds_running() -> list[str]:
    """The networks being built now."""
    return [rec["id"] for rec in listing() if rec.get("building") or _launched(rec)]


def can_launch(ds_id: str | None = None) -> None:
    """Refuse a build that may not start now: the network in use (the server reads its folder), one
    already building, or one more than KAPACITET_MAX_BUILDS."""
    if ds_id is not None:
        if building(ds_id) or _launched(record(ds_id)):
            raise DatasetError("building", id=ds_id)
        if ds_id in (active(), current()):
            raise DatasetError("in_use", id=ds_id)
    running = builds_running()
    if len(running) >= MAX_BUILDS:
        raise DatasetError("too_many_builds", n=len(running))


def launch(ds_id: str, workers: int = 2, part: str = "capacity") -> int:
    """Start the build (`part`, as `build` takes it) in its own process, detached from the server,
    and return its PID."""
    if part not in PARTS:
        raise ValueError(f"no part {part!r}: one of {', '.join(PARTS)}")
    with _LAUNCH_LOCK:
        rec = record(ds_id)
        reap()
        can_launch(ds_id)
        # the envelope studies need the capacity part, however the last build ended (a failed attempt
        # at the envelopes leaves the capacity as it was)
        if part == "envelopes" and "capacity" not in built_parts(rec):
            raise DatasetError("not_ready", id=ds_id)
        root = folder(ds_id)
        env = {**os.environ, **child_env(ds_id), "PYTHONUNBUFFERED": "1"}
        env.pop("KAPACITET_SUPERVISED", None)
        # the id of the record read from disk, not the caller's text: what is launched is a network
        # that exists, by the name its own record gives it
        cmd = [sys.executable, "-m", "kapacitet", "dataset", "build", str(rec["id"]), "--workers", str(int(workers)),
               "--part", part]
        if rec.get("quick"):
            cmd.append("--quick")
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kwargs["start_new_session"] = True
        log = open(root / procs.LOG, "ab")
        proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, **kwargs)
        log.close()
        rec.update(status="building", launched=_now(), pid=proc.pid)
        rec.pop("error", None)
        _save(rec)
        return proc.pid


def cancel(ds_id: str, reason: str = "cancelled") -> dict:
    """Stop a network's build and every process it started; the record says where it stopped and why."""
    rec = record(ds_id)
    holder = procs.holder(_lock(ds_id))
    pids = {p for p in (holder, int(rec.get("pid") or 0) if _launched(rec) else None) if p}
    if not pids:
        raise DatasetError("not_building", id=ds_id)
    for pid in pids:
        procs.kill_tree(pid)
    rec = record(ds_id)                    # as the build last wrote it
    for s in rec.get("steps", []):
        if s.get("status") == "running":
            s.update(status="failed", error=reason, finished=_now())
    rec.update(status="failed", error=reason, finished=_now(), cancelled=True)
    _save(rec)
    _lock(ds_id).unlink(missing_ok=True)
    return rec


def reap(now: float | None = None) -> list[str]:
    """Stop every build that has run past KAPACITET_BUILD_TIMEOUT_S; returns the networks stopped."""
    from datetime import datetime

    now = time.time() if now is None else now
    stopped = []
    for rec in listing():
        if not _running(rec):
            continue
        began = rec.get("launched") or rec.get("started")
        try:
            age = now - datetime.fromisoformat(began).timestamp() if began else 0.0
        except ValueError:
            age = 0.0
        if age > BUILD_TIMEOUT_S:
            try:
                cancel(rec["id"], f"timed out after {BUILD_TIMEOUT_S:.0f} s")
                stopped.append(rec["id"])
            except DatasetError:
                pass
    return stopped


def _copy_households(dest_processed: Path) -> int:
    """The reference network's household profile pool, linked where the disk allows, else copied
    (a file already there is kept; nothing rewrites the pool in place)."""
    return procs.link_files(HOME / "processed" / "households", dest_processed / "households", replace=False)


def _annual_check(year: int) -> dict | None:
    """The export's measured annual consumption beside the modelled household demand, when given."""
    import numpy as np

    from .grid.builder import load_tables
    from .profiles import household

    _, _, cust = load_tables()
    if "annual_kwh" not in cust.columns:
        return None
    given = cust["annual_kwh"].to_numpy(dtype=float)
    ok = np.isfinite(given) & (given > 0)
    if not ok.any():
        return None
    model = household.customer_base_load(year, len(cust)).sum(axis=1)
    ratio = given[ok] / np.maximum(model[ok], 1e-9)
    return {"customers": int(ok.sum()), "median_given_kwh": float(np.median(given[ok])),
            "median_model_kwh": float(np.median(model[ok])), "median_ratio": float(np.median(ratio))}


def built_parts(rec: dict) -> list[str]:
    """The parts of a computation a network's last good build left (PARTS). A record from before
    the build had parts says nothing: then its folder does, by the files each part writes."""
    if "parts" in rec:
        return [p for p in PARTS if p in rec["parts"]]
    results = folder(str(rec["id"])) / "processed" / "results"
    return [p for p, f in (("capacity", "hosting_summary.json"), ("envelopes", "showcase.json")) if (results / f).exists()]


def build(ds_id: str, workers: int = 2, quick: bool = True, steps: dict | None = None,
          part: str = "capacity") -> dict:
    """Run every step of a network's build in this process, recording each one. `steps` replaces
    the real steps (a test's fakes); the order and the record are the same.

    `part` "capacity" is the network, its profiles and its capacity: the network is ready to open
    when it ends (the envelope studies too, when an earlier build had them, so a rebuild leaves no
    stale part behind). "envelopes" adds the envelope studies to a network that is ready, and keeps
    the record of the first part's steps."""
    if part not in PARTS:
        raise ValueError(f"no part {part!r}: one of {', '.join(PARTS)}")
    rec = record(ds_id)
    if building(ds_id):
        raise DatasetError("building", id=ds_id)
    had = built_parts(rec)
    # the record says "building" by now when the server launched this process (launch): what counts
    # is that the capacity part is computed
    if part == "envelopes" and "capacity" not in had:
        raise DatasetError("not_ready", id=ds_id)
    rec.update(workers=workers, quick=quick)
    with_envelopes = part == "envelopes" or "envelopes" in had
    names = ENVELOPE_STEPS if part == "envelopes" else STEPS
    steps = steps or _real_steps(ds_id, workers, quick, envelopes=with_envelopes and part == "capacity")
    out = procs.run_steps(rec, names, steps, _save, _lock(ds_id), logger="kapacitet.datasets",
                          keep=part == "envelopes")
    if out.get("status") == "ready":
        out["parts"] = [p for p in PARTS if p == "capacity" or (p == "envelopes" and with_envelopes)]
        _save(out)
    elif part == "envelopes":
        # the capacity part is untouched: the network stays ready to open, its record says which step
        # failed and why, and the envelope studies can be asked for again
        out["status"] = "ready"
        _save(out)
    return out


def own_folder(ds_id: str) -> bool:
    """Whether this process's data folder is the network's own. Every step of a build writes into
    the data folder of the process it runs in, so a build anywhere else writes over another
    network: the reference network's, when the command is typed without its environment."""
    from .paths import DATA

    return Path(DATA).resolve() == folder(ds_id).resolve()


def _real_steps(ds_id: str, workers: int, quick: bool, envelopes: bool = False) -> dict:
    """The build as the CLI runs it, in a process whose KAPACITET_DATA_DIR is the network's folder
    (and in no other: `own_folder`). `compute` is the capacity part, and the envelopes part too
    when `envelopes` (a rebuild of a network that had it); `envelopes` is that part alone."""
    from .paths import DATA, PROCESSED

    if not own_folder(ds_id):
        raise DatasetError("wrong_folder", id=ds_id, data=str(DATA))

    def do_import():
        from .importer.build import import_directory

        chosen = record(ds_id).get("options") or {}
        network, _report, applied = import_directory(folder(ds_id) / "import",
                                                     default_cable=bool(chosen.get("default_cable")))
        return {"summary": {k: network.summary[k] for k in ("customers", "feeders", "buses", "lines", "trafo_type")},
                "fallbacks": applied}

    def do_households():
        return {"files": _copy_households(PROCESSED)}

    def do_site():
        """The project's own site: the nearest SMHI temperature station with every weather year, the
        site layer written, and the downloads of that place, beside links to the reference
        downloads that do not depend on it. Without a site, the reference site's weather and prices."""
        rec = record(ds_id)
        site = rec.get("site")
        if not site:
            return {"site": "reference"}
        from . import cache, config
        from .ingest import prices, smhi
        from .paths import RAW as here_raw

        root = folder(ds_id)
        # its downloads are its own (child_env): never the reference network's folder
        if Path(here_raw).resolve() != (root / "raw").resolve():
            raise DatasetError("wrong_folder", id=ds_id, data=str(here_raw))
        cfg = config.settings()
        station = smhi.nearest_station(site["lat"], site["lon"], cfg.weather_years[0], cfg.weather_years[-1])
        write_study_toml(root, ds_id, rec["name"], site, station)
        config.study_config.cache_clear()
        config.settings.cache_clear()
        cache.clear_all()
        cfg = config.settings()
        for year in cfg.weather_years:
            smhi.fetch_strang(cfg.strang["ghi"], year)
            smhi.fetch_strang(cfg.strang["dni"], year)
        smhi.fetch_strang(cfg.strang["dhi"], cfg.reference_year)
        smhi.fetch_metobs(cfg.temperature_station)
        years = sorted(set(cfg.weather_years) | set(cfg.price_years))
        early = [y for y in years if prices.source_for_year(y) == "entsoe"]
        for year in years:
            if year not in early:
                prices.fetch_elpris_year(site["zone"], year)
        priced_early = bool(early) and prices.entsoe_token() is not None
        if priced_early:
            prices.fetch_ecb_eursek(early[0], early[-1])
            for year in early:
                prices.fetch_entsoe_day_ahead(site["zone"], year)
        doc = {"name": site.get("name") or rec["name"], "lat": site["lat"], "lon": site["lon"], "zone": site["zone"],
               "strang": {"lat": cfg.site.strang_point[0], "lon": cfg.site.strang_point[1]},
               "station": station,
               "prices": {"zone": site["zone"],
                          "years": [y for y in years if y not in early or priced_early],
                          "sources": ["elprisetjustnu"] + (["entsoe"] if priced_early else [])},
               "households": "the reference network's synthetic household pool"}
        procs.write_json(root / "processed" / "site.json", doc)
        return {"site": doc}

    def do_profiles():
        from .config import settings
        from .profiles import build as profiles

        # every weather year, quick or not: the static study takes its minimum load over all of
        # them, and the profiles are the cheap part; "quick" trims the computation that follows
        profiles.build_all(quick=False)
        check = _annual_check(settings().reference_year)
        return {"annual_kwh": check} if check else {}

    def do_meters():
        # the customers' readings in inputs/meters, checked and summed to complete hours, and the
        # metered customers' load in place of their pool profile (profiles/metered.py), as a
        # station's meters and load steps do; after the profiles, whose weather the fit needs
        return place_meters(folder(ds_id))

    def do_compute():
        # what the four screens of an installation read: the capacity per order, the hourly
        # deployment, the consumption capacity, the request's static baseline, the sensitivity, the
        # validation, the protection and meta.json
        from .engine import run

        run.compute_all(quick=quick, workers=workers, parts=run.PARTS if envelopes else ("capacity",))
        return do_envelope_reports() if envelopes else {}

    def do_envelopes():
        # the envelopes per order and everything built on them, beside the capacity already there
        from .engine import run

        run.compute_all(quick=quick, workers=workers, parts=("envelopes",))
        return do_envelope_reports()

    def do_envelope_reports():
        from .engine import forecast, meter_compliance, redispatch

        forecast.write()          # the forecast ladder the Method screen reads (its own CLI command)
        # The two reports Operation reads that no stage writes. Each has its own CLI command, which
        # acts on the data folder it is typed in, so a note telling the reader to run it would send
        # them to the reference network: they are written here. The fleet check uses the network's
        # meter readings when it has any, and otherwise a synthetic fleet that says it is one.
        notes = {}
        try:
            redispatch.write()
        except redispatch.RedispatchError as err:
            notes["redispatch"] = str(err)
        try:
            fleet = meter_compliance.build(PROCESSED) or meter_compliance.build(PROCESSED, synthetic=True)
            if fleet is not None:
                meter_compliance.write(fleet, PROCESSED)
        except meter_compliance.MeterComplianceError as err:
            notes["meter_compliance"] = str(err)
        return {"not_written": notes} if notes else {}

    return {"import": do_import, "households": do_households, "site": do_site, "profiles": do_profiles,
            "meters": do_meters, "compute": do_compute, "envelopes": do_envelopes}


def meters_folder(root: Path) -> Path:
    """Where a project keeps the meter readings it is given (PUT /datasets/{id}/meters)."""
    return Path(root) / "inputs" / "meters"


def place_meters(root: Path) -> dict:
    """The meters step of a project: processed/meters from inputs/meters, the metered load, and
    processed/meters/summary.json for its card. Without readings, nothing (and nothing left over)."""
    import json as _json

    import pandas as pd

    from .ingest import meters
    from .paths import PROCESSED
    from .profiles import metered

    out = PROCESSED / "meters"
    out.mkdir(parents=True, exist_ok=True)
    for f in out.iterdir():
        if f.is_file():
            f.unlink()
    for f in (PROCESSED / "households").glob("metered_*.npy"):
        f.unlink()
    files = meters.meter_files(meters_folder(root))
    if not files:
        return {"files": 0}
    ids = set(pd.read_parquet(PROCESSED / "grid" / "customers.parquet")["osm_id"].astype(str))
    report, hourly = meters.ingest(files, out, keep=ids)
    if hourly is None:
        raise RuntimeError(report.text())
    fit = metered.build() or {}
    n = int(hourly["customer_id"].nunique())
    doc = {"files": len(files), "meters": n, "customers": len(ids), "hours": int(len(hourly)),
           "first": None if not n else f"{hourly['start'].min():%Y-%m-%d}",
           "last": None if not n else f"{hourly['start'].max():%Y-%m-%d}",
           "fitted": ((fit.get("customers") or {}).get("fitted")), "report": report.to_dict()}
    (out / "summary.json").write_text(_json.dumps(doc, indent=1, default=str), encoding="utf-8")
    return {k: doc[k] for k in ("files", "meters", "hours", "fitted")}


def log_tail(ds_id: str, lines: int = 40) -> list[str]:
    return procs.log_tail(folder(ds_id), lines)
