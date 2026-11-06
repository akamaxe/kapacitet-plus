"""Module 4's start (study/decision.py): `study portfolio` and `study options`, on a study of stand-in
stations whose capacity.json is written by the test, and a stand-in for each station's options
process. No pipeline output is needed, so CI runs all of it, except the last test, which sets the
options against the demo's published reinforcement and business case."""

from __future__ import annotations

import json
import re

import pytest
from conftest import needs_results

from kapacitet.engine import summaries
from kapacitet.study import collect, csvio, init_study, open_study
from kapacitet.study import decision
from kapacitet.study.config import METHOD_VERSION, StudyConfigError, parse
from kapacitet.study.publish import PLACEHOLDER

Q = "2026Q4"
NOW = {"std_type": "0.4 MVA 20/0.4 kV", "sn_mva": 0.4}
NEXT = {"std_type": "0.63 MVA 20/0.4 kV", "sn_mva": 0.63}
COSTS = """
transformer_swap_sek = { "630" = 400000 }
curtailment_sek_per_kwh = 1.5
price_level_year = 2026
source = "ramavtal 2026-01"
"""


def make(tmp_path, costs: str = "", approval: str = ""):
    from test_study_publish import STATIONS, put

    study = init_study(tmp_path / "nb", "Testnät AB", allow_git=True)
    text = study.toml.read_text(encoding="utf-8").replace("[costs]\n", "[costs]\n" + costs.lstrip("\n"), 1)
    if approval:
        text = text.replace('# approval_reference = "..."', f'approval_reference = "{approval}"')
    study.toml.write_text(text, encoding="utf-8")
    study = open_study(study.root, allow_git=True)
    for sid, v in STATIONS.items():
        put(study, sid, v["customers"], v["lon"], v["lat"], v["gen"], v["cons"])
    collect.collect(study, Q)
    return study


def doc(gen_kw=200.0, cons_kw=160.0, condition=None, nxt=NEXT) -> dict:
    out = {"schema": decision.OPTIONS_SCHEMA, "method_version": METHOD_VERSION, "fingerprint": "f",
           "approval_reference": "", "years": [2024, 2025], "orders": 5, "current": NOW, "next": nxt,
           "condition": condition or {"status": "not_approved"}}
    if nxt is not None:
        out["reinforce"] = {"generation": {"status": "ok", "kw": gen_kw, "limit": "voltage", "full_year_violations": 0},
                            "consumption": {"status": "ok", "kw": cons_kw, "limit": "cable"}}
    return out


def runner_for(docs: dict, calls: list | None = None):
    def run(study, sid, action, batch):
        assert action == "options" and batch == Q
        if calls is not None:
            calls.append(sid)
        path = study.station_dir(sid) / "options" / f"{batch}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(docs.get(sid, doc())), encoding="utf-8")
        return 0
    return run


# ---------------------------------------------------------------- the portfolio

def test_the_ranking_is_by_margin_per_customer_and_deterministic(tmp_path):
    study = make(tmp_path)
    out = decision.portfolio(study, Q)
    order = [r["station_id"] for r in out["rows"]]
    # S3 has no consumption figure; then per customer S1 80/40, S4 150/60, S2 25/3
    assert order == ["S3", "S1", "S4", "S2"]
    assert [r["rank"] for r in out["rows"]] == [1, 2, 3, 4]
    assert out["rows"][1]["margin_kw_per_customer"] == pytest.approx(2.0)
    first = [p.read_bytes() for p in out["paths"]]
    again = decision.portfolio(study, Q)
    assert [p.read_bytes() for p in again["paths"]] == first
    # ties go by station id, whatever order they come in
    rows = [{"station_id": s, "margin_kw_per_customer": m} for s, m in (("B", 1.0), ("A", 1.0), ("C", None))]
    assert [r["station_id"] for r in decision.rank(rows)] == ["C", "A", "B"]
    assert [r["station_id"] for r in decision.rank(list(reversed(rows)))] == ["C", "A", "B"]


def test_a_correction_lowers_the_margin_the_portfolio_ranks_by(tmp_path):
    study = make(tmp_path)
    (study.runs / Q / "corrections.csv").write_text(
        "station_id;field;value;reason;by\r\nS2;consumption_kw;3;a cable is weaker;R\r\n", encoding="utf-8")
    rows = decision.portfolio_rows(study, Q)
    s2 = next(r for r in rows if r["station_id"] == "S2")
    assert s2["consumption_kw"] == 3.0 and s2["corrected"] and s2["rank"] == 2


