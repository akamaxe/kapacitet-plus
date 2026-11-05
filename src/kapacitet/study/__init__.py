"""A DSO's study: many secondary substations ("stations"), each one built by a process of its own.

    <study>/
        study.toml        what the study is (study/config.py)
        inputs/           what the DSO hands over: network/(cgmes|contract/<sid>), cim_customers.csv,
                          stations.csv, equipment.csv, meters/, upstream.csv, mv/<sid>.csv,
                          dppower/snapshot.csv, cases/, sync/
        raw/              the study's own downloads (`kapacitet study fetch`)
        stations/<sid>/   one data folder per station, shaped like an imported network's:
                          station.json, import/, processed/, requests/, build.log, build.lock
        evidence/         the study's seal chain
        runs/<Q>/         one quarter's publication
        cases/<batch>/    connection cases checked in a batch

A study holds a DSO's network and customer data, so it lives outside the code: by default under
`%USERPROFILE%\\kapacitet-studies\\<id>` (or KAPACITET_STUDY_DIR), and never inside a git work tree,
unless `--allow-git` says the data are synthetic. A station's build runs as
`kapacitet station build <sid> --study <dir>` with KAPACITET_DATA_DIR on its folder
(study/portfolio.py), so it writes nothing outside that folder; it never touches active.json or
the server.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .. import procs
from ..paths import ROOT
from ..cache import memo

if TYPE_CHECKING:
    from .config import StudyConfig

SCHEMA = "kapacitet.station/1"
STATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
# names Windows keeps for devices: a folder cannot have one
RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                      *(f"LPT{i}" for i in range(1, 10))})
# inputs recorded with a build that do not make it stale: the upstream caps are applied to the
# published figures, after the computation
NOT_A_TRIGGER = ("upstream",)
LAYOUT = ("inputs/network/contract", "inputs/meters", "inputs/mv", "inputs/dppower", "inputs/cases",
          "inputs/sync", "raw", "stations", "evidence", "runs", "cases")
PROVENANCE = ("real", "synthetic", "assumed")


class StudyError(ValueError):
    """A request about a study that cannot be honoured, with a code and its parameters."""

    def __init__(self, code: str, **params):
        super().__init__(ERRORS[code].format(**params))
        self.code = code
        self.params = params


ERRORS = {
    "inside_repo": "{path} is inside the Kapacitet+ code ({root}): a study holds a DSO's data and is kept "
                   "outside it (pass --allow-git only for synthetic data)",
    "inside_git": "{path} is inside the git work tree {tree}: a study holds a DSO's data and is kept outside "
                  "any repository (pass --allow-git only for synthetic data)",
    "no_study": "no study.toml in {path}: start one with `kapacitet study init {path}`",
    "exists": "{path} already holds a study",
    "config": "{message}",
    "bad_station": "{id!r} is not a station id (letters, digits, - and _, at most 64, and not a name "
                   "Windows keeps for a device, such as CON or NUL)",
    "station_case": "station {id} differs from station {other} only in letter case: on Windows they are "
                    "one folder, so give them ids that differ otherwise",
    "unknown_station": "the study has no station {id}",
    "building": "station {id} is being built",
    "study_building": "the study is being built by another process (PID {pid})",
    "no_site": "study.toml names no place of its own ([site] lat, lon and temperature_station), so there is "
               "no weather to fetch for it; without it, the stations reuse the reference network's weather, "
               "labelled synthetic",
    "method_version": "the study is pinned to method {pinned}; this code computes method {current}",
    "not_isolated": "station {id} is built by its own process, with KAPACITET_DATA_DIR on its folder: "
                    "run `kapacitet study build {study} --station {id}`",
    "cgmes_pending": "inputs/network/cgmes holds files, and the CGMES import is not in this version yet: "
                     "hand the stations over as contract folders (inputs/network/contract/<sid>)",
    "no_network": "the study has no network: put a contract folder per station in inputs/network/contract/ "
                  "(or run `kapacitet study import {path} --contract <folder>`)",
}


# ---------------------------------------------------------------- where a study may live

def default_root() -> Path:
    value = os.environ.get("KAPACITET_STUDY_DIR")
    return Path(value) if value else Path.home() / "kapacitet-studies"


def resolve(path: str | Path) -> Path:
    """A bare name is a study under default_root(); anything else is a path as given."""
    text = str(path)
    if not any(sep in text for sep in ("/", "\\", ":")) and not text.startswith("."):
        return (default_root() / text).resolve()
    return Path(text).expanduser().resolve()


def git_tree(path: Path) -> Path | None:
    """The git work tree `path` is in (a `.git` folder, or the `.git` file of a worktree), if any."""
    p = Path(path).resolve()
    for candidate in (p, *p.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def guard(path: Path, allow_git: bool = False) -> Path:
    """Refuse a study folder inside the code or inside any git work tree, unless `allow_git`."""
    p = Path(path).resolve()
    if allow_git:
        return p
    root = ROOT.resolve()
    if p == root or root in p.parents:
        raise StudyError("inside_repo", path=str(p), root=str(root))
    tree = git_tree(p)
    if tree is not None:
        raise StudyError("inside_git", path=str(p), tree=str(tree))
    return p


# ---------------------------------------------------------------- digests

def valid_id(sid) -> bool:
    """A station id that is also a folder name on every system."""
    return isinstance(sid, str) and bool(STATION_ID.match(sid)) and sid.upper() not in RESERVED


def file_digest(path: Path) -> str | None:
    """sha256 of a file, or None when it is not there."""
    path = Path(path)
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def dir_digest(path: Path, pattern: str = "*") -> str | None:
    """sha256 over the names and contents of a folder's files (recursively), or None if it has none."""
    path = Path(path)
    if not path.is_dir():
        return None
    files = sorted(f for f in path.rglob(pattern) if f.is_file())
    if not files:
        return None
    h = hashlib.sha256()
    for f in files:
        h.update(f.relative_to(path).as_posix().encode("utf-8") + b"\0")
        h.update((file_digest(f) or "").encode("ascii") + b"\n")
    return h.hexdigest()


