"""Shared fixtures.

Unit tests run anywhere. Tests that need the pipeline output (`python -m kapacitet all`)
are skipped when `data/processed` is empty, so a fresh clone still gets a green run.

Tests of the engine itself do not need the pipeline: `grid_model` runs them on the tiny grid
committed in tests/fixtures/grid_tiny, with the synthetic profiles of
tests/fixtures/synthetic_profiles.py, and on the reference network as well when it is built.
"""

from __future__ import annotations

import contextlib
import functools
import importlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import pytest

# The inbox's worker decides applications in the real queue; tests drive `inbox.process` themselves.
os.environ["KAPACITET_INBOX_WORKER"] = "0"
# Starlette's TestClient sends `Host: testserver`; the guard reads its hosts when the app is built.
os.environ.setdefault("KAPACITET_ALLOWED_HOSTS", "testserver")

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


@pytest.fixture(autouse=True)
def _a_fresh_rate_limit():
    """Every TestClient is the same client address: each test starts with the guard's windows empty."""
    from kapacitet.api.guard import LIMITER

    LIMITER.reset()


@contextlib.contextmanager
def another_server(data_dir: Path):
    """A process that holds data/requests/server.lock of the data folder `data_dir`, as a running
    server does (kapacitet.api.records.claim_server_lock), until the block ends; yields its PID.

    The PID is the one the process prints: on Windows a virtual environment's python.exe starts the
    interpreter as a child, so Popen's own PID is the launcher's. The block ends by killing both."""
    import subprocess
    import time

    from kapacitet import procs

    code = ("import os; from kapacitet.api import records; records.claim_server_lock(); "
            "print('held', os.getpid(), flush=True); import time; time.sleep(60)")
    env = {**os.environ, "KAPACITET_DATA_DIR": str(data_dir)}
    proc = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, text=True)
    pid = None
    try:
        word, _, pid_text = proc.stdout.readline().strip().partition(" ")
        assert word == "held", "the other process could not take the lock"
        pid = int(pid_text)
        yield pid
    finally:
        procs.kill_tree(proc.pid)
        if pid is not None:
            procs.kill_tree(pid)
        proc.kill()
        proc.wait()
        proc.stdout.close()
        for _ in range(250):                  # gone, and the OS has let go of its lock
            if pid is None or not procs.alive(pid):
                break
            time.sleep(0.02)


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


# `from .paths import DATA` copies the folder into the importing module, so moving `paths.DATA`
# does not move it there. Such imports are found in the package's source, so a new one is patched
# without anyone adding it to a list.
_BY_VALUE = re.compile(r"^from \.+paths import (\([^)]*\)|.+)$", re.M)


@functools.cache
def bound_by_value() -> dict[str, frozenset[str]]:
    """Every kapacitet module that binds `DATA` or `PROCESSED` at import, with the names it binds."""
    import kapacitet

    root = Path(kapacitet.__file__).resolve().parent
    out: dict[str, frozenset[str]] = {}
    for path in sorted(root.rglob("*.py")):
        for match in _BY_VALUE.finditer(path.read_text(encoding="utf-8")):
            names = set(re.split(r"[\s,()]+", match.group(1))) & {"DATA", "PROCESSED"}
            if names:
                module = ".".join(path.relative_to(root.parent).with_suffix("").parts)
                out[module] = frozenset(names)
    return out


