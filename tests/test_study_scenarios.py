"""Module 4's electrification scenarios (study/scenarios.py): `study scenarios` on a study of stand-in
stations with a stand-in for each station's process, the growth and the battery as pure functions,
and the portfolio's trend column. The last test grows the reference network's own capacity with no
growth and needs the pipeline's output."""

from __future__ import annotations

import json

import numpy as np
import pytest
from conftest import needs_results

from kapacitet.config import A
from kapacitet.engine.scenario import adoption_order, mask_from_share
from kapacitet.study import csvio, decision, open_study
from kapacitet.study import scenarios as sc
from kapacitet.study.config import METHOD_VERSION, StudyConfigError, parse

Q = "2026Q4"
GROWTH = """
year = 2028
pv_per_year = 0.1
ev_per_year = 0.05
source = "nätföretagets prognos 2026-10"
"""


def make(tmp_path, growth: str = ""):
    from test_study_decision import make as make_decision

    study = make_decision(tmp_path)
    if growth:
        study.toml.write_text(study.toml.read_text(encoding="utf-8").replace(
            "\nyear = 2030\n", "\n" + growth.lstrip("\n"), 1), encoding="utf-8")
        study = open_study(study.root, allow_git=True)
    return study


def station_doc(study, sid: str, gen_left=(120.0, 80.0, 0.0), violation_from: int | None = None,
                fingerprint: str = "f") -> dict:
    g = sc.growth_doc(study, int(Q[:4]))
    rows = []
    for i, year in enumerate(range(g["start_year"], g["year"] + 1)):
        v = 3 if violation_from is not None and year >= violation_from else 0
        rows.append({"year": year, "years_from_start": i,
                     "counts": {"pv": 4 + i, "ev": 2 + i, "hp": 3, "battery": i}, "new": {"pv": i, "ev": i, "hp": 0, "battery": i},
                     "generation": {"status": "ok", "kw_left": gen_left[min(i, len(gen_left) - 1)], "violation_hours": 0,
                                    "binding_mv_pu": 1.06},
                     "consumption": {"status": "ok", "kw_left": 50.0 - 10 * i, "violation_hours": v}})
    return {"schema": sc.SCHEMA, "station": sid, "quarter": Q, "method_version": METHOD_VERSION,
            "fingerprint": fingerprint, "scenario_digest": sc.value_digest(g), "scenario": g,
            "weather_years": [2024, 2025], "mv_band_pu": [0.98, 1.06], "customers": 40,
            "published": {"generation_kw": 120.0, "consumption_kw": 50.0, "consumption_device": "ev"},
            "years": rows, **sc.first_years(rows)}


def runner_for(calls: list | None = None, docs: dict | None = None):
    def run(study, sid, action, batch):
        assert action == "scenarios" and batch == Q
        if calls is not None:
            calls.append(sid)
        doc = (docs or {}).get(sid) or station_doc(study, sid)
        for path in (study.station_dir(sid) / "scenarios" / f"{batch}.json",
                     study.station_dir(sid) / "processed" / "results" / "scenarios.json"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(doc), encoding="utf-8")
        return 0
    return run


# ---------------------------------------------------------------- the growth

def test_rates_left_out_are_the_sourced_defaults_and_say_so(tmp_path):
    g = sc.growth_doc(make(tmp_path), 2026)
    assert (g["start_year"], g["year"]) == (2026, 2030)
    assert g["rates"] == {d: A(sc.DEFAULTS[d]) for d in sc.DEVICES}
    assert set(g["basis"].values()) == {"default"} and sc.rates_label(g) == "default"
    assert g["battery_kwh"] == A(sc.BATTERY_KWH)
    mixed = sc.growth_doc(make(tmp_path / "m", GROWTH), 2026)
    assert mixed["rates"]["pv"] == 0.1 and mixed["basis"]["pv"] == "entered"
    assert mixed["rates"]["hp"] == A(sc.DEFAULTS["hp"]) and mixed["basis"]["hp"] == "default"
    assert sc.rates_label(mixed) == "mixed" and mixed["source"].startswith("nätföretagets")
    # an end year before the quarter's is today's grid only
    assert sc.growth_doc(make(tmp_path / "late", GROWTH), 2029)["year"] == 2029