def value_digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


@memo(maxsize=1)
def code_version() -> dict:
    """The Kapacitet+ version and commit a station was computed with."""
    from .. import __version__

    commit = None
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=10)
        if out.returncode == 0:
            commit = out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    return {"version": __version__, "commit": commit}


# ---------------------------------------------------------------- the study

@dataclass(frozen=True)
class Study:
    root: Path
    config: StudyConfig

    @property
    def id(self) -> str:
        return self.root.name

    @property
    def toml(self) -> Path:
        return self.root / "study.toml"

    @property
    def inputs(self) -> Path:
        return self.root / "inputs"

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def stations_dir(self) -> Path:
        return self.root / "stations"

    @property
    def evidence(self) -> Path:
        return self.root / "evidence"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    def station_dir(self, sid: str) -> Path:
        if not valid_id(sid):
            raise StudyError("bad_station", id=sid)
        return self.stations_dir / sid

    def station_file(self, sid: str) -> Path:
        return self.station_dir(sid) / "station.json"

    def station_ids(self) -> list[str]:
        """Every station with a record, in id order."""
        if not self.stations_dir.is_dir():
            return []
        return sorted(p.parent.name for p in self.stations_dir.glob("*/station.json") if valid_id(p.parent.name))

    def has(self, sid: str) -> bool:
        return self.station_file(sid).is_file()

    def record(self, sid: str) -> dict:
        path = self.station_file(sid)
        if not path.is_file():
            raise StudyError("unknown_station", id=sid)
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, rec: dict) -> dict:
        procs.write_json(self.station_file(rec["id"]), rec)
        return rec

    def building(self, sid: str) -> bool:
        return procs.building(self.station_dir(sid))

    def digests(self, sid: str, shared: dict | None = None) -> dict:
        """The sha256 of everything a station's build reads: when none has changed, and the method
        and mode are the same, the station is not built again (study/portfolio.py). `shared` is
        shared_digests(), computed once for a whole plan rather than again for every station."""
        shared = self.shared_digests() if shared is None else shared
        return {
            "network": dir_digest(self.station_dir(sid) / "import", "*.csv"),
            "mv": file_digest(self.inputs / "mv" / f"{sid}.csv"),
            **shared,
        }

    def shared_digests(self) -> dict:
        """The digests of what every station of the study reads alike: the study's inputs, the
        computation's part of study.toml, its overrides, and the weather and household profiles
        its stations start from (the study's own downloads, or the reference network's files)."""
        from .station import households_source, weather_source

        cfg = self.config
        overrides = cfg.overrides()
        weather = weather_source(self)
        return {
            "equipment": file_digest(self.inputs / "equipment.csv"),
            "meters": dir_digest(self.inputs / "meters"),
            "upstream": file_digest(self.inputs / "upstream.csv"),
            "study_toml": value_digest(cfg.computation()),
            "overrides": value_digest(overrides) if overrides else None,
            "weather": value_digest({"source": weather["source"], "point": weather.get("point"),
                                     "files": {f.name: file_digest(f) for f in weather["files"]}}),
            "households": dir_digest(households_source()),
        }

    def same_but_case(self, sid: str) -> str | None:
        """Another station's id that differs from `sid` only in letter case, if there is one."""
        from .intake import contract_dir

        for parent in (contract_dir(self), self.stations_dir):
            if parent.is_dir():
                for p in parent.iterdir():
                    if p.is_dir() and p.name != sid and p.name.lower() == sid.lower():
                        return p.name
        return None

    def new_record(self, sid: str, name: str = "", origin: dict | None = None) -> dict:
        """A station's first record: what it is and where it came from; nothing built yet."""
        return {
            "schema": SCHEMA, "id": sid, "name": name or sid, "study": self.id,
            "origin": origin or {"format": "contract", "trafo_mrid": None, "files": {}},
            "inputs": {}, "method_version": self.config.dso.method_version,
            "kapacitet": code_version(), "status": "imported", "pid": None, "steps": [],
            "summary": {}, "provenance": {k: [] for k in PROVENANCE},
            "quality": {"report": None, "usable": None, "warnings": []},
        }


def _config(root: Path) -> StudyConfig:
    from .config import StudyConfigError, load

    if not (root / "study.toml").is_file():
        raise StudyError("no_study", path=str(root))
    try:
        return load(root / "study.toml")
    except StudyConfigError as err:
        raise StudyError("config", message=str(err)) from None


def open_study(path: str | Path, allow_git: bool = False) -> Study:
    """The study in `path` (a bare name is looked up under default_root()), its study.toml checked."""
    root = guard(resolve(path), allow_git)
    return Study(root=root, config=_config(root))


def init_study(path: str | Path, name: str = "", allow_git: bool = False, site: dict | None = None) -> Study:
    """A new, empty study: the folder layout and a study.toml with every default stated."""
    from .config import template

    root = guard(resolve(path), allow_git)
    if (root / "study.toml").exists():
        raise StudyError("exists", path=str(root))
    root.mkdir(parents=True, exist_ok=True)
    for sub in LAYOUT:
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "study.toml").write_text(template(name, site), encoding="utf-8")
    return Study(root=root, config=_config(root))


__all__ = ["SCHEMA", "Study", "StudyError", "default_root", "file_digest", "dir_digest", "guard", "init_study",
           "open_study", "resolve", "valid_id"]
