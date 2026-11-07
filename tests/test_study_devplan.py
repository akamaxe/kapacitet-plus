"""The inputs for the network development plan (study/devplan.py): profiles, flexibility need and the
investment list, on stand-in stations whose station processes are written by the test. Only the
last test needs pipeline output (the flexibility on the reference network)."""

from __future__ import annotations

import json
import re
import zlib

import numpy as np
import pandas as pd
import pytest
from conftest import needs_results

from kapacitet.study import StudyError, csvio, decision, devplan, open_study
from kapacitet.study.config import METHOD_VERSION, StudyConfigError, parse
from kapacitet.study.publish import PLACEHOLDER

Q = "2026Q4"
YEARS = (2024, 2025)          # a leap year and the reference year
SCENARIO = """
scenario_year = 2030
pv_share = 0.5
ev_share = 0.4
source = "nätföretagets prognos 2026-10"
"""


def make(tmp_path, costs: str = "", scenario: str = ""):
    from test_study_decision import make as make_decision

    study = make_decision(tmp_path, costs)
    if scenario:
        study.toml.write_text(study.toml.read_text(encoding="utf-8").replace(
            "scenario_year = 2030\n", scenario.lstrip("\n"), 1), encoding="utf-8")
        study = open_study(study.root, allow_git=True)
    return study


def series(sid: str, year: int, kind: str) -> np.ndarray:
    """A station's stand-in hourly profile: different in every station, year and kind."""
    h = 8784 if year % 4 == 0 else 8760
    rng = np.random.default_rng(zlib.crc32(f"{sid}/{year}/{kind}".encode()))
    return rng.uniform(0.0, 50.0 if kind == "load" else 30.0, h)


def flex_block(hours: int, energy: float, limit: str | None = "transformer", mv: float = 1.06) -> dict:
    per_year = {str(y): {"hours": hours, "energy_kwh": energy, "unresolved_hours": 0} for y in YEARS}
    return {"hours_mean": float(hours), "hours_max": hours, "hours_reference_year": hours,
            "energy_kwh_mean": energy, "energy_kwh_max": energy, "energy_kwh_reference_year": energy,
            "unresolved_hours": 0, "by_limit": {limit: hours * len(YEARS)} if hours and limit else {},
            "limit": limit if hours else None, "mv_pu": mv, "per_year": per_year}


def station_doc(study, sid: str, today=(0, 0.0), scenario=(10, 500.0)) -> dict:
    arrays = {f"{k}_{y}": series(sid, y, k) for y in YEARS for k in ("load", "generation")}
    scen = devplan.scenario_doc(study)
    flex = {"today": {"generation": flex_block(*today), "consumption": flex_block(0, 0.0, mv=0.98)}}
    if scen is not None:
        flex["scenario"] = {"generation": flex_block(*scenario), "consumption": flex_block(4, 40.0, "voltage", 0.98)}
    doc = {"schema": devplan.SCHEMA, "station": sid, "quarter": Q, "method_version": METHOD_VERSION,
           "fingerprint": "f", "scenario_digest": devplan.value_digest(scen), "scenario": scen,
           "years": list(YEARS), "reference_year": 2025, "mv_band_pu": [0.98, 1.06],
           "energy": {"load_kwh_reference_year": float(arrays["load_2025"].sum()),
                      "generation_kwh_reference_year": float(arrays["generation_2025"].sum())},
           "flexibility": flex}
    return doc, arrays


def runner_for(calls: list | None = None, docs: dict | None = None):
    def run(study, sid, action, batch):
        assert action == "devplan" and batch == Q
        if calls is not None:
            calls.append(sid)
        doc, arrays = (docs or {}).get(sid) or station_doc(study, sid)
        folder = study.station_dir(sid) / "devplan"
        folder.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(folder / f"{batch}.npz", **arrays)
        (folder / f"{batch}.json").write_text(json.dumps(doc), encoding="utf-8")
        return 0
    return run


# ---------------------------------------------------------------- the profiles