@contextlib.contextmanager
def processed_at(folder: Path, weather_years: tuple[int, ...] | None = None):
    """Read `folder` as data/processed, and its parent as the data folder, for the duration, with
    every cache emptied on both ends.

    `kapacitet.paths.processed()` reads `PROCESSED` when it is called, so patching that one name
    moves every engine reader. The modules that copied `DATA` or `PROCESSED` at import
    (`bound_by_value`) are imported first and patched after, so the patch is undone on exit even for
    a module whose first import would otherwise have happened inside the context. What the engine
    writes under DATA (the request's injection cache, the queue, the records, the DSO's settings)
    therefore lands next to `folder`, never in the real data folder. RAW stays where it is: an
    imported network shares the reference network's downloads too.

    `weather_years` replaces the span in config/site.toml, so a folder with profiles for fewer
    years than the reference network is still read whole; the reference year becomes its last.
    The span is a first and a last year, so the years must be contiguous. The TOML reader is
    patched, not `config.settings`, because about twenty modules import `settings` by value.
    """
    from kapacitet import cache, config, paths

    folder = Path(folder)
    assert folder.name == "processed", f"{folder} is not the processed/ folder of a data folder"
    moved = {"PROCESSED": folder, "DATA": folder.parent}
    modules = {importlib.import_module(name): names for name, names in bound_by_value().items()}
    if weather_years:
        years = tuple(sorted(weather_years))
        assert years == tuple(range(years[0], years[-1] + 1)), f"site.toml holds a span, not these years: {years}"
    with pytest.MonkeyPatch.context() as mp:
        cache.clear_all()
        for name, value in moved.items():
            mp.setattr(paths, name, value)
        for module, names in modules.items():
            for name in names:
                mp.setattr(module, name, moved[name])
        if weather_years:
            read = config._read_toml

            def site_with_years(name: str) -> dict:
                cfg = read(name)
                if name == "site.toml":
                    cfg = {**cfg, "time": {**cfg["time"], "weather_first_year": years[0],
                                           "weather_last_year": years[-1], "reference_year": years[-1]}}
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


def on_tiny(request) -> bool:
    """Whether a test parametrized by `grid_model` is running on the tiny grid."""
    return request.node.callspec.params.get("grid_model") == "tiny"


# ------------------------------------------------------------------ shared engine scenarios

def stress_instance(model, seed: int = 42, hours: int = 150):
    """A stressed scenario for `doe.compute` on `model`'s grid: every customer wants 25-100 % of
    its rating against 0.2-2.5 kW of load, and the last third of them are non-firm.

    Returns (arrays, nonfirm, cap). test_doe_safety.py and test_grid_tiny.py both use it, so
    what one asserts about the stress is about the scenario the other runs.
    """
    from kapacitet.engine import doe

    rng = np.random.default_rng(seed)
    cap = doe.customer_caps()
    want = cap[:, None] * rng.uniform(0.25, 1.0, size=(model.C, hours))
    load = rng.uniform(0.2, 2.5, size=(model.C, hours))
    p_net = want - load
    q = -0.05 * np.clip(p_net, 0.0, None)
    nonfirm = np.arange(model.C)[int(model.C * 2 / 3):]
    return {"p_net": p_net, "q": q}, nonfirm, cap


def violations_beyond_firm(model, arrays: dict, nonfirm: np.ndarray, r, vm_slack=None) -> int:
    """The re-check of a `DoeResult` over the whole year, written out from scratch so it does not
    go through `doe.verify_full_year`, which `doe.compute` already ran: every hour solved with the
    published envelope held, then again with the non-firm customers exporting nothing, counting the
    hours the first breaks and the second does not (envelopes cannot fix those). A firm state
    without a converged answer excuses nothing."""
    nf = np.asarray(r.nonfirm, dtype=int)
    assert set(nf.tolist()) == set(np.asarray(nonfirm).tolist())
    p = np.array(arrays["p_net"], dtype=float)
    p[nf] = np.minimum(p[nf], r.envelope(p.shape[1]))
    held = model.violations(model.solve(p, arrays["q"], vm_slack), p).export_side
    p_firm = np.array(arrays["p_net"], dtype=float)
    p_firm[nf] = np.minimum(p_firm[nf], 0.0)
    vf = model.violations(model.solve(p_firm, arrays["q"], vm_slack), p_firm)
    return int((held & ~(vf.export_side & ~vf.nonconverged)).sum())
