"""The tiny grid and its synthetic profiles, which let the engine's tests run without data.

The fixture is only worth something if it stays what the safety tests assume: the committed
CSV files are exactly what make.py writes, the export passes the data-quality check, and every
reader the engine uses on a weather year finds its file with the shape it expects.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import numpy as np
import pytest
from conftest import (TINY_GRID, bound_by_value, fixture_module, processed_at, stress_instance, synthetic_profiles,
                      tiny_grid, violations_beyond_firm)

from kapacitet import cache
from kapacitet.engine.compliance import CONSTRAINT
from kapacitet.timeindex import hours_in_year

# What `DoeResult.binding` says bound each critical hour, by name.
CODE = {name: code for code, name in CONSTRAINT.items()}


def test_make_py_writes_the_committed_files_byte_for_byte(tmp_path):
    make = fixture_module(TINY_GRID / "make.py", "grid_tiny_make")
    written = make.write(tmp_path)
    assert {p.name for p in written} == {"buses.csv", "lines.csv", "transformer.csv", "customers.csv", "roofs.csv"}
    for path in written:
        assert path.read_bytes() == (TINY_GRID / path.name).read_bytes(), \
            f"{path.name} differs: run python tests/fixtures/grid_tiny/make.py and commit the result"


def test_the_export_is_complete_and_shaped_as_the_tests_assume():
    from kapacitet.importer.validate import read_export, validate

    data = read_export(TINY_GRID)
    report = validate(data)
    assert report.usable and report.score == 100, report.text()
    cust = data["customers"]
    assert len(cust) == 20 and cust["bus"].is_unique
    assert set(cust["fuse_a"]) == {20, 25, 35}
    assert float(data["transformer"]["sn_mva"].iloc[0]) == 0.1
    assert set(data["lines"]["std_type"]) == {"NAYY 4x150 SE", "NAYY 4x50 SE"}


@pytest.fixture(scope="module")
def tiny(tiny_processed):
    with tiny_grid(tiny_processed) as folder:
        yield folder


def test_the_engine_reads_every_profile_of_both_years(tiny):
    from kapacitet.config import settings
    from kapacitet.engine.run import background_masks
    from kapacitet.engine.scenario import Scenario, customers

    assert settings().weather_years == synthetic_profiles.YEARS
    C = len(customers())
    assert C == 20 and customers()["kwp"].notna().all()
    hp, ev = background_masks(C)
    for year in synthetic_profiles.YEARS:
        arr = Scenario(year=year, pv=np.ones(C, dtype=bool), hp=hp, ev=ev).arrays()
        assert arr["p_net"].shape == (C, hours_in_year(year))
        assert np.isfinite(arr["p_net"]).all()
        assert arr["pv"].max() > 0 and arr["load"].min() > 0


def test_hosting_forecast_and_curtailment_run_on_the_synthetic_profiles(tiny):
    from kapacitet.engine import forecast, hosting, run
    from kapacitet.engine.scenario import adoption_order

    winter = hosting.winter_design_load()
    assert winter["year"] in synthetic_profiles.YEARS and winter["peak_kw"] > 0
    assert (hosting.min_daytime_load() > 0).all()
    # the leap year is planned from the other year's calendar, and the other way round
    for year in synthetic_profiles.YEARS:
        assert forecast.pv_upper_bound(year).shape == (20, hours_in_year(year))

    model = run.model()
    hp, ev = run.background_masks(model.C)
    order = adoption_order(0, model.C)
    ms = hosting.static_adopters(model, order, hp, ev)
    assert 0 < ms < model.C
    z = run.curtailment(order, ms, model.C, (2025,), hp, ev)
    assert z["potential_kwh"] > 0 and 0 < z["z_pct"] < 100


def test_at_the_top_of_the_mv_band_the_long_feeder_binds_the_voltage(tiny):
    """The stress of test_doe_safety.py with 1.06 pu on the MV side: voltage-bound in every critical hour."""
    from kapacitet.engine import doe
    from kapacitet.engine.netmodel import get_model

    model = get_model(vm_slack=1.06)
    arrays, nonfirm, _ = stress_instance(model)
    r = doe.compute(model, arrays, nonfirm)
    assert r.verified_violations == 0 and r.info["critical_hours"] > 0
    assert (r.binding == CODE["voltage"]).all()


@pytest.mark.parametrize("max_iter", [4, 1])
def test_envelopes_verify_when_the_linear_model_is_optimistic(tiny, max_iter):
    """What makes envelopes safe when the linear model is wrong: margin tightening and back-off.

    The true model's stress converges in one iteration, so neither runs in test_doe_safety.py.
    Here the allocation sees half the true voltage sensitivity, so its first envelopes overshoot
    the voltage limit: with room to iterate the margins tighten, with one iteration the
    back-off bisects. The critical hours still come from the true screen, and every power flow
    is the true one, so the published envelope must verify all the same."""
    from kapacitet.engine import doe
    from kapacitet.engine.netmodel import get_model

    true = get_model()
    optimistic = copy.copy(true)
    optimistic.dv_dp = true.dv_dp * 0.5
    optimistic.export_critical_hours = true.export_critical_hours
    arrays, nonfirm, _ = stress_instance(true)
    r = doe.compute(optimistic, arrays, nonfirm, max_iter=max_iter)
    if max_iter > 1:
        assert r.iterations > 1, "the margins must have been tightened"
    else:
        assert r.backoff_hours > 0, "the back-off must have run"
    assert r.verified_violations == 0
    assert violations_beyond_firm(true, arrays, nonfirm, r) == 0


def test_the_api_reads_the_tiny_grid(tiny):
    from fastapi.testclient import TestClient

    from kapacitet.api import main

    r = TestClient(main.app).get("/api/v1/network")
    assert r.status_code == 200, r.text
    assert sum(f["properties"]["layer"] == "house" for f in r.json()["features"]) == 20
    assert main._engine()["model"].C == 20


def test_processed_at_moves_every_copy_of_the_data_folder_and_restores_it(tmp_path):
    """Writes under DATA land next to the processed folder in use, and nothing stays moved after."""
    import importlib

    from kapacitet import paths

    bound = bound_by_value()
    assert {"kapacitet.seals", "kapacitet.engine.request", "kapacitet.engine.queue", "kapacitet.dso",
            "kapacitet.api.records", "kapacitet.api.inbox"} <= set(bound)
    modules = {importlib.import_module(name): names for name, names in bound.items()}
    before = {name: getattr(paths, name) for name in ("DATA", "PROCESSED")}
    folder = tmp_path / "processed"
    with processed_at(folder):
        assert paths.PROCESSED == folder and paths.DATA == tmp_path
        for module, names in modules.items():
            for name in names:
                assert getattr(module, name) == getattr(paths, name), f"{module.__name__}.{name}"
    for module, names in modules.items():
        for name in names:
            assert getattr(module, name) == before[name], f"{module.__name__}.{name} stayed moved"


def test_clear_all_forgets_everything_read_from_the_data_folder(tiny):
    from kapacitet.api import main
    from kapacitet.engine import forecast, hosting, netmodel, queue, run, scenario

    cache.clear_all()
    hp, ev = run.background_masks(len(scenario.customers()))
    scenario.year_profiles(2025)
    hosting.min_daytime_load()
    hosting.winter_design_load()
    forecast.pv_upper_bound(2025)
    forecast.load_lower_bound(2025, hp, ev)
    netmodel.get_model()
    run.model()
    main._engine()
    queue._CACHE["version"] = 1
    named = (scenario.customers, scenario.year_profiles, netmodel.get_model, hosting._min_daytime_load,
             hosting.winter_design_load, forecast.pv_upper_bound)
    assert all(fn.cache_info().currsize >= 1 for fn in named)
    assert forecast._LOAD_BOUND and run._MODEL is not None and main._ENGINE

    cache.clear_all()
    memos = [reset.__self__ for reset in cache._CLEARERS if hasattr(getattr(reset, "__self__", None), "cache_info")]
    assert set(named) <= set(memos)
    assert [m.__qualname__ for m in memos if m.cache_info().currsize] == []
    assert not forecast._LOAD_BOUND and run._MODEL is None and not main._ENGINE
    assert queue._CACHE["version"] is None


def test_every_cache_in_the_package_is_in_the_registry():
    """A plain lru_cache on data would survive cache.clear_all() and answer about another grid.

    Allowed: cache.py, which implements the registry; config.py, which reads config/ and is
    reset by processed_at itself; economics.hourly, which reads data/raw (shared by every grid).
    """
    import kapacitet

    root = Path(kapacitet.__file__).resolve().parent
    allowed = {"cache.py", "config.py", "engine/economics.py"}
    pattern = re.compile(r"\blru_cache\b|\bfunctools\.cache\b|^from functools import .*\bcache\b|@cache\b", re.M)
    found = sorted(path.relative_to(root).as_posix() for path in root.rglob("*.py")
                   if pattern.search(path.read_text(encoding="utf-8")))
    assert [f for f in found if f not in allowed] == [], "use kapacitet.cache.memo, so clear_all() empties it"
