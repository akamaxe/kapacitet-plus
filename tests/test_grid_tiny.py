"""The tiny grid and its synthetic profiles, which let the engine's tests run without data.

The fixture is only worth something if it stays what the safety tests assume: the committed
CSV files are exactly what make.py writes, the export passes the data-quality check, and every
reader the engine uses on a weather year finds its file with the shape it expects.
"""

from __future__ import annotations

import numpy as np
import pytest
from conftest import TINY_GRID, fixture_module, synthetic_profiles, tiny_grid

from kapacitet import cache
from kapacitet.timeindex import hours_in_year


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
    rng = np.random.default_rng(42)
    cap = doe.customer_caps()
    p_net = cap[:, None] * rng.uniform(0.25, 1.0, size=(model.C, 150)) - rng.uniform(0.2, 2.5, size=(model.C, 150))
    q = -0.05 * np.clip(p_net, 0.0, None)
    r = doe.compute(model, {"p_net": p_net, "q": q}, np.arange(model.C)[int(model.C * 2 / 3):])
    assert r.verified_violations == 0 and r.info["critical_hours"] > 0
    assert (r.binding == 0).all()


def test_clear_all_forgets_what_was_read_from_the_processed_folder(tiny):
    from kapacitet.engine import netmodel, run, scenario

    cache.clear_all()
    scenario.customers()
    netmodel.get_model()
    run.model()
    assert scenario.customers.cache_info().currsize == 1
    assert netmodel.get_model.cache_info().currsize == 1
    cache.clear_all()
    assert scenario.customers.cache_info().currsize == 0
    assert netmodel.get_model.cache_info().currsize == 0
    assert run._MODEL is None
