"""Delivering the day-ahead envelopes (engine/delivery.py, engine/csipaus.py): schedules on the
study clock, the inverter's XML, the shadow season's criteria and the meter check. The pure parts
run anywhere; the last tests read the operation stage's output."""

from __future__ import annotations

import json
from datetime import date
from xml.etree import ElementTree as ET

import numpy as np
import pytest

from conftest import needs_operation

NS = {"s": "urn:ieee:std:2030.5:ns", "c": "https://csipaus.org/ns/v1.2"}


def test_schedule_intervals_start_on_the_study_clock():
    from kapacitet.engine.delivery import day_of, schedule

    year = 2025
    limits = np.arange(8760, dtype=float) / 1000.0
    day = day_of(year, date(2025, 6, 15))
    rows = schedule(limits, None, year, day)
    assert len(rows) == 24
    # the study day starts at 00:00 UTC+1, which is 23:00 UTC the day before, and 01:00 in Stockholm's summer
    assert rows[0]["start_utc"] == "2025-06-14T23:00:00+00:00"
    assert rows[0]["study_clock"] == "2025-06-15 00:00"
    assert rows[0]["stockholm"].startswith("2025-06-15 01:00")
    assert all(b["start_epoch"] - a["start_epoch"] == 3600 for a, b in zip(rows, rows[1:]))
    assert rows[5]["limit_kw"] == pytest.approx(limits[day * 24 + 5])
    with pytest.raises(ValueError):
        day_of(year, date(2026, 1, 1))


