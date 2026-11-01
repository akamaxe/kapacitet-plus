"""Filesystem layout of the project."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"
# KAPACITET_DATA_DIR points the whole project at another data folder, such as an unpacked bundle
# (python -m kapacitet unbundle) or a volume mounted into a container.
DATA = Path(os.environ["KAPACITET_DATA_DIR"]).resolve() if os.environ.get("KAPACITET_DATA_DIR") else ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
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