def test_a_growth_rate_needs_its_source_and_a_share():
    with pytest.raises(StudyConfigError, match="scenarios"):
        parse({"scenarios": {"pv_per_year": 0.02}})
    with pytest.raises(StudyConfigError, match="ev_per_year"):
        parse({"scenarios": {"ev_per_year": 1.5, "source": "x"}})
    assert not parse({}).scenarios.entered() and parse({}).scenarios.year == 2030


def test_a_station_with_no_equipment_grows_from_the_default_shares():
    C = 60
    none = np.zeros(C, dtype=bool)                       # no PV, no records: today's shares stand
    hp = mask_from_share(adoption_order(101, C), A("today_hp_share"))
    ev = mask_from_share(adoption_order(202, C), A("today_ev_share"))
    order = adoption_order(7, C)
    rates = {d: A(sc.DEFAULTS[d]) for d in sc.DEVICES}
    today = sc.grow(rates, 0, none, order, hp, ev)
    assert not today["pv"].any() and not today["battery"].any()
    assert (today["hp"] == hp).all() and (today["ev"] == ev).all()
    for dy in (1, 4):
        y = sc.grow(rates, dy, none, order, hp, ev)
        assert y["pv"].sum() == round(rates["pv"] * C * dy)
        assert y["hp"].sum() == hp.sum() + round(rates["hp"] * C * dy) and (y["hp"] >= hp).all()
        assert y["ev"].sum() == ev.sum() + round(rates["ev"] * C * dy) and (y["ev"] >= ev).all()
        assert not (y["battery"] & ~y["pv"]).any()       # a battery only where there is PV
        assert y["battery"].sum() == min(round(rates["battery"] * C * dy), y["pv"].sum())
        assert (y["pv_new"] == y["pv"]).all() and set(np.flatnonzero(y["pv"])) == set(order[:y["pv"].sum()])
    # the growth never passes every customer, and existing PV is kept apart from the new
    full = sc.grow({"pv": 1.0, "ev": 1.0, "hp": 1.0, "battery": 1.0}, 3, none, order[:10], hp, ev)
    assert full["pv"].sum() == 10 and full["hp"].all() and full["ev"].all() and full["battery"].sum() == 10
    has = np.zeros(C, dtype=bool)
    has[order[-1]] = True
    y = sc.grow(rates, 2, has, order[:-1], hp, ev)
    assert y["pv"][order[-1]] and not y["pv_new"][order[-1]]


def test_a_self_consumption_battery_never_adds_export_or_import():
    rng = np.random.default_rng(1)
    pv = np.clip(rng.normal(3, 3, (4, 500)), 0, None)
    load = rng.uniform(0.2, 4, (4, 500))
    d = sc.self_consumption(pv, load, kwh=10.0, kw=5.0, eta_in=0.95, eta_out=0.95)
    net, after = pv - load, pv - load + d
    assert (np.maximum(after, 0) <= np.maximum(net, 0) + 1e-9).all()       # export never more
    assert (np.maximum(-after, 0) <= np.maximum(-net, 0) + 1e-9).all()     # import never more
    assert (np.abs(d) <= 5.0 + 1e-9).all() and d.min() < 0 < d.max()
    charged, given = -d[d < 0].sum(), d[d > 0].sum()
    assert given <= charged * 0.95 * 0.95 + 1e-6                          # no energy from nowhere
    assert (sc.self_consumption(np.zeros((2, 5)), np.ones((2, 5)), 10.0, 5.0, 0.9, 0.9) == 0).all()