def test_the_profiles_sum_to_the_stations_energy_and_the_study_is_their_sum(tmp_path):
    study = make(tmp_path)
    out = devplan.devplan(study, Q, runner=runner_for())
    folder = study.runs / Q / "devplan" / "profiles"
    stations = ["S1", "S2", "S3", "S4"]
    frames = {sid: pd.read_parquet(folder / f"{sid}.parquet") for sid in stations}
    for sid, df in frames.items():
        doc = json.loads((study.station_dir(sid) / "devplan" / f"{Q}.json").read_text(encoding="utf-8"))
        assert len(df) == 8760 and list(df.columns) == devplan.PROFILE_COLUMNS
        assert df["load_kw"].sum() == pytest.approx(doc["energy"]["load_kwh_reference_year"])
        assert df["generation_kw"].sum() == pytest.approx(doc["energy"]["generation_kwh_reference_year"])
        # each hour's percentiles over the years lie between the years' values
        both = np.vstack([devplan.align(series(sid, y, "load"), y, 2025) for y in YEARS])
        assert np.allclose(df["load_p50_kw"], both.mean(axis=0))
        assert (df["load_p90_kw"] >= df["load_p50_kw"] - 1e-9).all()
    total = pd.read_parquet(folder / "study.parquet")
    assert np.allclose(total["load_kw"], sum(f["load_kw"] for f in frames.values()))
    assert np.allclose(total["generation_kw"], sum(f["generation_kw"] for f in frames.values()))
    # the study's percentiles are those of the sum, never a sum of percentiles
    sums = np.vstack([sum(devplan.align(series(s, y, "load"), y, 2025) for s in stations) for y in YEARS])
    assert np.allclose(total["load_p90_kw"], np.percentile(sums, 90, axis=0))
    # interval start in UTC: the year in UTC+1 begins at 23:00 the day before
    assert total["time_utc"].iloc[0] == pd.Timestamp("2024-12-31 23:00", tz="UTC")
    assert str(total["time_local"].iloc[0]) == "2025-01-01 00:00:00"
    # the Swedish dialect beside the machine copy
    raw = (folder / "S1.csv").read_bytes()
    assert raw.startswith("﻿".encode("utf-8")) and b";" in raw.splitlines()[0]
    machine = csvio.read(folder / "S1.machine.csv")
    assert len(machine) == 8760 and machine[0]["time_utc"] == "2024-12-31T23:00Z"
    assert sum(float(r["load_kw"]) for r in machine) == pytest.approx(frames["S1"]["load_kw"].sum(), rel=1e-6)
    assert out["reference_year"] == 2025


def test_a_leap_day_is_dropped_or_filled_from_the_day_before():
    leap = np.arange(8784, dtype=float)
    assert len(devplan.align(leap, 2024, 2025)) == 8760
    assert devplan.align(leap, 2024, 2025)[59 * 24] == 60 * 24       # 1 March follows 28 February
    plain = np.arange(8760, dtype=float)
    filled = devplan.align(plain, 2025, 2024)
    assert len(filled) == 8784 and (filled[59 * 24:60 * 24] == plain[58 * 24:59 * 24]).all()
    assert (devplan.align(plain, 2025, 2025) == plain).all()


# ---------------------------------------------------------------- flexibility and investments

def test_a_station_without_costs_still_gets_profiles_and_flexibility(tmp_path):
    from test_study_decision import runner_for as options_runner

    study = make(tmp_path, scenario=SCENARIO)
    decision.options(study, Q, runner=options_runner({}))
    out = devplan.devplan(study, Q, runner=runner_for())
    folder = study.runs / Q / "devplan"
    assert (folder / "profiles" / "S1.parquet").is_file() and (folder / "profiles" / "S1.machine.csv").is_file()
    s1 = [r for r in out["flexibility"] if r["station_id"] == "S1"]
    assert {(r["state"], r["side"]) for r in s1} == {(s, d) for s in devplan.STATES for d in devplan.SIDES}
    assert all(r["status"] == "ok" for r in s1)
    gen = next(r for r in s1 if r["state"] == "scenario" and r["side"] == "generation")
    assert gen["hours_per_year_max"] == 10 and gen["binding_component"] == "transformer" and gen["published_kw"] == 120.0
    inv = next(r for r in out["investments"] if r["station_id"] == "S1")
    assert inv["option"] == "transformer_swap" and inv["cost_sek"] is None and inv["cost_status"] == "not entered"
    assert inv["year_needed"] == 2030 and inv["needed_because"] == "scenario"
    machine = csvio.read(folder / "investments.machine.csv")
    assert next(r for r in machine if r["station_id"] == "S1")["cost_status"] == "not entered"
    # the study's rows: energy summed in each year, hours left per station
    study_gen = next(r for r in out["flexibility"] if r["station_id"] == devplan.STUDY_ID
                     and r["state"] == "scenario" and r["side"] == "generation")
    assert study_gen["energy_kwh_per_year_mean"] == pytest.approx(4 * 500.0)
    assert study_gen["hours_per_year_mean"] is None and study_gen["stations_with_need"] == 4
    assert not out["options_missing"]