def test_the_portfolio_reads_the_risk_and_the_trend_of_each_station(tmp_path):
    study = make(tmp_path)
    results = study.station_dir("S1") / "processed" / "results"
    cap = json.loads((results / "capacity.json").read_text(encoding="utf-8"))
    cap["generation"]["by_end"] = {"0.98": {"kw": 140.0}, "1.06": {"kw": 120.0}}
    (results / "capacity.json").write_text(json.dumps(cap), encoding="utf-8")
    (results / "peak_check.json").write_text(json.dumps(
        {"velander_kw": 90.0, "modelled_peak_kw": 80.0,
         "measured": {"customers": 10, "max_kw": 33.0, "velander_kw": 30.0}}), encoding="utf-8")
    (results / "scenarios.json").write_text(json.dumps(
        {"year": 2025, "cases": [{"static_ok": True, "doe_ok": True}, {"static_ok": False, "doe_ok": True},
                                 {"static_ok": False, "doe_ok": False}]}), encoding="utf-8")
    (study.inputs / "equipment.csv").write_text(
        "customer_id,pv_kwp,connected_on\nc1,5,2023-04-01\nc2,7.5,2024-06-01\nc3,,2024-09-01\n", encoding="utf-8")
    batch = study.root / "cases" / "b1"
    csvio.write(batch, "verdicts", [
        {"case_id": "k1", "station": "S1"}, {"case_id": "k2", "station": "S1", "decision_dso": "firm"},
        {"case_id": "k3", "station": "S1"}, {"case_id": "k4", "station": "S1", "error": "station_stale"},
        {"case_id": "k5", "station": "S2"}], ["case_id", "station", "decision_dso", "error"], "sv-excel")
    csvio.write(study.root / "cases" / "sync-1", "sync_report", [
        {"case_id": "k3", "station": "S1", "status": "approved"}, {"case_id": "k6", "station": "S4", "status": "pending"}],
        ["case_id", "station", "status"], "rfc4180")
    rows = {r["station_id"]: r for r in decision.portfolio_rows(study, Q)}
    s1 = rows["S1"]
    assert (s1["generation_kw_mv_low"], s1["generation_kw_mv_high"]) == (140.0, 120.0)
    assert s1["measured_peak_ratio"] == pytest.approx(1.1) and "measured_above_velander" in s1["flags"]
    assert (s1["scenarios_cases"], s1["scenarios_static_ok"], s1["scenarios_envelopes_ok"]) == (3, 1, 2)
    # k2 decided, k3 approved by the sync, k4 not checked: only k1 waits
    assert (s1["pending_cases"], rows["S2"]["pending_cases"], rows["S4"]["pending_cases"]) == (1, 1, 1)
    assert rows["S3"]["pending_cases"] == 0
    # the stand-in stations have no customers file, so the whole table counts for each
    assert s1["equipment_rows"] == 3 and s1["equipment_pv_kwp"] == pytest.approx(12.5)
    assert (s1["equipment_first_connected_on"], s1["equipment_last_connected_on"]) == ("2023-04-01", "2024-09-01")
    assert s1["equipment_per_year"] == "2023: 1; 2024: 2"
    md = decision.portfolio(study, Q)["paths"][-1].read_text(encoding="utf-8")
    assert "Testnät AB" in md and "1 / 2 av 3" in md and "{{" not in md


# ---------------------------------------------------------------- the options

def test_a_station_without_costs_gets_not_entered_and_no_recommendation(tmp_path):
    study = make(tmp_path)
    out = decision.options(study, Q, runner=runner_for({}))
    s1 = next(r for r in out["rows"] if r["station_id"] == "S1")
    assert s1["reinforce_status"] == "ok" and s1["reinforce_cost_sek"] is None
    assert s1["reinforce_cost_status"] == decision.NOT_ENTERED == "not entered"
    assert s1["curtailment_price_status"] == "not entered"
    assert s1["recommendation"] is None and s1["why_none"] == "cost_not_entered"
    assert all(r["recommendation"] is None for r in out["rows"])
    machine = csvio.read(study.runs / Q / "options.machine.csv")
    assert next(r for r in machine if r["station_id"] == "S1")["reinforce_cost_status"] == "not entered"
    note = (study.runs / Q / "options" / "S1.sv.md").read_text(encoding="utf-8")
    assert "inte angivet" in note and "{{" not in note and "{" not in note
    # Ei's norm values stand beside it only as a reference, never as the price
    assert s1["reference_ei_500_kva_sek"] is not None and "endast som referens" in note