def test_a_quarter_hour_schedule_of_hourly_inputs_is_the_hourly_one_repeated():
    from kapacitet.engine.delivery import day_of, schedule, terms, within_envelope

    year = 2025
    rng = np.random.default_rng(3)
    limits = rng.uniform(0.0, 10.0, 8760)
    desired = rng.uniform(0.0, 12.0, 8760)
    # a winter day: Stockholm's clock is the study's, so the delivery day is the study day
    day = day_of(year, date(2025, 2, 10))
    hourly = schedule(limits, desired, year, day, 60)
    quarter = schedule(limits, desired, year, day, 15)
    assert len(quarter) == 96 and {r["duration_s"] for r in quarter} == {900}
    for key in ("limit_kw", "desired_kw", "hour"):
        assert [r[key] for r in quarter] == [r[key] for r in hourly for _ in range(4)]
    assert [r["start_epoch"] for r in quarter] == [r["start_epoch"] + 900 * k for r in hourly for k in range(4)]
    assert quarter[1]["study_clock"] == "2025-02-10 00:15" and quarter[1]["stockholm"].startswith("2025-02-10 00:15")
    # a summer day starts at midnight in Stockholm, 23:00 the day before on the study's clock, and
    # every quarter still carries the envelope of the hour it falls in
    day = day_of(year, date(2025, 6, 15))
    summer = schedule(limits, desired, year, day, 15)
    assert summer[0]["start_utc"] == "2025-06-14T22:00:00+00:00" and summer[0]["study_clock"] == "2025-06-14 23:00"
    assert summer[0]["hour"] == day * 24 - 1
    assert all(r["limit_kw"] == round(limits[r["hour"]], 3) and r["hour"] == r["quarter"] // 4 for r in summer)
    assert within_envelope(summer, limits) == {"hours": 24, "intervals": 96, "hours_above": 0, "ok": True,
                                               "first_above": None}
    t = terms(15, desired, len(limits))
    assert t["resolution_min"] == 15 and t["interval_s"] == 900 and t["inputs_resolution_min"] == 60
    assert t["verified_resolution_min"] == 60 and t["inputs"]["desired_kw"] == 60


def test_a_quarter_above_its_hours_envelope_is_caught():
    from kapacitet.engine.delivery import schedule, within_envelope

    limits = np.full(8760, 3.0)
    rows = schedule(limits, None, 2025, 40, 15)
    rows[13]["limit_kw"] = 3.2                       # one quarter of the fourth hour above its envelope
    out = within_envelope(rows, limits)
    assert not out["ok"] and out["hours_above"] == 1 and out["first_above"] == 40 * 24 + 3


def test_a_load_side_metered_at_15_minutes_enters_quarter_by_quarter():
    from kapacitet.engine.delivery import schedule, terms

    limits = np.full(8760, 4.0)
    desired = np.tile([1.0, 2.0, 3.0, 6.0], 8760)    # one value per quarter-hour of the year
    rows = schedule(limits, desired, 2025, 40, 15)
    assert [r["desired_kw"] for r in rows[:4]] == [1.0, 2.0, 3.0, 6.0]
    assert all(r["limit_kw"] == 4.0 for r in rows)
    assert schedule(limits, desired, 2025, 40, 60)[0]["desired_kw"] == 3.0     # the hour is the mean
    assert terms(15, desired, 8760)["inputs"]["desired_kw"] == 15


@pytest.mark.parametrize(("day", "n"), [(date(2025, 3, 30), 92), (date(2025, 10, 26), 100), (date(2025, 7, 1), 96)])
def test_the_inverter_xml_has_a_control_per_quarter_hour_of_the_delivery_day(day, n):
    from kapacitet import market
    from kapacitet.engine.csipaus import der_control_list
    from kapacitet.engine.delivery import day_of, schedule

    rows = schedule(np.arange(8760, dtype=float) / 100.0, None, 2025, day_of(2025, day), 15)
    root = ET.fromstring(der_control_list(79, rows, 1749900000))
    controls = root.findall("s:DERControl", NS)
    assert len(controls) == n and root.get("all") == str(n)
    assert {c.find("s:interval/s:duration", NS).text for c in controls} == {"900"}
    starts = [int(c.find("s:interval/s:start", NS).text) for c in controls]
    assert all(b - a == 900 for a, b in zip(starts, starts[1:]))
    assert len({c.find("s:mRID", NS).text for c in controls}) == n
    # the market gate's day is the same span
    span = market.delivery_day(day, 15)
    assert span["intervals"] == n and span["start_utc"] == rows[0]["start_utc"]
    assert market.delivery_day(day, 60)["intervals"] == n // 4


def test_the_resolution_comes_from_the_assumption_unless_asked():
    from kapacitet.engine.delivery import resolution, schedule

    assert resolution() == 15 and resolution(60) == 60
    with pytest.raises(ValueError):
        resolution(30)
    with pytest.raises(ValueError):
        schedule(np.ones(8760), None, 2025, 10, 30)


def test_der_control_list_is_well_formed_and_namespaced():
    from kapacitet.engine.csipaus import der_control_list

    rows = [{"start_epoch": 1749942000 + 3600 * h, "limit_kw": 1.2345 * h} for h in range(24)]
    root = ET.fromstring(der_control_list(79, rows, 1749900000))
    assert root.tag == "{urn:ieee:std:2030.5:ns}DERControlList" and root.get("all") == "24"
    controls = root.findall("s:DERControl", NS)
    assert len(controls) == 24
    first = controls[3]
    assert first.find("s:interval/s:start", NS).text == str(rows[3]["start_epoch"])
    assert first.find("s:interval/s:duration", NS).text == "3600"
    lim = first.find("s:DERControlBase/c:opModExpLimW", NS)
    assert lim.find("s:value", NS).text == str(int(1000 * 1.2345 * 3)) and lim.find("s:multiplier", NS).text == "0"
    assert len({c.find("s:mRID", NS).text for c in controls}) == 24       # every event its own id


def test_active_power_never_reads_above_the_limit():
    from kapacitet.engine.csipaus import active_power

    assert active_power(1965.4) == (1965, 0)
    value, mult = active_power(44_000.0)                 # beyond Int16: a coarser multiplier
    assert mult == 1 and value * 10 ** mult <= 44_000.0 and value <= 32767
    assert active_power(-5) == (0, 0)


def test_default_control_carries_the_fallback_limit():
    from kapacitet.engine.csipaus import default_der_control

    root = ET.fromstring(default_der_control(79, 0.055))
    assert root.tag == "{urn:ieee:std:2030.5:ns}DefaultDERControl"
    assert root.find("s:DERControlBase/c:opModExpLimW/s:value", NS).text == "55"


def test_meter_check_flags_only_readings_above_the_tolerance():
    from kapacitet.engine.delivery import meter_check

    limits = np.full(8760, 2.0)                                           # 2 kW all year
    csv = "timestamp,export_kw\n" + "\n".join([
        "2025-06-15T12:00:00+01:00,2.10",       # 100 W over: within the tolerance
        "2025-06-15T12:05:00+01:00,2.30",       # 300 W over: a breach
        "2025-06-15T12:10:00Z,1.50",
    ]) + "\n"
    out = meter_check(csv, limits, 2025, 250.0)
    assert out["readings"] == 3 and out["breaches"] == 1
    assert out["breach_rows"][0]["excess_w"] == pytest.approx(300.0)
    assert out["compliance_pct"] == pytest.approx(100 * 2 / 3, abs=1e-3)


def test_meter_check_reads_quarter_hour_readings_against_each_quarter():
    from kapacitet.engine.delivery import MeterError, meter_check

    limits = np.full(8760, 2.0)
    limits[24 * 165 + 12] = 1.0                       # 2025-06-15 12:00-13:00 UTC+1 held to 1 kW
    # quarter-hour energy, as a meter reports it: 0.30 kWh in 15 minutes is 1.2 kW on average
    csv = "timestamp,export_kwh\n" + "\n".join([
        "2025-06-15T12:45:00+01:00,0.30",     # 1.2 kW against 1 kW: within the tolerance
        "2025-06-15T13:00:00+01:00,0.40",     # 1.6 kW against 2 kW: fine
        "2025-06-15T12:30:00+01:00,0.35",     # 1.4 kW against 1 kW: a breach
        "2025-06-15T11:15:00Z,0.35",          # 12:15 UTC+1: the same held hour, a breach
    ]) + "\n"
    out = meter_check(csv, limits, 2025, 250.0, 15)
    assert out["reading"] == "energy_15min" and out["resolution_min"] == 15
    assert out["readings"] == 4 and out["breaches"] == 2 and out["intervals"] == 4 and out["breached_intervals"] == 2
    assert [r["excess_w"] for r in out["breach_rows"]] == [pytest.approx(400.0), pytest.approx(400.0)]
    assert out["breach_rows"][0]["interval_start"] == "2025-06-15T11:30:00+00:00"
    with pytest.raises(MeterError) as e:
        meter_check("timestamp,export_kwh\n2025-06-15T12:05:00+01:00,0.1\n", limits, 2025, 250.0, 15)
    assert e.value.code == "meter_kwh_grid"
    # power readings at five minutes fall into their quarter the same way
    csv = "timestamp,export_kw\n2025-06-15T12:50:00+01:00,1.30\n2025-06-15T13:05:00+01:00,2.10\n"
    out = meter_check(csv, limits, 2025, 250.0, 15)
    assert out["breaches"] == 1 and out["breach_rows"][0]["interval_start"] == "2025-06-15T11:45:00+00:00"


def test_meter_check_refuses_readings_without_an_offset():
    from kapacitet.engine.delivery import MeterError, meter_check

    with pytest.raises(MeterError) as e:
        meter_check("timestamp,export_kw\n2025-06-15 12:00,1.0\n", np.ones(8760), 2025, 250.0)
    assert e.value.code == "meter_naive_time"
    with pytest.raises(MeterError) as e:
        meter_check("when,kw\n1,2\n", np.ones(8760), 2025, 250.0)
    assert e.value.code == "meter_columns"


def test_the_share_is_a_percentage_and_nothing_lost_where_nothing_was_produced():
    from kapacitet.engine.delivery import share_pct

    out = share_pct(np.array([1.0, 5.0, 0.0, 2.0]), np.array([100.0, 50.0, 0.0, 0.0]))
    assert out.tolist() == [1.0, 10.0, 0.0, 0.0]
    assert share_pct(3.0, 200.0) == pytest.approx(1.5)
    assert share_pct(np.zeros(0), np.zeros(0)).shape == (0,)
    # each row against its own potential, and one potential for every row
    rows = share_pct(np.array([[1.0, 2.0], [3.0, 4.0]]), np.array([[10.0], [0.0]]))
    assert rows.tolist() == [[10.0, 20.0], [0.0, 0.0]]


def test_go_needs_zero_violations_and_every_agreement_within_its_level():
    from kapacitet.engine.delivery import go_no_go

    season = np.arange(120, 243)
    days = {"violation_hours": [0] * 365}
    per = {"curtailed": np.array([[1.0] * 365, [5.0] * 365]), "pv": np.array([[100.0] * 365] * 2), "cids": [7, 8]}
    v = go_no_go(days, per, season, agreed_level_pct=3.0)
    assert v["safety_ok"] and v["within_level"] == 1 and not v["go"]       # house 8 at 5 % > 3 %
    v = go_no_go(days, per, season, agreed_level_pct=6.0)
    assert v["go"]
    days["violation_hours"][150] = 1
    assert not go_no_go(days, per, season, 6.0)["go"]


@needs_operation
def test_the_shadow_days_add_up_to_the_year_and_the_day_ahead_season_is_safe():
    from kapacitet.paths import processed_ro

    root = processed_ro("results", "operation")
    for basis in ("dayahead", "foresight"):
        days = json.loads((root / basis / "maxmin" / "days.json").read_text(encoding="utf-8"))
        report = json.loads((root / basis / "maxmin" / "compliance.json").read_text(encoding="utf-8"))
        assert sum(days["curtailed_kwh"]) == pytest.approx(report["curtailed_kwh"], rel=1e-3, abs=0.5)
        assert sum(days["violation_hours"]) == 0
        npz = np.load(root / basis / "maxmin" / "days.npz")
        assert npz["curtailed"].sum() == pytest.approx(report["curtailed_kwh"], rel=1e-3, abs=0.5)


@needs_operation
def test_the_api_issues_a_schedule_seals_it_and_checks_a_meter():
    from fastapi.testclient import TestClient

    from kapacitet import seals
    from kapacitet.api.main import app

    with TestClient(app) as c:
        idx = c.get("/api/v1/operation/index").json()
        cid, firm, year = idx["nonfirm_cids"][0], idx["firm_cids"][0], idx["year"]
        day = f"{year}-06-15"
        sch = c.get("/api/v1/operation/schedule", params={"cid": cid, "date": day, "resolution": 60}).json()
        assert len(sch["rows"]) == 24 and sch["basis"] == "dayahead" and sch["resolution_min"] == 60
        assert sch["default_kw"] <= min(r["limit_kw"] for r in sch["rows"]) + 1e-6
        # by default the market's quarter-hours: the delivery day on Stockholm's clock, each quarter
        # with its hour's verified envelope, checked hour by hour against it
        q = c.get("/api/v1/operation/schedule", params={"cid": cid, "date": day}).json()
        assert q["resolution_min"] == 15 and q["inputs_resolution_min"] == 60 and len(q["rows"]) == 96
        assert q["within_envelope"]["ok"] and q["within_envelope"]["hours"] == 24
        assert {r["start_epoch"] for r in sch["rows"][:23]} <= {r["start_epoch"] for r in q["rows"]}
        odd = c.get("/api/v1/operation/schedule", params={"cid": cid, "date": day, "resolution": 30})
        assert odd.status_code == 422 and odd.json()["detail"]["code"] == "resolution"

        # a preview leaves no record, whatever it is asked (a GET never seals); an issue is a POST,
        # sealed with the limits it sent
        preview = c.get("/api/v1/operation/schedule.xml", params={"cid": cid, "date": day})
        assert preview.status_code == 200 and "content-disposition" not in preview.headers
        assert c.get("/api/v1/operation/schedule.xml", params={"cid": cid, "date": day, "issue": "true"}).status_code == 200
        assert not seals.sealed_of_kind("schedule")
        issued = c.post("/api/v1/operation/schedule/issue", json={"cid": cid, "date": day})
        assert issued.status_code == 200 and "attachment" in issued.headers["content-disposition"]
        kept = seals.sealed_of_kind("schedule")
        assert len(kept) == 1 and kept[0]["record"]["cid"] == cid and len(kept[0]["record"]["limits"]) == 96
        assert kept[0]["record"]["resolution_min"] == 15
        # the seal travels with the file, so the page can keep a receipt of what it issued
        assert issued.headers["x-seal-digest"] == kept[0]["entry"]["digest"] and issued.headers["x-seal-seq"].isdigit()
        assert "x-seal-digest" not in preview.headers
        assert ET.fromstring(issued.content).get("all") == "96"
        assert {d.text for d in ET.fromstring(issued.content).iterfind("s:DERControl/s:interval/s:duration", NS)} == {"900"}
        # an issue sent again under the same Idempotency-Key is the first one, document and seal
        key = {"Idempotency-Key": "inverter-retry-0001"}
        first = c.post("/api/v1/operation/schedule/issue", json={"cid": cid, "date": day}, headers=key)
        again = c.post("/api/v1/operation/schedule/issue", json={"cid": cid, "date": day}, headers=key)
        assert first.headers["x-seal-seq"] == again.headers["x-seal-seq"] and first.content == again.content
        assert len(seals.sealed_of_kind("schedule")) == 2
        assert c.post("/api/v1/operation/schedule/issue", json={"cid": cid, "date": day},
                      headers={"Idempotency-Key": "../x"}).status_code == 422

        # the synthetic day carries one deliberate breach of ten minutes, in an hour its limit bound,
        # and the check finds those two readings and nothing else
        csv = c.get("/api/v1/operation/meter-example", params={"cid": cid, "date": day}).text
        out = c.post("/api/v1/operation/meter-check", json={"cid": cid, "csv": csv}).json()
        assert out["readings"] == 24 * 12 and out["breaches"] == 2
        assert all(r["excess_w"] == pytest.approx(400.0, abs=1.0) for r in out["breach_rows"])
        bound = [r for r in sch["rows"] if r["desired_kw"] is not None and r["desired_kw"] > r["limit_kw"] + 1e-6]
        if bound:
            assert all(r["limit_w"] <= 1000 * max(b["limit_kw"] for b in bound) + 1 for r in out["breach_rows"])

        refused = c.get("/api/v1/operation/schedule", params={"cid": firm, "date": day})
        assert refused.status_code == 404 and refused.json()["detail"]["code"] == "not_an_agreement"
        late = c.get("/api/v1/operation/schedule", params={"cid": cid, "date": f"{year + 1}-01-01"})
        assert late.status_code == 422 and late.json()["detail"]["code"] == "outside_year"