def test_the_scenario_is_not_entered_without_shares_and_a_share_needs_a_source(tmp_path):
    study = make(tmp_path)
    assert devplan.scenario_doc(study) is None
    out = devplan.devplan(study, Q, runner=runner_for())
    assert {r["status"] for r in out["flexibility"] if r["state"] == "scenario"} == {"not_entered"}
    assert out["options_missing"] and all(r["option"] == "not_computed" for r in out["investments"])
    with pytest.raises((StudyConfigError, StudyError, ValueError)):
        parse({"devplan": {"pv_share": 0.5}})
    assert parse({"devplan": {"pv_share": 0.5, "source": "prognos"}}).devplan.entered()


def test_a_station_is_computed_again_only_when_its_build_or_the_scenario_changes(tmp_path):
    study = make(tmp_path)
    calls: list = []
    devplan.devplan(study, Q, runner=runner_for(calls))
    assert sorted(calls) == ["S1", "S2", "S3", "S4"]
    calls.clear()
    devplan.devplan(study, Q, runner=runner_for(calls))
    assert calls == []
    study.toml.write_text(study.toml.read_text(encoding="utf-8").replace(
        "scenario_year = 2030\n", SCENARIO.lstrip("\n"), 1), encoding="utf-8")
    devplan.devplan(open_study(study.root, allow_git=True), Q, runner=runner_for(calls))
    assert sorted(calls) == ["S1", "S2", "S3", "S4"]


def _flex(today: int = 0, scenario: int = 0, year: int = 2030) -> list[dict]:
    rows = []
    for state, h in (("today", today), ("scenario", scenario)):
        rows.append({"state": state, "status": "ok", "hours_per_year_max": h,
                     "scenario_year": year if state == "scenario" else None})
    return rows


def test_the_year_an_investment_is_needed_follows_its_reasons():
    facts = {"station_id": "S1", "name": "S"}
    swap = {"reinforce_status": "ok", "trafo_now_kva": "400", "trafo_next_type": "0.63 MVA 20/0.4 kV",
            "trafo_next_kva": "630", "reinforce_cost_sek": "400000", "cost_price_level_year": "2026",
            "wait_years": "4", "recommendation": ""}
    row = devplan.investment_row(facts, swap, _flex(today=3), 2026)
    assert (row["year_needed"], row["needed_because"]) == (2026, "violations_today")
    assert row["cost_sek"] == 400000.0 and row["cost_status"] == "entered" and row["cost_price_level_year"] == 2026
    assert devplan.investment_row(facts, {**swap, "recommendation": "reinforce"}, _flex(), 2026)["needed_because"] \
        == "recommended_now"
    row = devplan.investment_row(facts, {**swap, "recommendation": "condition"}, _flex(scenario=5, year=2032), 2026)
    assert (row["year_needed"], row["needed_because"]) == (2030, "end_of_deferral")
    row = devplan.investment_row(facts, swap, _flex(scenario=5, year=2040), 2026)
    assert row["year_needed"] is None and row["needed_because"] == "not_within_horizon"
    assert devplan.investment_row(facts, {**swap, "reinforce_status": "no_larger_unit"}, _flex(), 2026)["option"] == "none"
    assert devplan.investment_row(facts, None, _flex(), 2026)["needed_because"] == "options_not_computed"


