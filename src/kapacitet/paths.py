"""Filesystem layout of the project.

Three roots, each one an environment variable away from its default:

    HOME   KAPACITET_HOME      where the imported networks live (datasets/) and which one is in
                               use (active.json); the project's data/ by default
    DATA   KAPACITET_DATA_DIR  the data the engine reads and writes: processed/ and requests/;
                               an unpacked bundle, a container volume, or one imported network
    RAW    KAPACITET_RAW_DIR   downloaded sources (weather, prices, OSM); an imported network
                               shares the reference network's, so nothing is fetched twice

Two more for a study's station (study/), whose build runs with DATA on the station's folder, HOME
on the study and RAW on the study's own downloads:

    REFERENCE  KAPACITET_REFERENCE_DIR  the reference network's data, whose weather years and
                                        household pool a station reuses when its study has none
                                        (and says so); HOME by default
    (study)    KAPACITET_STUDY          the study folder whose study.toml config.py merges in

And one for the installation's signing key (seals.py), which a study's station processes share
with the process that starts them:

    SEAL_KEYS  KAPACITET_SEAL_KEY_DIR   the Ed25519 key that signs every seal; HOME/keys by default
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"


def _env_dir(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).resolve() if value else default


DATA = _env_dir("KAPACITET_DATA_DIR", ROOT / "data")
# the supervisor passes its own data folder down as HOME, so a network's server and its build
# know where the reference network (its households, its downloads) lives
HOME = _env_dir("KAPACITET_HOME", DATA)
RAW = _env_dir("KAPACITET_RAW_DIR", DATA / "raw")
PROCESSED = DATA / "processed"
REFERENCE = _env_dir("KAPACITET_REFERENCE_DIR", HOME)
DATASETS = HOME / "datasets"
SEAL_KEYS = _env_dir("KAPACITET_SEAL_KEY_DIR", HOME / "keys")
WEB_DIST = ROOT / "web" / "dist"


def processed(*parts: str) -> Path:
    """Path inside data/processed, creating the parent folder."""
    path = PROCESSED.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def processed_ro(*parts: str) -> Path:
    """Path inside data/processed for reading only: unlike `processed`, it creates nothing."""
    return PROCESSED.joinpath(*parts)


def raw(*parts: str) -> Path:
    """Path inside data/raw, creating the parent folder."""
    path = RAW.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