def test_the_largest_verified_count_is_one_that_passed():
    seen = []

    def ok(n):
        seen.append(n)
        return n <= 13

    assert sc.largest_verified(2, 40, ok) == 13 and 40 in seen
    assert sc.largest_verified(0, 10, lambda n: True) == 10
    assert sc.largest_verified(5, 3, lambda n: False) == 5                 # nothing left above the start
    assert sc.added(0.03, 100, 2, 50) == 6 and sc.added(0.5, 100, 4, 50) == 50 and sc.added(0.1, 10, 0, 9) == 0


def test_the_first_years_follow_the_rows():
    rows = [{"year": 2026, "generation": {"kw_left": 10.0, "violation_hours": 0}, "consumption": {"kw_left": 5.0}},
            {"year": 2027, "generation": {"kw_left": 0.0, "violation_hours": 0}, "consumption": {"kw_left": 5.0}},
            {"year": 2028, "generation": {"kw_left": 0.0, "violation_hours": 2},
             "consumption": {"kw_left": None, "violation_hours": 0}}]
    assert sc.first_years(rows) == {"first_violation_year": 2028, "first_exhausted_year": 2027}
    assert sc.first_years(rows[:1]) == {"first_violation_year": None, "first_exhausted_year": None}


# ---------------------------------------------------------------- the command

def test_the_study_table_has_a_row_per_station_and_year(tmp_path):
    study = make(tmp_path, GROWTH)
    calls: list[str] = []
    out = sc.scenarios(study, Q, runner=runner_for(calls))
    ready = sorted(calls)
    assert ready and set(ready) == set(out["computed"])
    rows = csvio.read(out["paths"][1])
    for sid in ready:
        mine = [r for r in rows if r["station_id"] == sid]
        assert [r["year"] for r in mine] == ["2026", "2027", "2028"]
        assert {r["rates"] for r in mine} == {"mixed"} and float(mine[-1]["generation_kw_left"]) == 0.0
    others = {r["station_id"]: r["status"] for r in rows if r["station_id"] not in ready}
    assert set(others.values()) <= {"not_computed"}
    # current: nothing is computed again until the growth changes
    again: list[str] = []
    assert sc.scenarios(study, Q, runner=runner_for(again))["computed"] == [] and again == []
    study.toml.write_text(study.toml.read_text(encoding="utf-8").replace("ev_per_year = 0.05", "ev_per_year = 0.06"),
                          encoding="utf-8")
    changed = open_study(study.root, allow_git=True)
    assert sorted(sc.scenarios(changed, Q, runner=runner_for(again))["computed"]) == ready


def test_a_failed_station_is_reported_as_failed(tmp_path):
    study = make(tmp_path)
    out = sc.scenarios(study, Q, runner=lambda *a: 3)
    assert out["failed"] and all(r["status"] in ("failed", "not_computed") for r in out["rows"])


def test_the_portfolio_shows_the_scenario_when_present(tmp_path):
    study = make(tmp_path, GROWTH)
    before = {r["station_id"]: r for r in decision.portfolio_rows(study, Q)}
    assert all(r["scenarios_year"] is None for r in before.values())
    sid = next(iter(before))
    docs = {sid: station_doc(study, sid, violation_from=2027)}
    sc.scenarios(study, Q, runner=runner_for(docs=docs))
    rows = {r["station_id"]: r for r in decision.portfolio_rows(study, Q)}
    r = rows[sid]
    assert (r["scenarios_year"], r["scenarios_rates"]) == (2028, "mixed")
    assert (r["scenarios_generation_kw"], r["scenarios_consumption_kw"]) == (0.0, 30.0)
    assert (r["scenarios_first_violation_year"], r["scenarios_first_exhausted_year"]) == (2027, 2028)
    assert "scenario_violates" in r["flags"]
    md = decision.portfolio(study, Q)["paths"][-1].read_text(encoding="utf-8")
    assert "2028: 0 / 30; överträdelse från 2027" in md
    # a scenario of another build is not shown
    stale = station_doc(study, sid, fingerprint="older build")
    (study.station_dir(sid) / "processed" / "results" / "scenarios.json").write_text(json.dumps(stale), encoding="utf-8")
    assert next(x for x in decision.portfolio_rows(study, Q) if x["station_id"] == sid)["scenarios_year"] is None


