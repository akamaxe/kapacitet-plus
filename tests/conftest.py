"""Shared fixtures.

Unit tests run anywhere. Tests that need the pipeline output (`python -m kapacitet all`)
are skipped when `data/processed` is empty, so a fresh clone still gets a green run.

Tests of the engine itself do not need the pipeline: `grid_model` runs them on the tiny grid
committed in tests/fixtures/grid_tiny, with the synthetic profiles of
tests/fixtures/synthetic_profiles.py, and on the reference network as well when it is built.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

# The inbox's worker decides applications in the real queue; tests drive `inbox.process` themselves.
os.environ["KAPACITET_INBOX_WORKER"] = "0"

from kapacitet.paths import processed

HAS_GRID = processed("grid", "net.json").exists()
HAS_RESULTS = processed("results", "showcase.json").exists()

needs_grid = pytest.mark.skipif(not HAS_GRID, reason="no built network (run: python -m kapacitet build)")
needs_results = pytest.mark.skipif(not HAS_RESULTS, reason="no results (run: python -m kapacitet all)")


@pytest.fixture(scope="session")
def model():
    from kapacitet.engine.netmodel import get_model

    return get_model()


@pytest.fixture(scope="session")
def results():
    def load(name: str) -> dict:
        return json.loads(processed("results", f"{name}.json").read_text(encoding="utf-8"))

    return load


@pytest.fixture(autouse=True)
def _seals_in_a_temporary_folder(tmp_path, monkeypatch):
    """No test writes the real chain: seals go to the test's own folder (kapacitet.seals)."""
    from kapacitet import seals

    monkeypatch.setattr(seals, "folder", lambda: tmp_path / "seals")


def _has_operation() -> bool:
    from kapacitet.paths import processed_ro

    return processed_ro("results", "operation", "index.json").exists()


needs_operation = pytest.mark.skipif(not _has_operation(),
                                     reason="run: python -m kapacitet compute --stage extra --only operation")


# ------------------------------------------------------------------ the tiny grid, without data

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TINY_GRID = FIXTURES / "grid_tiny"


def fixture_module(path: Path, name: str):
    """Import a helper from tests/fixtures by its path (the folder is not a package)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


synthetic_profiles = fixture_module(FIXTURES / "synthetic_profiles.py", "synthetic_profiles")


@contextlib.contextmanager
def processed_at(folder: Path, weather_years: tuple[int, ...] | None = None):
    """Read `folder` as data/processed for the duration, with every cache emptied on both ends.

    `kapacitet.paths.processed()` reads `PROCESSED` when it is called, so patching that one name
    moves every engine reader; the modules that imported the value itself are patched too.
    `weather_years` replaces the span in config/site.toml, so a folder with profiles for fewer
    years than the reference network is still read whole.
    """
    from kapacitet import cache, config, paths

    folder = Path(folder)
    with pytest.MonkeyPatch.context() as mp:
        cache.clear_all()
        mp.setattr(paths, "PROCESSED", folder)
        for name in ("kapacitet.api.main", "kapacitet.seals"):
            module = sys.modules.get(name)
            if module is not None and hasattr(module, "PROCESSED"):
                mp.setattr(module, "PROCESSED", folder)
        if weather_years:
            read = config._read_toml

            def site_with_years(name: str) -> dict:
                cfg = read(name)
                if name == "site.toml":
                    cfg = {**cfg, "time": {**cfg["time"], "weather_first_year": min(weather_years),
                                           "weather_last_year": max(weather_years),
                                           "reference_year": max(weather_years)}}
                return cfg

            mp.setattr(config, "_read_toml", site_with_years)
            config.settings.cache_clear()
        try:
            yield folder
        finally:
            cache.clear_all()
            config.settings.cache_clear()


@pytest.fixture(scope="session")
def tiny_processed(tmp_path_factory) -> Path:
    """A data/processed folder for the tiny grid: the network as `kapacitet import` builds it,
    its roofs and the synthetic profiles. Built once per session, in a temporary folder."""
    import pandas as pd

    from kapacitet.grid.builder import save
    from kapacitet.importer.build import build_from_export
    from kapacitet.importer.validate import read_export, validate

    data = read_export(TINY_GRID)
    report = validate(data)
    assert report.usable, report.text()
    network, _ = build_from_export(data)
    folder = tmp_path_factory.mktemp("tiny") / "processed"
    with processed_at(folder, synthetic_profiles.YEARS):
        save(network)
        synthetic_profiles.write(folder, network.customers, pd.read_csv(TINY_GRID / "roofs.csv"))
    return folder


@contextlib.contextmanager
def tiny_grid(folder: Path):
    """The engine reading the tiny grid's folder (from `tiny_processed`) and only its weather years."""
    with processed_at(folder, synthetic_profiles.YEARS) as path:
        yield path


@pytest.fixture(scope="module", params=["tiny", pytest.param("real", marks=needs_grid)])
def grid_model(request):
    """The network model, on the tiny grid everywhere and on the reference network where it is built.

    For the tiny grid the engine reads its folder until the module's last test has run, so
    anything a test calls (`doe.customer_caps`, the scenario readers) sees the same network.
    """
    from kapacitet.engine.netmodel import get_model

    if request.param == "real":
        yield get_model()
        return
    with tiny_grid(request.getfixturevalue("tiny_processed")):
        yield get_model()
