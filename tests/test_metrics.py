"""The investor metrics (kapacitet/metrics.py): computed from stand-in files written here, a missing
source given as `not_available` with its command, the owner's metrics never filled in, and texts
whose only figures are the metrics' own. CI runs all of it but the last test, which reads the real
results."""

from __future__ import annotations

import json
import math
import re

import pytest

from conftest import needs_results
from kapacitet import metrics
from kapacitet.dossier import PLACEHOLDER

OWNER = ("discovery_conversations", "sales_pipeline", "pilots_signed", "onboarding_days", "security_incidents")


def by_id(doc: dict) -> dict[str, dict]:
    return {m["id"]: m for m in doc["metrics"]}


def assert_no_typed_figure(doc: dict) -> None:
    """In both texts, every digit is in a metric's value or period, the subject or the time stamp."""
    for lang in ("en", "sv"):
        text = metrics.text(doc, lang)
        assert "{{" not in text and "}}" not in text
        rest = text.replace(metrics.stamp_text(doc["computed_at"]), "").replace(metrics.subject_text(doc, lang), "")
        for m in doc["metrics"]:
            for cell in (metrics.value_text(m, lang), metrics.period_text(m, lang)):
                rest = rest.replace(f"| {cell} |", "|  |", 1)
        stray = [line for line in rest.splitlines() if re.search(r"\d", line)]
        assert not stray, f"figures typed into the {lang} text: {stray}"
    json.dumps(doc)


def test_the_templates_and_the_words_hold_no_figure():
    for lang, path in metrics.TEMPLATES.items():
        text = PLACEHOLDER.sub("", path.read_text(encoding="utf-8"))
        assert not re.search(r"\d", text), f"a figure typed into {path.name}"
    for mid, (_, _, _, en, sv) in metrics.DEFINITIONS.items():
        assert not re.search(r"\d", en + sv), mid

    def words(x):
        return [w for v in x.values() for w in (words(v) if isinstance(v, dict) else [v])]

    for lang in ("en", "sv"):
        assert not [w for w in words(metrics.WORDS[lang]) if re.search(r"\d", w)]


def test_on_an_empty_data_folder_every_metric_says_what_writes_its_source(tmp_path):
    doc = metrics.build(data_dir=tmp_path, now="2031-01-02T03:04:05+00:00")
    m = by_id(doc)
    assert doc["schema"] == metrics.SCHEMA and doc["kind"] == "demo"
    assert set(m) == set(metrics.DEFINITIONS)
    computed = [x for x in doc["metrics"] if x["status"] == "ok"]
    assert computed == []
    for x in doc["metrics"]:
        assert x["value"] is None
        if x["group"] == "owner":
            assert x["status"] == "entered_by_owner" and "command" not in x
        else:
            assert x["status"] == "not_available" and x["command"].startswith("python -m kapacitet") and x["source"]
    assert m["envelope_hours_activated"]["command"] == metrics.REDISPATCH
    assert m["dayahead_bound_exceeded_hours"]["command"].endswith("--only foresight")
    assert m["intraday_bound_exceeded_hours"]["command"].endswith("--only intraday")
    assert m["capacity_over_fixed_cap"]["command"] == metrics.COMPUTE
    assert m["voltage_error_pu"]["command"] == metrics.VALIDATE
    assert sorted(doc["entered_by_owner"]) == sorted(OWNER)
    assert {x["id"] for x in doc["not_available"]} == set(metrics.DEFINITIONS) - set(OWNER)
    sv = metrics.text(doc, "sv")
    assert "saknas: kör `python -m kapacitet redispatch-report`" in sv and "anges av ägaren" in sv
    assert "not available: run `python -m kapacitet redispatch-report`" in metrics.text(doc, "en")
    assert_no_typed_figure(doc)


# ---------------------------------------------------------------- the installation, from stand-in files