def test_the_command_line_runs_the_scenarios(tmp_path, capsys):
    from kapacitet.pipeline import main

    study = make(tmp_path)
    sc.scenarios(study, Q, runner=runner_for())          # each station current: the command spawns none
    main(["study", "scenarios", str(study.root), "--run", Q, "--allow-git"])
    out = capsys.readouterr().out
    assert "2026-2030" in out and "default growth rates" in out


# ---------------------------------------------------------------- on the reference network

@needs_results
def test_no_growth_reproduces_todays_capacity(tmp_path, monkeypatch):
    from kapacitet import paths
    from kapacitet.config import set_overrides
    from kapacitet.engine import request as rq
    from kapacitet.engine import run
    from kapacitet.engine import station as es
    from kapacitet.study import station as st

    zero = 'year = 2027\npv_per_year = 0.0\nev_per_year = 0.0\nhp_per_year = 0.0\nbattery_per_year = 0.0\nsource = "test"\n'
    study = make(tmp_path / "s", zero)
    monkeypatch.setattr(st, "isolated", lambda *_: True)
    years, band = [2025], (1.0, 1.0)
    m = run.model()
    hp, ev = run.background_masks(m.C)
    try:
        gen = es.generation_capacity(band, years, 1, 50.0, hp, ev)
        cons = es.consumption_capacity(rq.model_at(1.0), "ev", years, 1, 50.0, hp, ev)
        cons["consumption_device"] = "ev"
        cap = tmp_path / "results" / "capacity.json"
        cap.parent.mkdir(parents=True)
        cap.write_text(json.dumps({"years": years, "mv_band_pu": list(band), "generation": gen, "consumption": cons,
                                   "fingerprint": "x"}, default=run._default), encoding="utf-8")
        monkeypatch.setattr(st, "capacity_path", lambda: cap)
        monkeypatch.setattr(paths, "DATA", tmp_path / "out")
        doc = sc.station_scenarios(study, "s1", Q)
        grown = sc.station_scenarios(make(tmp_path / "g", "year = 2028\n"), "s1", Q)    # the defaults
    finally:
        set_overrides(None, layer="study")
    assert gen["status"] == "ok" and cons["status"] == "ok"
    assert [r["year"] for r in doc["years"]] == [2026, 2027]
    for r in doc["years"]:
        assert r["new"] == {"pv": 0, "ev": 0, "hp": 0, "battery": 0}
        assert r["generation"]["kw_left"] == pytest.approx(gen["kw"])
        assert r["consumption"]["kw_left"] == pytest.approx(cons["kw"])
        assert r["generation"]["violation_hours"] == 0 == r["consumption"]["violation_hours"]
    assert (tmp_path / "out" / "scenarios" / f"{Q}.json").is_file()
    # with growth: the devices add up, a battery only with PV, and never more than today's capacity
    assert sc.rates_label(grown["scenario"]) == "default" and grown["years"][0]["generation"]["kw_left"] == pytest.approx(gen["kw"])
    last = grown["years"][-1]
    assert last["new"]["pv"] == round(A(sc.DEFAULTS["pv"]) * m.C * 2) > 0
    assert 0 < last["counts"]["battery"] <= last["counts"]["pv"]
    for r in grown["years"]:
        for side, pub in (("generation", gen["kw"]), ("consumption", cons["kw"])):
            assert r[side]["kw_left"] <= pub + 1e-9
    assert json.loads((cap.with_name("scenarios.json")).read_text(encoding="utf-8"))["years"] == grown["years"]