def test_the_options_follow_their_inputs(tmp_path):
    study = make(tmp_path, COSTS, approval="Styrelsebeslut 2026-10")
    cond = {"status": "ok", "budget": "installation", "kw": 180.0, "adopters": 30, "z_pct": 1.2,
            "z_customer_max_pct": 2.9, "curtailed_kwh_per_year": 1000.0}
    docs = {"S1": {**doc(condition=cond), "approval_reference": "Styrelsebeslut 2026-10"},
            "S2": {**doc(condition={**cond, "curtailed_kwh_per_year": 2_000_000.0}),
                   "approval_reference": "Styrelsebeslut 2026-10"},
            "S3": {**doc(), "approval_reference": "Styrelsebeslut 2026-10"},
            "S4": {**doc(gen_kw=260.0, cons_kw=150.0), "approval_reference": "Styrelsebeslut 2026-10"}}
    csvio.write(study.root / "cases" / "b1", "verdicts", [{"case_id": "k1", "station": "S1"},
                                                          {"case_id": "k2", "station": "S2"}],
                ["case_id", "station", "decision_dso", "error"], "sv-excel")
    calls: list[str] = []
    out = decision.options(study, Q, runner=runner_for(docs, calls))
    rows = {r["station_id"]: r for r in out["rows"]}
    assert sorted(calls) == ["S1", "S2", "S3", "S4"]
    # a pending case and envelopes cheap enough: connect with conditions
    assert rows["S1"]["recommendation"] == "condition" and rows["S1"]["condition_net_sek_in_force"] > 0
    assert rows["S1"]["condition_curtailment_sek_per_year"] == pytest.approx(1500.0)
    # the same, curtailing so much that reinforcing now is worth more
    assert rows["S2"]["recommendation"] == "reinforce" and rows["S2"]["condition_net_sek_2028_proposed"] < 0
    # a finding (no consumption figure) and no envelopes to connect with: reinforce
    assert rows["S3"]["recommendation"] == "reinforce"
    # room on both sides and nobody waiting: wait, worth deferring the swap at every rate
    assert rows["S4"]["recommendation"] == "wait" and rows["S4"]["why_none"] is None
    assert rows["S4"]["reinforce_generation_gain_kw"] == 60.0 and rows["S4"]["reinforce_consumption_gain_kw"] == 0.0
    assert all(rows["S4"][f"wait_deferral_sek_{c}"] > 0 for c in decision.RATE_COLUMNS.values())
    note = (study.runs / Q / "options" / "S1.sv.md").read_text(encoding="utf-8")
    assert "anslut med villkor" in note and "ramavtal" in note and "400\u00a0000 SEK" in note
    assert "gräns efter bytet: spänningen" in note
    # a second run reuses what is current
    calls.clear()
    decision.options(study, Q, runner=runner_for(docs, calls))
    assert calls == []


def test_a_bigger_unit_that_adds_nothing_and_a_missing_unit_are_not_recommended():
    from kapacitet.study.config import Costs

    costs = Costs(transformer_swap_sek={"630": 400000}, curtailment_sek_per_kwh=1.0, source="avtal")
    facts = {"rank": 1, "station_id": "S", "name": "S", "customers": 10, "pending_cases": 0,
             "generation_kw": 100.0, "consumption_kw": 80.0}
    rates = {"ei_wacc_real_pct": 4.53}
    ref = {"lo": 1.0, "hi": 2.0, "year": 2022}
    flat = decision.option_row(facts, doc(gen_kw=100.0, cons_kw=80.0), costs, rates, 4, ref)
    assert flat["recommendation"] is None and flat["why_none"] == "no_gain"
    none = decision.option_row(facts, doc(nxt=None), costs, rates, 4, ref)
    assert none["reinforce_status"] == "no_larger_unit" and none["why_none"] == "no_larger_unit,cost_not_entered"
    failed = decision.option_row(facts, {"failed": 1}, costs, rates, 4, ref)
    assert failed["reinforce_status"] == "failed" and failed["recommendation"] is None
    ok = decision.option_row(facts, doc(), costs, rates, 4, ref)
    assert ok["recommendation"] == "wait" and ok["trafo_next_kva"] == 630.0


def test_a_wait_needs_a_clean_station_and_a_deferral_value():
    from kapacitet.study.config import Costs

    costs = Costs(transformer_swap_sek={"630": 400000}, curtailment_sek_per_kwh=1.0, source="avtal")
    facts = {"rank": 1, "station_id": "S", "name": "S", "customers": 10, "pending_cases": 0,
             "generation_kw": 100.0, "consumption_kw": 80.0}
    ref = {"lo": 1.0, "hi": 2.0, "year": 2022}
    d = doc(gen_kw=160.0, cons_kw=80.0)
    rates = {"ei_wacc_real_pct": 4.53}
    assert decision.option_row(facts, d, costs, rates, 4, ref)["recommendation"] == "wait"
    for k in ("generation_baseline_violation_hours", "consumption_baseline_violation_hours", "full_year_violations"):
        assert decision.option_row({**facts, k: 3}, d, costs, rates, 4, ref)["recommendation"] == "reinforce"
    # no rates: nothing to value the deferral with
    assert decision.option_row(facts, d, costs, {}, 4, ref)["recommendation"] == "reinforce"