def _lines(path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _put(folder, name, doc) -> None:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _stats(n, rmse, bias=0.0, mae=None, mx=None):
    return {"n": n, "bias": bias, "mae": rmse * 0.8 if mae is None else mae, "rmse": rmse, "p95": rmse * 2,
            "max": rmse * 3 if mx is None else mx}


def _events():
    def at(day, hour=10):
        return f"2030-10-{day:02d}T{hour:02d}:00:00+00:00"

    def received(app, day):
        return {"app": app, "event": "received", "at": at(day), "cid": 7, "assessment": f"{app:0>12}"}

    return [
        received("app-1", 1), {"app": "app-1", "event": "review", "at": at(1), "proposal": "firm"},
        {"app": "app-1", "event": "decided", "at": at(3), "outcome": "firm", "by": "dso"},              # agrees, 2 days
        received("app-2", 2), {"app": "app-2", "event": "review", "at": at(2), "proposal": "nonfirm"},
        {"app": "app-2", "event": "decided", "at": at(6), "outcome": "firm", "by": "dso"},              # differs, 4 days
        received("app-3", 3), {"app": "app-3", "event": "decided", "at": at(3, 11), "outcome": "nonfirm", "by": "rule"},
        received("app-4", 4), {"app": "app-4", "event": "review", "at": at(4), "proposal": "review"},
        {"app": "app-4", "event": "decided", "at": at(5), "outcome": "declined", "by": "dso"},          # 1 day
        received("app-5", 5),                                                                        # waiting
    ]


def _installation(folder):
    """A data folder with every source the metrics read, holding numbers no real run gives."""
    req, res = folder / "requests", folder / "processed" / "results"
    _lines(req / "index.jsonl", [{"id": f"{i:012x}", "created": f"2030-09-{d:02d}T08:00:00+00:00"}
                                 for i, d in ((1, 3), (2, 9), (3, 21))] + [{"id": "not-an-id"}])
    _lines(req / "applications.jsonl", _events())
    _put(res, "meta.json", {"site": "Provområdet"})
    _put(res, "redispatch.json", {"year": 2030, "hours": 1301, "hours_needed": 151, "hours_not_needed": 1150,
                                  "basis": "dayahead", "policy": "maxmin"})
    _put(res, "foresight.json", {"years": [2027, 2028, 2029, 2030], "margins": {"pv_pct": 3.0, "load_pct": 3.0},
                                 "plan_exceedance": {"total": {"pv_aggregate_hours_over": 61, "audits": 68,
                                                               "pv_customer_hours_over": 999}}})
    _put(res, "intraday.json", {"years": [2027, 2028, 2029, 2030], "headline_variant": "forecast",
                                "plan_exceedance": {"pv_aggregate_hours_over": 377, "audits": 68}})
    _put(res, "hosting_summary.json", {
        "years": [2027, 2028, 2029, 2030], "n_orders": 3, "z_star_pct": 3.0, "budget_basis": {"doe": "aggregate"},
        "measures": {"timeseries": {"kw_ac": {"median": 600.0}}, "fixed_cap": {"kw_ac": {"median": 400.0}},
                     "doe": {"kw_ac": {"median": 780.0}}},
        "per_order": [{"customers_within_pct": v} for v in (70.0, 64.5, 81.25)]})
    _put(res, "validation_measured.json", {
        "measured": {"hours_complete": 744, "bases": {"measured": {"hours": 744, "voltage_pu": _stats(5000, 0.0042),
                                                                   "transformer_pct": _stats(744, 1.7)}}},
        "dppower": {"2030-01-15T11:00:00+00:00": {"measured": {"rows": 40, "matched": 38,
                                                              "voltage_pu": _stats(30, 0.003),
                                                              "loading_pct": _stats(8, 2.0)},
                                                  "metered_injection": True}}})
    return folder


def test_the_installation_metrics_come_from_its_files(tmp_path):
    doc = metrics.build(data_dir=_installation(tmp_path), now="2031-01-02T03:04:05+00:00")
    m = by_id(doc)
    assert m["assessments_prechecked"]["value"] == 3
    assert m["assessments_prechecked"]["period"] == {"from": "2030-09-03", "to": "2030-09-21"}
    a = m["proposal_agreement_pct"]
    assert a["value"] == pytest.approx(50.0) and a["detail"]["compared"] == 2 and a["detail"]["review_not_compared"] == 1
    assert m["median_days_to_decision"]["value"] == pytest.approx(2.0)      # a person's 1, 2 and 4 days
    assert m["median_days_to_decision"]["detail"]["decided_by_rule"] == 1
    assert m["median_days_to_decision"]["detail"]["all_decisions_median_days"] == pytest.approx(1.5)
    assert m["median_days_to_decision"]["period"] == {"from": "2030-10-01", "to": "2030-10-06"}
    assert m["voltage_error_pu"]["value"] == pytest.approx(0.0042) and m["voltage_error_pu"]["period"] == {"hours": 744}
    assert m["transformer_error_pp"]["value"] == pytest.approx(1.7)
    assert m["dppower_matched_pct"]["value"] == pytest.approx(95.0)
    assert m["dppower_voltage_pu"]["value"] == pytest.approx(0.003)
    assert m["envelope_hours_activated"]["value"] == 1301 and m["envelope_hours_needed"]["value"] == 151
    assert m["envelope_hours_activated"]["period"] == {"year": 2030}
    assert m["dayahead_bound_exceeded_hours"]["value"] == 61 and m["dayahead_bound_exceeded_hours"]["detail"]["audits"] == 68
    assert m["intraday_bound_exceeded_hours"]["value"] == 377
    assert m["agreements_within_budget_pct"]["value"] == pytest.approx(70.0)
    assert m["agreements_within_budget_pct"]["detail"]["worst"] == pytest.approx(64.5)
    assert m["capacity_over_hourly"]["value"] == pytest.approx(1.3)
    assert m["capacity_over_fixed_cap"]["value"] == pytest.approx(1.95)
    assert m["capacity_over_hourly"]["period"] == {"years": [2027, 2028, 2029, 2030]}
    for x in doc["metrics"]:
        assert x["source"] or x["group"] == "owner"
        assert (x["status"] == "ok") == (x["value"] is not None)
    assert [x for x in doc["metrics"] if x["status"] == "not_available"] == []
    assert sorted(doc["entered_by_owner"]) == sorted(OWNER)
    sv, en = metrics.text(doc, "sv"), metrics.text(doc, "en")
    for shown in ("×1,3 (780 kW mot 600 kW)", "1301 h", "2027–2030", "70 % (median över 3 ordningar, sämsta 64,5 %)",
                  "50 % (1 av 2 jämförda; 1 förslag om prövning jämförs inte)", "0,0042 p.u.", "Provområdet",
                  "2031-01-02 03:04 UTC"):
        assert shown in sv, shown
    assert "×1.95 (780 kW against 400 kW)" in en and "2 days" in en and "1 by the rule, not counted" in en
    assert_no_typed_figure(doc)


def test_a_source_that_holds_nothing_yet_is_not_given_a_value(tmp_path):
    folder = _installation(tmp_path)
    _lines(folder / "requests" / "applications.jsonl", _events()[:2])        # received and in review only
    (folder / "processed" / "results" / "intraday.json").write_text("{}", encoding="utf-8")
    (folder / "processed" / "results" / "validation_measured.json").write_text(
        json.dumps({"measured": {"status": "no meter readings"}}), encoding="utf-8")
    m = by_id(metrics.build(data_dir=folder))
    for mid, reason in (("proposal_agreement_pct", "no_decisions"), ("median_days_to_decision", "no_decisions"),
                        ("intraday_bound_exceeded_hours", "missing"), ("voltage_error_pu", "no_measurements"),
                        ("dppower_matched_pct", "no_snapshot")):
        assert m[mid]["status"] == "not_available" and m[mid]["value"] is None, mid
        assert m[mid]["reason"] == reason and m[mid]["command"], mid
    assert m["intraday_bound_exceeded_hours"]["command"] == metrics.EXTRA + "intraday"


def test_error_statistics_pool_by_their_samples():
    a, b = _stats(100, 0.004, bias=0.001, mae=0.003, mx=0.02), _stats(300, 0.002, bias=-0.001, mae=0.001, mx=0.01)
    p = metrics.pool([a, b, {"n": 0, "rmse": None}])
    assert p["n"] == 400
    assert p["rmse"] == pytest.approx(math.sqrt((100 * 0.004 ** 2 + 300 * 0.002 ** 2) / 400))
    assert p["bias"] == pytest.approx((0.1 - 0.3) / 400) and p["mae"] == pytest.approx((0.3 + 0.3) / 400)
    assert p["max"] == 0.02 and "p95" not in p
    assert metrics.pool([]) is None


def test_numbers_are_written_as_each_language_does():
    assert metrics.number(54745.2, 1, "sv") == "54 745,2"
    assert metrics.number(54745.2, 1, "en") == "54,745.2"
    assert metrics.number(1.2949, 2, "en") == "1.29" and metrics.number(-0.25, 2, "en") == "-0.25"
    assert metrics.number(None, 0, "en") == "–"


# ---------------------------------------------------------------- a study

@pytest.fixture
def study(tmp_path):
    from test_study_publish import STATIONS, put

    from kapacitet.study import csvio, init_study

    s = init_study(tmp_path / "nb", "Testnät AB", allow_git=True)
    doe = {"S1": {"status": "ok", "kw": 180.0}, "S4": {"status": "ok", "kw": 260.0}}
    for sid, v in STATIONS.items():
        put(s, sid, v["customers"], v["lon"], v["lat"], v["gen"], v["cons"], doe=doe.get(sid))
    _lines(s.station_dir("S1") / "requests" / "index.jsonl",
           [{"id": "0000000000a1", "created": "2030-03-01T00:00:00+00:00"},
            {"id": "0000000000a2", "created": "2030-03-02T00:00:00+00:00"}])
    _lines(s.station_dir("S4") / "requests" / "index.jsonl", [{"id": "0000000000b1", "created": "2030-03-05T00:00:00+00:00"}])
    rows = [{"case_id": c, "received_at": f"2030-02-{d:02d}", "proposal": p, "decision_dso": dd, "error": e}
            for c, d, p, dd, e in (("c1", 1, "firm", "firm", ""), ("c2", 2, "nonfirm", "firm", ""),
                                   ("c3", 3, "nonfirm", "NONFIRM", ""), ("c4", 4, "review", "firm", ""),
                                   ("c5", 5, "", "firm", "unknown_point"), ("c6", 6, "firm", "", ""))]
    csvio.write(s.root / "cases" / "b1", "verdicts", rows, list(rows[0]), "sv-excel")
    val = {"S1": (_stats(100, 0.004), _stats(100, 0.006)), "S2": (_stats(300, 0.002), _stats(300, 0.003))}
    for sid, (low, high) in val.items():
        _put(s.station_dir(sid) / "processed" / "results", "validation_measured.json",
             {"measured": {"hours_complete": 100 if sid == "S1" else 300, "mv": "band",
                           "bases": {"band_low": {"voltage_pu": low, "transformer_pct": {"n": 0}},
                                     "band_high": {"voltage_pu": high, "transformer_pct": {"n": 0}}}}})
    return s


def test_a_study_reads_its_stations_and_its_case_batches(study):
    doc = metrics.build(study.root, now="2031-01-02T03:04:05+00:00")
    m = by_id(doc)
    assert doc["kind"] == "study" and doc["subject"]["dso"] == "Testnät AB"
    assert m["assessments_prechecked"]["value"] == 3
    assert m["assessments_prechecked"]["period"] == {"from": "2030-03-01", "to": "2030-03-05"}
    a = m["proposal_agreement_pct"]
    assert a["value"] == pytest.approx(200.0 / 3) and a["detail"]["review_not_compared"] == 1
    assert a["period"] == {"from": "2030-02-01", "to": "2030-02-03"}
    v = m["voltage_error_pu"]
    assert v["detail"]["mv"] == "band_high"           # without a measured MV, the worse end of the band
    assert v["value"] == pytest.approx(math.sqrt((100 * 0.006 ** 2 + 300 * 0.003 ** 2) / 400))
    assert v["detail"]["stations"] == 2 and v["period"] == {"hours": 400}
    assert m["transformer_error_pp"]["reason"] == "no_measurements"
    c = m["capacity_over_hourly"]
    assert c["value"] == pytest.approx(440.0 / 320.0) and c["detail"]["stations"] == 2
    for mid in ("median_days_to_decision", "envelope_hours_activated", "dayahead_bound_exceeded_hours",
                "agreements_within_budget_pct", "capacity_over_fixed_cap"):
        assert m[mid]["reason"] == "not_in_study" and m[mid]["value"] is None, mid
    assert "ingår inte i en studie" in metrics.text(doc, "sv")
    assert_no_typed_figure(doc)


def test_the_command_writes_the_three_files(study):
    from kapacitet.pipeline import main

    main(["metrics", "--study", str(study.root)])
    doc = json.loads((study.root / metrics.JSON_NAME).read_text(encoding="utf-8"))
    assert doc["schema"] == metrics.SCHEMA and doc["kind"] == "study"
    for lang, name in metrics.TEXT_NAMES.items():
        assert (study.root / name).read_text(encoding="utf-8") == metrics.text(doc, lang)


# ---------------------------------------------------------------- the API

def test_kpis_serves_the_document_of_the_data_folder(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from kapacitet import paths
    from kapacitet.api.main import app

    monkeypatch.setattr(paths, "DATA", _installation(tmp_path))
    with TestClient(app) as c:
        r = c.get("/api/v1/kpis")
    assert r.status_code == 200
    body = r.json()
    assert "provenance" in body
    m = by_id(body["data"])
    assert body["data"]["schema"] == metrics.SCHEMA and m["envelope_hours_activated"]["value"] == 1301


def test_kpis_needs_a_session_and_takes_any_role_when_sign_in_is_on(oidc):
    c = _client()
    r = c.get("/api/v1/kpis")
    assert r.status_code == 401 and _code(r) == "auth_required"
    _sign_in(c, oidc, roles=["auditor"])
    assert c.get("/api/v1/kpis").status_code == 200


from test_auth import _client, _code, _sign_in, oidc  # noqa: E402,F401  (the fake provider's fixture)


# ---------------------------------------------------------------- the real results

@needs_results
def test_on_the_published_results_the_envelope_metrics_are_the_summarys(results):
    doc = metrics.build()
    m = by_id(doc)
    hs = results("hosting_summary")
    med = {k: hs["measures"][k]["kw_ac"]["median"] for k in ("doe", "timeseries", "fixed_cap")}
    assert m["capacity_over_hourly"]["value"] == pytest.approx(med["doe"] / med["timeseries"])
    assert m["capacity_over_fixed_cap"]["value"] == pytest.approx(med["doe"] / med["fixed_cap"])
    assert_no_typed_figure(doc)


def test_a_report_on_another_basis_or_without_a_reference_gets_its_own_reason(tmp_path):
    folder = _installation(tmp_path)
    res = folder / "processed" / "results"
    _put(res, "redispatch.json", {"year": 2030, "hours": 5, "hours_needed": 1, "basis": "perfect", "policy": "maxmin"})
    _put(res, "hosting_summary.json", {
        "years": [2030], "n_orders": 1, "measures": {"timeseries": {"kw_ac": {"median": 600.0}},
                                                     "fixed_cap": {"kw_ac": {"median": 0}},
                                                     "doe": {"kw_ac": {"median": 780.0}}},
        "per_order": [{"customers_within_pct": 70.0}]})
    doc = metrics.build(data_dir=folder)
    m = by_id(doc)
    for mid in ("envelope_hours_activated", "envelope_hours_needed"):
        assert m[mid]["status"] == "not_available" and m[mid]["reason"] == "wrong_basis"
    assert m["capacity_over_fixed_cap"]["reason"] == "no_reference" and m["capacity_over_hourly"]["status"] == "ok"
    assert "reference capacity is zero" in metrics.text(doc, "en")
    assert "run `python -m kapacitet compute`" not in metrics.text(doc, "en").split("fixed")[-1][:200]
