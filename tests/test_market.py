"""The market comes first (kapacitet/market.py): a schedule waits for the day-ahead result, the
alternatives the DSO consulted are logged and sealed, and the redispatch report (engine/redispatch.py)
says what the issued basis curtailed. The gate, the log and the report's arithmetic need no data;
the API's issue and the report's sums against the operation index do."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import needs_operation

UTC = timezone.utc


def _record(**over) -> dict:
    body = {"scope": "activation_period", "ref": {"start": "2025-05-01", "end": "2025-08-31"},
            "alternatives": [{"kind": "local_flexibility_market", "name": "a local market", "consulted_at": "2025-04-10",
                              "outcome": "no bids in this area", "note": ""}],
            "decided_by": "grid planner"}
    return {**body, **over}


# ---------------------------------------------------------------- the gate

@pytest.mark.parametrize(("day", "utc"), [
    (date(2025, 1, 15), datetime(2025, 1, 14, 12, 0, tzinfo=UTC)),    # winter: CET, UTC+1
    (date(2025, 6, 15), datetime(2025, 6, 14, 11, 0, tzinfo=UTC)),    # summer: CEST, UTC+2
    # the day before is the spring change (30 March 2025, 02:00 -> 03:00): 13:00 is already summer time
    (date(2025, 3, 31), datetime(2025, 3, 30, 11, 0, tzinfo=UTC)),
    (date(2025, 3, 30), datetime(2025, 3, 29, 12, 0, tzinfo=UTC)),
    # the day before is the autumn change (26 October 2025, 03:00 -> 02:00): 13:00 is winter time again
    (date(2025, 10, 27), datetime(2025, 10, 26, 12, 0, tzinfo=UTC)),
    (date(2025, 10, 26), datetime(2025, 10, 25, 11, 0, tzinfo=UTC)),
])
def test_the_market_result_is_13_on_the_stockholm_clock_the_day_before(day, utc):
    from kapacitet.market import result_at

    assert result_at(day, "13:00") == utc


@pytest.mark.parametrize("day", [date(2025, 3, 31), date(2025, 10, 27), date(2025, 6, 15)])
def test_the_gate_opens_at_the_result_and_not_a_minute_before(day):
    from datetime import timedelta

    from kapacitet.market import gate, result_at

    at = result_at(day, "13:00")
    assert not gate(day, "13:00", at - timedelta(minutes=1))["open"]
    assert gate(day, "13:00", at)["open"] and gate(day, "13:00", at + timedelta(hours=5))["open"]
    assert gate(day, "13:00", at)["day_before"] == (day - timedelta(days=1)).isoformat()


def test_the_market_time_is_a_dso_setting_with_its_assumption_as_default(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from kapacitet import dso
    from kapacitet.api.main import app
    from kapacitet.config import A

    monkeypatch.setattr(dso, "path", lambda: tmp_path / "requests" / "settings.json")
    assert dso.load().market_result_local == A("dayahead_market_result_local") == "13:00"
    with TestClient(app) as c:
        assert c.put("/api/v1/settings", json={"market_result_local": "25:00"}).status_code == 422
        assert c.put("/api/v1/settings", json={"market_result_local": "12:45"}).json()["settings"]["market_result_local"] == "12:45"
    assert dso.load().market_result_local == "12:45"


# ---------------------------------------------------------------- the log

@pytest.mark.files_only
def test_a_record_is_appended_sealed_and_listed_newest_first():
    from fastapi.testclient import TestClient

    from kapacitet import market, seals
    from kapacitet.api.main import app

    with TestClient(app) as c:
        first = c.post("/api/v1/operation/market-alternatives", json=_record())
        assert first.status_code == 200, first.text
        second = c.post("/api/v1/operation/market-alternatives",
                        json=_record(scope="agreement", ref={"cid": 7},
                                     alternatives=[{"kind": "none_available", "consulted_at": "2025-03-01"}]))
        listed = c.get("/api/v1/operation/market-alternatives").json()
    lines = market.log_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["id"] == first.json()["entry"]["id"]
    assert [e["id"] for e in listed["entries"]] == [second.json()["entry"]["id"], first.json()["entry"]["id"]]
    assert listed["kinds"] == list(market.KINDS) and listed["market_result_local"]
    kept = seals.sealed_of_kind("market_alternatives")
    assert len(kept) == 2 and kept[0]["record"] == json.loads(lines[0])
    assert listed["entries"][1]["seal"]["digest"] == kept[0]["entry"]["digest"] == seals.digest(kept[0]["record"])
    assert seals.verify_chain()["ok"]


def test_the_same_idempotency_key_records_once():
    from fastapi.testclient import TestClient

    from kapacitet import market, seals
    from kapacitet.api.main import app

    key = {"Idempotency-Key": "market-retry-0001"}
    with TestClient(app) as c:
        a = c.post("/api/v1/operation/market-alternatives", json=_record(), headers=key).json()
        b = c.post("/api/v1/operation/market-alternatives", json=_record(decided_by="someone else"), headers=key).json()
    assert a == b and len(market.entries()) == 1 and len(seals.sealed_of_kind("market_alternatives")) == 1


@pytest.mark.files_only
def test_a_torn_tail_is_left_out_and_the_next_record_starts_its_own_line():
    from kapacitet import market

    market.record(market.RecordIn.model_validate(_record()))
    with market.log_path().open("a", encoding="utf-8") as fh:
        fh.write('{"id": "ma-torn", "scope": "zo')        # a power cut in the middle of an append
    assert len(market.entries()) == 1
    market.record(market.RecordIn.model_validate(_record(decided_by="after the cut")))
    got = market.entries()
    assert len(got) == 2 and got[-1]["decided_by"] == "after the cut"
    assert all("ma-torn" != e["id"] for e in got)


@pytest.mark.parametrize(("body", "code"), [
    (_record(scope="agreement"), "market_ref"),                                   # an agreement names a house
    (_record(ref={"cid": 3}), "market_ref"),                                      # a period names its days
    (_record(ref={"start": "2025-08-31", "end": "2025-05-01"}), "market_range"),
])
def test_a_record_that_does_not_fit_its_scope_is_refused_with_a_code(body, code):
    from fastapi.testclient import TestClient

    from kapacitet import market
    from kapacitet.api.main import app

    with TestClient(app) as c:
        r = c.post("/api/v1/operation/market-alternatives", json=body)
    assert r.status_code == 422 and r.json()["detail"]["code"] == code
    assert not market.entries()


def test_an_unknown_kind_of_alternative_is_refused():
    from fastapi.testclient import TestClient

    from kapacitet.api.main import app

    with TestClient(app) as c:
        bad = _record(alternatives=[{"kind": "a bribe", "consulted_at": "2025-04-10"}])
        assert c.post("/api/v1/operation/market-alternatives", json=bad).status_code == 422


def test_a_period_covers_its_days_and_an_agreement_covers_none():
    from kapacitet import market

    a = market.record(market.RecordIn.model_validate(_record()))[0]
    ag = market.record(market.RecordIn.model_validate(_record(scope="agreement", ref={"cid": 7})))[0]["id"]
    z = market.record(market.RecordIn.model_validate(_record(scope="zone", ref={"start": "2025-06-01", "end": "2025-06-30"})))[0]
    assert market.covering(date(2025, 5, 1)) == [a["id"]]
    assert market.covering(date(2025, 6, 15)) == [a["id"], z["id"]]
    assert market.covering(date(2025, 9, 1)) == []
    assert market.covering(date(2025, 9, 1), cid=7) == [ag] and market.covering(date(2025, 9, 1), cid=8) == []
    assert len(market.in_year(2025)) == 3 and len(market.in_year(2024)) == 1     # the agreement's holds every year


def test_the_ei_pack_carries_the_records_beside_the_statement():
    from kapacitet import market
    from kapacitet.dso import DsoSettings
    from kapacitet.engine.eipack import assess

    market.record(market.RecordIn.model_validate(_record()))
    index = {"year": 2025, "necessary_hours": 148,
             "combos": {"dayahead": {"maxmin": {"hours_limited": 1240, "activations_unnecessary_hours": 1092}}}}
    args = (index, [{"customer_id": 1, "static_approved": False}], None)
    bare = assess("dayahead", "maxmin", DsoSettings(), *args, None, market.entries())
    row = next(c for c in bare["conditions"] if c["key"] == "market_first")
    assert bare["market_alternatives"] == market.entries()
    # the records back the DSO's statement and never stand in for it
    assert row["state"] == "awaiting" and row["fact"] == {"code": "market_recorded_only", "params": {"recorded": 1}}
    stated = assess("dayahead", "maxmin", DsoSettings(market_alternative_statement="asked"), *args, None, market.entries())
    row = next(c for c in stated["conditions"] if c["key"] == "market_first")
    assert row["state"] == "declared" and row["fact"]["code"] == "market_statement_recorded"


# ---------------------------------------------------------------- the redispatch report

def _fake_operation(monkeypatch, verified=True):
    from kapacitet.engine import redispatch

    index = {"year": 2025, "necessary_hours": 148, "combos": {"dayahead": {"maxmin": {
        "verified": verified, "curtailed_kwh": 300.0, "curtailed_share_of_export_pct": 25.0, "hours_limited": 12,
        "activations_unnecessary_hours": 4}}, "foresight": {"maxmin": {
        "verified": True, "curtailed_kwh": 99.0, "curtailed_share_of_export_pct": 9.0, "hours_limited": 8}}}}
    comp = {"activations_necessary_hours": 8, "conditional_agreements": 3, "non_firm_export_kwh": 1200.0,
            "binding_constraint_hours": {"transformer": 10, "line 4": 2},
            "binding_constraint_necessary_hours": {"transformer": 8}, "verified_violations": 0,
            "per_customer": [{"customer_id": 1, "curtailed_kwh": 200.0}, {"customer_id": 2, "curtailed_kwh": 100.0},
                             {"customer_id": 3, "curtailed_kwh": 0.0}]}
    files = {("results", "operation", "index.json"): index,
             ("results", "operation", "dayahead", "maxmin", "compliance.json"): comp}

    def read(*parts):
        if parts not in files:
            raise redispatch.RedispatchError("missing")
        return files[parts]

    monkeypatch.setattr(redispatch, "_read", read)
    return index, comp


def test_the_report_reads_the_issued_basis_and_never_the_planning_figure(monkeypatch, tmp_path):
    from kapacitet import dso, market
    from kapacitet.engine import redispatch

    monkeypatch.setattr(dso, "path", lambda: tmp_path / "requests" / "settings.json")
    _fake_operation(monkeypatch)
    market.record(market.RecordIn.model_validate(_record()))
    r = redispatch.build()
    assert r["basis"] == "dayahead" and r["curtailed_kwh"] == 300.0 and r["curtailed_mwh"] == 0.3
    assert (r["hours"], r["hours_needed"], r["hours_not_needed"]) == (12, 8, 4)
    assert (r["installations"], r["agreements"]) == (2, 3)
    assert r["share_of_potential_export_pct"] == 25.0 and r["potential_export_kwh"] == 1200.0
    assert r["compensation_sek"] == 0.0 and not r["compensation_entered"]
    assert r["binding_components_hours"] == {"transformer": 10, "line 4": 2}
    assert r["market_alternatives"]["recorded"] == 1 and r["market_alternatives"]["kinds"] == ["local_flexibility_market"]
    assert r["legal_basis"].endswith("art. 13.4")
    text = redispatch.text_sv(r)
    assert "Icke-marknadsbaserad omdirigering, rapportår 2025" in text and "0,3 MWh" in text and "kabel 4" in text
    from kapacitet.language import NOT_REVIEWED_SV

    assert text.rstrip().endswith(NOT_REVIEWED_SV)
    assert "SEK" in text and " kr" not in text

    dso.update(dso.DsoSettingsChange(redispatch_compensation_sek=1500))
    paid = redispatch.build()
    assert paid["compensation_sek"] == 1500.0 and paid["compensation_entered"]
    assert "1 500 SEK" in redispatch.text_sv(paid)


def test_the_report_refuses_another_year_and_an_unverified_combination(monkeypatch):
    from kapacitet.engine import redispatch

    _fake_operation(monkeypatch)
    with pytest.raises(redispatch.RedispatchError, match="2025, not 2024"):
        redispatch.build(2024)
    _fake_operation(monkeypatch, verified=False)
    with pytest.raises(redispatch.RedispatchError, match="did not verify"):
        redispatch.build()


def test_the_report_command_and_endpoint_say_what_writes_them():
    from kapacitet.api.main import command_for

    assert command_for("redispatch.json") == "python -m kapacitet redispatch-report"
    assert command_for("redispatch_sv.txt") == "python -m kapacitet redispatch-report"


# ---------------------------------------------------------------- on the computed year

@needs_operation
def test_the_report_sums_equal_the_operation_index(monkeypatch, tmp_path):
    from kapacitet import dso
    from kapacitet.api.combos import operation_index
    from kapacitet.engine import redispatch
    from kapacitet.paths import processed_ro

    monkeypatch.setattr(dso, "path", lambda: tmp_path / "requests" / "settings.json")
    idx = operation_index()
    r = redispatch.build()
    combo = idx["combos"]["dayahead"][r["policy"]]
    assert r["year"] == idx["year"] and r["basis"] == "dayahead"
    assert r["curtailed_kwh"] == combo["curtailed_kwh"] and r["hours"] == combo["hours_limited"]
    assert r["share_of_potential_export_pct"] == combo["curtailed_share_of_export_pct"]
    assert r["hours_not_needed"] == combo["activations_unnecessary_hours"]
    assert r["hours_needed"] == idx["necessary_hours"]
    comp = json.loads(processed_ro("results", "operation", "dayahead", r["policy"], "compliance.json").read_text(encoding="utf-8"))
    per = comp["per_customer"]
    # the per-installation figures add up to the total, to their rounding
    assert sum(c["curtailed_kwh"] for c in per) == pytest.approx(r["curtailed_kwh"], abs=0.05 * len(per))
    assert r["installations"] <= r["agreements"] == len(idx["nonfirm_cids"])
    assert sum(r["binding_components_needed_hours"].values()) >= r["hours_needed"] > 0
    assert r["curtailed_kwh"] != idx["combos"]["foresight"][r["policy"]]["curtailed_kwh"]


@needs_operation
def test_an_issue_waits_for_the_market_unless_a_reason_is_sealed_with_it(monkeypatch):
    from fastapi.testclient import TestClient

    from kapacitet import market, seals
    from kapacitet.api.main import app

    with TestClient(app) as c:
        idx = c.get("/api/v1/operation/index").json()
        cid, year = idx["nonfirm_cids"][0], idx["year"]
        day = f"{year}-06-15"
        opens = market.result_at(date.fromisoformat(day), "13:00")
        monkeypatch.setattr(market, "now", lambda: opens - timedelta(hours=1))
        early = c.post("/api/v1/operation/schedule/issue", json={"cid": cid, "date": day})
        assert early.status_code == 409 and early.json()["detail"]["code"] == "before_market"
        assert early.json()["detail"]["params"]["day_before"] == f"{year}-06-14"
        assert not seals.sealed_of_kind("schedule")

        forced = c.post("/api/v1/operation/schedule/issue", json={"cid": cid, "date": day,
                                                                  "override_reason": "storm warning, issued early"})
        assert forced.status_code == 200
        assert forced.headers["x-issue-flags"] == "before_market_override,no_market_alternatives"
        rec = seals.sealed_of_kind("schedule")[-1]["record"]["market"]
        assert rec["before_market_result"] and rec["override_reason"] == "storm warning, issued early"
        assert rec["market_result_at"] == opens.isoformat()

        # after the result, and with the season's alternatives on record, the issue carries no flag
        monkeypatch.setattr(market, "now", lambda: opens)
        c.post("/api/v1/operation/market-alternatives", json=_record())
        on_time = c.post("/api/v1/operation/schedule/issue", json={"cid": cid, "date": day,
                                                                   "override_reason": "not needed"})
        assert on_time.status_code == 200 and on_time.headers["x-issue-flags"] == ""
        rec = seals.sealed_of_kind("schedule")[-1]["record"]["market"]
        assert not rec["before_market_result"] and rec["override_reason"] is None and len(rec["market_alternatives"]) == 1

        # a key sent again returns the first issue, even once the terms have changed
        key = {"Idempotency-Key": "market-gate-0001"}
        first = c.post("/api/v1/operation/schedule/issue", json={"cid": cid, "date": day}, headers=key)
        c.post("/api/v1/operation/market-alternatives",
               json=_record(scope="zone", ref={"start": day, "end": day}))
        again = c.post("/api/v1/operation/schedule/issue", json={"cid": cid, "date": day}, headers=key)
        assert first.headers["x-seal-seq"] == again.headers["x-seal-seq"] and first.content == again.content


def test_the_report_endpoint_says_how_to_write_it_or_serves_what_was_written():
    from fastapi.testclient import TestClient

    from kapacitet.api.main import app, result_path

    with TestClient(app) as c:
        r = c.get("/api/v1/redispatch")
        txt = c.get("/api/v1/redispatch_sv.txt")
    if not result_path("redispatch.json").exists():
        assert r.status_code == 404 and r.json()["detail"]["command"] == "python -m kapacitet redispatch-report"
        assert txt.status_code == 404
    else:
        assert r.status_code == 200 and r.json()["data"]["basis"] == "dayahead" and "provenance" in r.json()
        assert txt.status_code == 200 and "omdirigering" in txt.text and "attachment" in txt.headers["content-disposition"]


@needs_operation
def test_a_written_report_still_equals_the_operation_index():
    from kapacitet.api.combos import operation_index
    from kapacitet.paths import processed_ro

    path = processed_ro("results", "redispatch.json")
    if not path.exists():
        pytest.skip("run: python -m kapacitet redispatch-report")
    r = json.loads(path.read_text(encoding="utf-8"))
    combo = operation_index()["combos"]["dayahead"][r["policy"]]
    assert r["basis"] == "dayahead"
    assert (r["curtailed_kwh"], r["hours"]) == (combo["curtailed_kwh"], combo["hours_limited"])
    assert r["share_of_potential_export_pct"] == combo["curtailed_share_of_export_pct"]