def test_a_reinforcement_is_verified_on_both_sides():
    from kapacitet.study.config import Costs

    costs = Costs(transformer_swap_sek={"630": 400000}, curtailment_sek_per_kwh=1.0, source="avtal")
    facts = {"rank": 1, "station_id": "S", "name": "S", "customers": 10, "pending_cases": 0,
             "generation_kw": 100.0, "consumption_kw": 80.0}
    ref = {"lo": 1.0, "hi": 2.0, "year": 2022}
    for side, key, val in (("consumption", "full_year_violations", 2), ("consumption", "nonconverged_hours", 1),
                           ("generation", "nonconverged_hours", 1)):
        d = doc()
        d["reinforce"][side][key] = val
        row = decision.option_row(facts, d, costs, {"ei_wacc_real_pct": 4.53}, 4, ref)
        assert row["reinforce_status"] == "not_verified" and row["recommendation"] is None
    assert decision._brief({"status": "ok", "verification": {"nonconverged_hours": 2}})["nonconverged_hours"] == 2


def test_the_doe_block_is_reused_only_when_budget_approval_and_curtailment_match():
    ok = {"status": "ok", "budget": "installation", "curtailed_kwh_per_year": 1.0, "approval_reference": " R1 "}
    assert decision.reusable_doe(ok, "R1")
    assert not decision.reusable_doe({**ok, "budget": "aggregate"}, "R1")
    assert not decision.reusable_doe({**ok, "status": "failed"}, "R1")
    assert not decision.reusable_doe(ok, "R2")
    assert not decision.reusable_doe({k: v for k, v in ok.items() if k != "curtailed_kwh_per_year"}, "R1")


@needs_results
def test_the_reinforcement_is_computed_on_the_next_transformer_and_the_doe_at_the_installation_budget():
    from kapacitet.engine import request as rq
    from kapacitet.engine import run
    from kapacitet.engine import station as es
    from kapacitet.study.cases import next_transformer

    m = run.model()
    hp, ev = run.background_masks(m.C)
    band = (1.0, 1.0)
    nxt = next_transformer(rq.model_at(band[0]).net)
    if nxt is None:
        pytest.skip("no larger transformer")
    now = es.generation_capacity(band, [2025], 3, 50.0, hp, ev)
    new = es.generation_capacity(band, [2025], 3, 50.0, hp, ev, trafo_type=nxt["std_type"],
                                 approval_reference="R", budget="installation")
    assert now["status"] == "ok" and new["status"] == "ok" and new["kw"] != now["kw"]
    assert not decision._brief(new)["full_year_violations"]
    doe = new["doe"]
    assert doe["status"] == "ok" and doe["budget"] == "installation" and "curtailed_kwh_per_year" in doe
    assert decision.reusable_doe(doe, "R")


def test_a_price_needs_its_source_and_a_kva_key():
    with pytest.raises(StudyConfigError, match="source"):
        parse({"costs": {"transformer_swap_sek": {"630": 1.0}}})
    with pytest.raises(StudyConfigError, match="kVA"):
        parse({"costs": {"transformer_swap_sek": {"0.63 MVA": 1.0}, "source": "x"}})
    c = parse({"costs": {"transformer_swap_sek": {"0630": 1.0}, "source": "x"}}).costs
    assert c.swap_sek(0.63) == 1.0 and c.swap_sek(0.8) is None and c.swap_sek(None) is None


def _demo_inputs():
    tornado = {"order": 7, "base": {"static_kw": 413.0, "timeseries_kw": 500.0, "doe_kw": 787.0},
               "cases": [{"key": "trafo", "level": "0.63 MVA", "static_kw": 450.0, "timeseries_kw": 600.0,
                          "doe_kw": 900.0}]}
    over = {"budget": {"curtailed_sek": 2500.0, "delivered_sek": 90000.0, "kw_ac": 787.0, "kw_ac_nonfirm": 374.0},
            "price_years": [2023, 2024, 2025], "order": 7}
    cost = {"reinforce_trafo_500_sek": 156126.0, "reinforce_trafo_800_sek": 209053.0,
            "reinforce_station_800_sek": 500000.0, "reinforce_urban_supplement_sek": 100000.0,
            "reinforce_price_level_year": 2022}
    return tornado, over, cost


