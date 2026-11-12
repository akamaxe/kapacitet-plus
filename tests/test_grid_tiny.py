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


def test_a_network_no_limit_is_reached_on_still_has_its_record(tiny):
    """With one small system on a network, no hour is critical and the envelopes never act. The
    result then had no record of what bound each hour at all, which the audit trail reads as a
    result from before that record existed: the build of such a network (a strong example
    network of a few houses) stopped there. The record is empty, and so is the trail."""
    from kapacitet.engine import compliance, doe, run
    from kapacitet.engine.scenario import Scenario

    model = run.model()
    hp, ev = run.background_masks(model.C)
    pv = np.zeros(model.C, dtype=bool)
    pv[0] = True
    arrays = Scenario(year=2025, pv=pv, hp=hp, ev=ev).arrays()
    one = np.array([0])
    r = doe.compute(model, arrays, one)
    assert len(r.hours) == 0 and r.info["critical_hours"] == 0 and r.verified_violations == 0
    assert r.binding is not None and r.binding.shape == (0,) and r.binding_line.shape == (0,)
    trail = compliance.audit_trail(model, r, arrays, 2025)
    assert len(trail.events) == 0 and trail.hours_with_curtailment == 0 and trail.energy_kwh == 0.0
    # nobody to limit at all
    none = doe.compute(model, arrays, np.array([], dtype=int))
    assert none.binding is not None and len(none.binding) == 0
    assert len(compliance.audit_trail(model, none, arrays, 2025).events) == 0


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
    doc = r.json()
    houses = [f for f in doc["features"] if f["properties"]["layer"] == "house"]
    cables = [f for f in doc["features"] if f["properties"]["layer"] == "cable"]
    assert len(houses) == 20
    assert main._engine()["model"].C == 20
    # the tiny grid came as tables, with no outline for a house and no route for a cable: each
    # house is a square of its footprint around its connection point, each cable a straight line
    # between its buses, and the answer says how many it drew
    assert all(f["geometry"]["type"] == "Polygon" and f["properties"]["drawn"] is True for f in houses)
    assert all(f["geometry"]["type"] == "LineString" and len(f["geometry"]["coordinates"]) == 2
               and f["properties"]["drawn"] is True for f in cables)
    assert doc["drawing"] == {"layout": "geographic", "houses_drawn": 20, "cables_drawn": len(cables)}
    for f in houses:
        ring = f["geometry"]["coordinates"][0]
        lon, lat = f["properties"]["lon"], f["properties"]["lat"]
        assert min(p[0] for p in ring) < lon < max(p[0] for p in ring)
        assert min(p[1] for p in ring) < lat < max(p[1] for p in ring)
    # the squares stay inside what the map frames, give or take a house
    west, south, east, north = doc["bbox"]
    assert all(west - 3e-4 < p[0] < east + 3e-4 and south - 2e-4 < p[1] < north + 2e-4
               for f in houses for p in f["geometry"]["coordinates"][0])


def test_an_example_network_is_drawn_as_a_schematic(tiny, monkeypatch):
    """An example network's coordinates are a layout around a point, not places: the answer says
    `schematic`, and the map leaves the real streets out."""
    from fastapi.testclient import TestClient

    from kapacitet import datasets as ds
    from kapacitet.api import main

    monkeypatch.setattr(ds, "current_origin", lambda: {"kind": "template", "template": "dickert-short", "synthetic": True})
    main._network.cache_clear()
    try:
        doc = TestClient(main.app).get("/api/v1/network").json()
        assert doc["drawing"]["layout"] == "schematic" and doc["drawing"]["houses_drawn"] == 20
        assert any("example network" in s for s in doc["provenance"]["synthetic"])
        assert not any(s.startswith("LV network topology") for s in doc["provenance"]["real"])
    finally:
        main._network.cache_clear()          # the next reader gets the network as it is


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
    queue._CACHE["queue"] = {"version": 1}
    named = (scenario.customers, scenario.year_profiles, netmodel.get_model, hosting._min_daytime_load,
             hosting.winter_design_load, forecast.pv_upper_bound)
    assert all(fn.cache_info().currsize >= 1 for fn in named)
    assert forecast._LOAD_BOUND and run._MODEL is not None and main._ENGINE

    cache.clear_all()
    memos = [reset.__self__ for reset in cache._CLEARERS if hasattr(getattr(reset, "__self__", None), "cache_info")]
    assert set(named) <= set(memos)
    assert [m.__qualname__ for m in memos if m.cache_info().currsize] == []
    assert not forecast._LOAD_BOUND and run._MODEL is None and not main._ENGINE
    assert not queue._CACHE


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


def test_the_transformer_cases_are_the_networks_own_neighbours(tiny):
    """The sensitivity varied the transformer between 0.25 and 0.63 MVA at 10 kV, the units next to
    the reference network's. On a network at 20 kV that swap is another network, whose power flow
    has no solution, and the build stopped. The cases are the standard units next to the network's
    own, for the same voltages."""
    import pandapower as pp

    from kapacitet.engine import run, run_extra
    from kapacitet.engine.netmodel import standard_neighbours
    from kapacitet.study.cases import next_transformer

    net = run.model().net                                  # the tiny grid: 0.1 MVA at 10 kV
    near = standard_neighbours(net)
    assert near["smaller"] is None and near["larger"] == {"std_type": "0.25 MVA 10/0.4 kV", "sn_mva": 0.25}
    assert next_transformer(net) == near["larger"]
    assert [c[:4] for c in run_extra.trafo_cases(net)] == [
        ("trafo", "Transformer rating", "0.25 MVA", {"trafo_type": "0.25 MVA 10/0.4 kV"})]

    def cases_for(sn_mva: float, vn_hv_kv: float) -> list[tuple]:
        other = pp.create_empty_network()
        hv, lv = pp.create_bus(other, vn_hv_kv), pp.create_bus(other, 0.4)
        pp.create_transformer_from_parameters(other, hv, lv, sn_mva=sn_mva, vn_hv_kv=vn_hv_kv, vn_lv_kv=0.4,
                                              vk_percent=4.0, vkr_percent=1.0, pfe_kw=0.0, i0_percent=0.0)
        return [(c[2], c[3]["trafo_type"]) for c in run_extra.trafo_cases(other)]

    # the reference network's unit keeps the two cases its published sensitivity has
    assert cases_for(0.4, 10.0) == [("0.25 MVA", "0.25 MVA 10/0.4 kV"), ("0.63 MVA", "0.63 MVA 10/0.4 kV")]
    assert cases_for(0.4, 20.0) == [("0.25 MVA", "0.25 MVA 20/0.4 kV"), ("0.63 MVA", "0.63 MVA 20/0.4 kV")]
    assert cases_for(0.63, 10.0) == [("0.4 MVA", "0.4 MVA 10/0.4 kV")]            # nothing larger
    assert cases_for(0.4, 11.0) == []                                              # no standard unit at 11 kV
    labels = [c[0] for c in run_extra.tornado_cases(net)]
    assert labels[:4] == ["mv_voltage", "mv_voltage", "v_margin", "v_margin"] and labels[4] == "trafo"
    assert labels[5:] == ["dcac", "dcac", "minload", "minload", "static_pv", "static_pv", "household", "household"]