def test_the_scenario_adds_devices_up_to_its_shares():
    C = 10
    has_pv = np.zeros(C, dtype=bool)
    has_pv[[0, 1]] = True
    order = np.array([5, 6, 7, 8, 9, 2, 3, 4])
    hp = np.zeros(C, dtype=bool)
    hp[9] = True
    ev = np.zeros(C, dtype=bool)
    pv, hp_s, ev_s = devplan.scenario_masks({"pv_share": 0.5, "ev_share": 0.3, "hp_share": None}, has_pv, order, hp, ev)
    assert pv.sum() + has_pv.sum() == 5 and pv[[5, 6, 7]].all()
    assert (hp_s == hp).all() and ev_s.sum() == 3


def test_summarize_reports_the_years_and_the_binding_kind():
    per = {2024: {"hours": 2, "energy_kwh": 10.0, "unresolved_hours": 0, "by_limit": {"voltage": 2}},
           2025: {"hours": 6, "energy_kwh": 30.0, "unresolved_hours": 1, "by_limit": {"transformer": 5, "voltage": 1}}}
    s = devplan.summarize(per, 2025)
    assert s["hours_mean"] == 4.0 and s["hours_max"] == 6 and s["hours_reference_year"] == 6
    assert s["energy_kwh_mean"] == 20.0 and s["energy_kwh_reference_year"] == 30.0
    assert s["limit"] == "transformer" and s["unresolved_hours"] == 1 and set(s["per_year"]) == {"2024", "2025"}


# ---------------------------------------------------------------- the Swedish summary

def test_the_summary_has_no_digit_outside_its_placeholders(tmp_path):
    text = PLACEHOLDER.sub("", devplan.TEMPLATE.read_text(encoding="utf-8"))
    found = [(i + 1, line) for i, line in enumerate(text.splitlines()) if re.search(r"\d", line)]
    assert not found, f"figures typed into {devplan.TEMPLATE.name}: {found}"
    typed = {k: v for k, v in devplan.SV.items() if re.search(r"\d", devplan.SLOT.sub("", v))}
    assert not typed, f"figures typed into the Swedish sentences: {typed}"
    study = make(tmp_path, scenario=SCENARIO)
    devplan.devplan(study, Q, runner=runner_for())
    md = (study.runs / Q / "devplan" / "devplan_sv.md").read_text(encoding="utf-8")
    assert devplan.REGULATION in md and "{" not in md and "flexibility.csv" in md and "investments.csv" in md
    assert "50 %" in md and "som i dag" in md and "2026–2035" in md


def test_the_command_line_runs_the_devplan(tmp_path, capsys):
    from kapacitet.pipeline import main

    study = make(tmp_path)
    devplan.devplan(study, Q, runner=runner_for())      # each station current: the command spawns none
    main(["study", "devplan", str(study.root), "--run", Q, "--allow-git"])
    out = capsys.readouterr().out
    assert "4 stations" in out and "study options" in out


# ---------------------------------------------------------------- on the reference network

@needs_results
def test_the_flexibility_need_clears_every_hour_it_counts():
    from kapacitet.engine import run
    from kapacitet.engine import station as es
    from kapacitet.engine.scenario import Scenario

    m = run.model()
    hp, ev = run.background_masks(m.C)
    arr = Scenario(year=2025, pv=np.ones(m.C, dtype=bool), hp=hp, ev=ev).arrays()
    p, q, pv = arr["p_net"].astype(float), arr["q"].astype(float), arr["pv"].astype(float)
    r = es.flexibility_need(m, p, q, pv, "export")
    crit = es.violating_hours(m, p, q, "export")
    assert r["hours"] == len(crit) > 0 and r["limit"] in es.EXPORT_LIMITS
    assert 0.0 < r["energy_kwh"] <= pv[:, crit].sum()
    # without anything to turn down, no hour is cleared
    none = es.flexibility_need(m, p, q, np.zeros_like(pv), "export")
    assert none["unresolved_hours"] == none["hours"] and none["energy_kwh"] == 0.0