def _agree(tornado, over, cost, rates, horizon):
    from kapacitet.study.config import Costs

    reinf = summaries.reinforcement(tornado, over, cost)
    case = summaries.business(reinf, rates["ei_wacc_real_pct"], horizon, rates=rates)
    k = reinf["envelopes"]["curtailed_sek_per_year"]
    # the swap at the low end of the bracket, the envelopes curtailing k SEK a year (k/2 kWh at 2 SEK per kWh)
    costs = Costs(transformer_swap_sek={"630": reinf["transformer_sek"][0]}, curtailment_sek_per_kwh=2.0, source="Ei")
    facts = {"rank": 1, "station_id": "demo", "name": "demo", "customers": 120, "pending_cases": 1,
             "generation_kw": tornado["base"]["static_kw"], "consumption_kw": 100.0}
    row = decision.option_row(facts, doc(condition={"status": "ok", "budget": "installation", "kw": 787.0,
                                                    "curtailed_kwh_per_year": k / 2.0}),
                              costs, rates, horizon, {"lo": cost["reinforce_trafo_500_sek"],
                                                      "hi": cost["reinforce_trafo_800_sek"], "year": 2022})
    assert row["years_of_curtailment"] == reinf["years_of_curtailment"]["transformer"][0]
    for key, col in decision.RATE_COLUMNS.items():
        at = case["at_rates"][key]["transformer"][0]
        assert row[f"wait_deferral_sek_{col}"] == at["deferral_sek"]
        assert row[f"condition_net_sek_{col}"] == at["net_sek"]
    assert row["condition_net_sek_in_force"] == case["at_horizon"]["transformer"][0]["net_sek"]
    # by hand: C(1 - (1+r)^-n) at Ei's 4.53 % and four years
    c = reinf["transformer_sek"][0]
    r = rates["ei_wacc_real_pct"] / 100.0
    assert row["wait_deferral_sek_in_force"] == pytest.approx(c * (1 - (1 + r) ** -horizon))
    assert row["condition_curtailment_sek_per_year"] == pytest.approx(k)


def test_the_options_agree_with_the_reinforcement_summary():
    tornado, over, cost = _demo_inputs()
    rates = {"ei_wacc_real_pct": 4.53, "ei_wacc_real_pct_2028_prelim": 1.99,
             "ei_wacc_real_pct_2028_current_method": 4.57}
    _agree(tornado, over, cost, rates, 4)
    assert set(decision.RATE_COLUMNS) == set(summaries.RATES)


@needs_results
def test_the_options_agree_with_the_demos_published_reinforcement():
    from kapacitet.config import A
    from kapacitet.engine.summaries import overinstallation_summary
    from kapacitet.paths import processed

    def read(name):
        return json.loads(processed("results", name).read_text(encoding="utf-8"))

    if not processed("results", "overinstallation.json").is_file() or not processed("results", "tornado.json").is_file():
        pytest.skip("needs tornado.json and overinstallation.json: compute --stage extra --only tornado,overinstallation")
    keys = ("reinforce_trafo_500_sek", "reinforce_trafo_800_sek", "reinforce_station_800_sek",
            "reinforce_urban_supplement_sek", "reinforce_price_level_year")
    _agree(read("tornado.json"), overinstallation_summary(read("overinstallation.json")), {k: A(k) for k in keys},
           {k: A(k) for k in summaries.RATES}, int(A("regulatory_period_years")))


# ---------------------------------------------------------------- the Swedish texts

def test_the_swedish_texts_have_no_digit_outside_their_placeholders():
    for path in (decision.PORTFOLIO_TEMPLATE, decision.OPTIONS_TEMPLATE):
        text = PLACEHOLDER.sub("", path.read_text(encoding="utf-8"))
        found = [(i + 1, line) for i, line in enumerate(text.splitlines()) if re.search(r"\d", line)]
        assert not found, f"figures typed into {path.name}: {found}"
    typed = {k: v for k, v in decision.SV.items() if re.search(r"\d", decision.SLOT.sub("", v))}
    assert not typed, f"figures typed into the Swedish sentences: {typed}"


def test_the_command_line_runs_the_portfolio(tmp_path, capsys):
    from kapacitet.pipeline import main

    study = make(tmp_path)
    main(["study", "portfolio", str(study.root), "--run", Q, "--allow-git"])
    assert "4 stations" in capsys.readouterr().out
    assert (study.runs / Q / "portfolio.csv").is_file() and (study.runs / Q / "portfolio_sv.md").is_file()
