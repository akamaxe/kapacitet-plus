"""A DSO's static method as a profile (src/kapacitet/method_profile.py, task 2.5 of the technical review).

Without data: what a profile sets, what it needs, where a project keeps it, and that the DSO's own
limits (a fixed load, a voltage-rise limit, a loading limit) bind on the tiny network. With the
reference results: the default profile is today's static study, and the solar output at 80 % gives
the static case of the sensitivity (results/tornado.json, adoption order 9).
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from conftest import needs_results
from kapacitet import method_profile as mp
from kapacitet.config import set_overrides


def test_the_default_profile_is_todays_static_study():
    assert mp.MethodProfile().overrides() == {}
    p = mp.MethodProfile(pv_factor=0.8, load_rule="no_load", v_rise_limit_pct=3.0, max_loading_pct=90.0,
                         source="nätbolagets anvisning 2026")
    assert p.overrides() == {"static_pv_factor": 0.8, "static_load_rule": "no_load",
                             "static_v_rise_limit_pct": 3.0, "static_max_loading_pct": 90.0}


def test_a_profile_says_who_set_it_and_a_fixed_load_has_no_default():
    with pytest.raises(ValueError, match="source"):
        mp.MethodProfile(pv_factor=0.8)
    with pytest.raises(ValueError, match="fixed_load_kw"):
        mp.MethodProfile(load_rule="fixed_per_customer", source="x")
    assert mp.MethodProfile(load_rule="fixed_per_customer", fixed_load_kw=0.3, source="x").overrides() == {
        "static_load_rule": "fixed_per_customer", "static_fixed_load_kw": 0.3}


def test_a_project_keeps_its_profile_beside_its_settings(tmp_path):
    assert mp.load(tmp_path) == mp.MethodProfile()
    profile = mp.MethodProfile(pv_factor=0.9, source="test")
    target = mp.save(profile, tmp_path)
    assert target == tmp_path / "requests" / "method_profile.json"
    assert mp.load(tmp_path) == profile and json.loads(target.read_text())["pv_factor"] == 0.9


def _static(m, order, hp, ev, overrides) -> int:
    from kapacitet.engine import hosting

    set_overrides(overrides, layer="study")
    try:
        return hosting.static_adopters(m, order, hp, ev)
    finally:
        set_overrides(None, layer="study")


def test_the_dsos_own_limits_bind_on_the_tiny_network(tiny_processed):
    from conftest import tiny_grid

    from kapacitet.engine import run
    from kapacitet.engine.netmodel import get_model
    from kapacitet.engine.scenario import adoption_order

    with tiny_grid(tiny_processed):
        m = get_model()
        hp, ev = run.background_masks(m.C)
        order = adoption_order(0, m.C)
        today = _static(m, order, hp, ev, {})
        assert today > 0
        # a rise limit and a loading limit can only take installations away, never add them
        assert _static(m, order, hp, ev, {"static_v_rise_limit_pct": 0.5}) <= today
        assert _static(m, order, hp, ev, {"static_max_loading_pct": 50.0}) <= today
        assert _static(m, order, hp, ev, {"static_v_rise_limit_pct": 0.01}) < today
        # a fixed load of the DSO's in place of the lowest simulated one
        heavy = _static(m, order, hp, ev, {"static_load_rule": "fixed_per_customer", "static_fixed_load_kw": 3.0})
        assert heavy >= today


@needs_results
def test_the_profile_reproduces_the_sensitivitys_static_case():
    from kapacitet.engine import doe, run
    from kapacitet.engine.scenario import adoption_order

    from kapacitet.paths import processed_ro

    tornado = json.loads(processed_ro("results", "tornado.json").read_text(encoding="utf-8"))
    case = next(c for c in tornado["cases"] if c["key"] == "static_pv" and "0.8" in c["level"])
    m = run.model()
    hp, ev = run.background_masks(m.C)
    order = adoption_order(tornado["order"], m.C)
    cap = doe.customer_caps()
    base = _static(m, order, hp, ev, mp.MethodProfile().overrides())
    assert base == tornado["base"]["static_adopters"]
    assert float(cap[order[:base]].sum()) == pytest.approx(tornado["base"]["static_kw"])
    low = _static(m, order, hp, ev, mp.MethodProfile(pv_factor=0.8, source="test").overrides())
    assert low == case["static_adopters"]
    assert float(cap[order[:low]].sum()) == pytest.approx(case["static_kw"]) == pytest.approx(520.158, abs=0.01)
    assert np.isfinite(case["static_kw"])


def test_every_process_of_the_folder_reads_the_profile_as_part_of_the_study_layer(tmp_path, monkeypatch):
    """A worker the engine starts reads config's study layer from scratch: the profile is in it."""
    from kapacitet import config, paths

    mp.save(mp.MethodProfile(pv_factor=0.85, source="test"), tmp_path)
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.delenv("KAPACITET_STUDY", raising=False)
    config.reload()
    try:
        assert config.A("static_pv_factor") == 0.85
        assert config.A("static_load_rule") == "min_daytime_summer"
    finally:
        monkeypatch.undo()
        config.reload()
    assert config.A("static_pv_factor") == 1.0


def test_the_profile_is_read_and_written_over_http(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from kapacitet import paths
    from kapacitet.api.main import app

    monkeypatch.setattr(paths, "DATA", tmp_path)
    c = TestClient(app)
    got = c.get("/api/v1/method-profile").json()
    assert got["profile"] == got["default"] and got["in_use"]["static_pv_factor"] == 1.0
    bad = c.put("/api/v1/method-profile", json={"pv_factor": 0.8})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "method_profile"
    assert not (tmp_path / "requests" / "method_profile.json").exists()
    ok = c.put("/api/v1/method-profile", json={"pv_factor": 0.8, "source": "nätbolaget, 2026-11"})
    assert ok.status_code == 200 and ok.json()["changes"] == {"static_pv_factor": 0.8}
    assert "compute" in ok.json()["command"]
    assert mp.load(tmp_path).pv_factor == 0.8
