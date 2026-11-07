"""The fleet check (engine/meter_compliance.py, `kapacitet meter-compliance`): every agreement's
metered export against the day-ahead envelope of each hour. Runs on a tiny operation stage written
here, so it needs no data."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from fixtures import meters_tiny as mt
from kapacitet.engine import meter_compliance as mc
from kapacitet.timeindex import year_index

YEAR = 2025
CIDS = [3, 5, 7]
H = len(year_index(YEAR))


def _operation(root, verified=True) -> tuple[np.ndarray, np.ndarray]:
    """A verified day-ahead combination for three agreements, with a midday sun the envelope
    holds back, and a network of eight customers whose ids are C0..C7."""
    hod = np.arange(H) % 24
    sun = np.clip(np.sin((hod - 6) / 12 * np.pi), 0, None)
    desired = np.vstack([6.0 * sun, 8.0 * sun, 4.0 * sun])
    env = np.vstack([np.full(H, 3.0), np.full(H, 5.0), np.full(H, 10.0)])   # the third never binds
    (root / "results" / "operation").mkdir(parents=True)
    (root / "results" / "operation" / "index.json").write_text(json.dumps({
        "year": YEAR, "nonfirm_cids": CIDS,
        "combos": {"dayahead": {"maxmin": {"verified": verified}}, "foresight": {"maxmin": {"verified": True}}}}))
    ui = root / "ui" / "operation" / "dayahead" / "maxmin"
    ui.mkdir(parents=True)
    np.save(ui / "envelopes.npy", env.astype(np.float16))
    np.save(root / "ui" / "operation" / "desired.npy", desired.astype(np.float16))
    (root / "grid").mkdir()
    pd.DataFrame({"cid": range(8), "osm_id": [f"C{i}" for i in range(8)]}).to_parquet(root / "grid" / "customers.parquet")
    return desired.astype(np.float16).astype(float), env.astype(np.float16).astype(float)


@pytest.fixture
def root(tmp_path):
    folder = tmp_path / "processed"
    folder.mkdir()
    return folder


def _hourly_file(root, rows: list[tuple[str, str, float]]):
    (root / "meters").mkdir(exist_ok=True)
    pd.DataFrame({"customer_id": [r[0] for r in rows], "start": pd.to_datetime([r[1] for r in rows], utc=True),
                  "kwh_import": 0.0, "kwh_export": [r[2] for r in rows]}).to_parquet(root / "meters" / "hourly.parquet")


def _by_cid(report) -> dict:
    return {p["cid"]: p for p in report["per_customer"]}


def test_a_compliant_fleet_flags_no_one(root):
    _operation(root)
    r = mc.build(root, "maxmin", synthetic=True)
    s = r["summary"]
    assert r["readings"] == "synthetic" and r["basis"] == "dayahead"
    assert (s["agreements"], s["compliant"], s["non_compliant"], s["no_readings"]) == (3, 3, 0, 0)
    assert s["intervals_checked"] == 3 * H and s["intervals_above"] == 0 and s["energy_above_kwh"] == 0.0
    assert s["intervals_within_pct"] == 100.0
    assert all(p["worst"] is None for p in r["per_customer"])
    assert "Syntetiska värden" in mc.text_sv(r)


def test_injected_over_export_is_flagged_with_the_right_energy(root):
    desired, env = _operation(root)
    share = 0.5
    r = mc.build(root, "maxmin", synthetic=True, inject={5: share})
    per = _by_cid(r)
    assert per[5]["status"] == "non_compliant"
    assert per[3]["status"] == per[7]["status"] == "compliant"
    excess = share * np.maximum(desired[1] - env[1], 0.0)
    above = excess > 0.25
    assert per[5]["intervals_above"] == int(above.sum()) > 0
    assert per[5]["energy_above_kwh"] == pytest.approx(excess[above].sum(), abs=1e-3)
    assert r["summary"]["energy_above_kwh"] == pytest.approx(excess[above].sum(), abs=1e-3)
    k = int(np.argmax(excess))
    assert per[5]["worst"]["hour"] == k and per[5]["worst"]["excess_kw"] == pytest.approx(excess[k], abs=1e-3)
    assert per[5]["worst"]["start"] == year_index(YEAR)[k].isoformat()
    assert r["per_customer"][0]["cid"] == 5               # the house above its envelope comes first
    assert r["injected"] == {"5": share}
    text = mc.text_sv(r)
    assert "kund 5 (C1)" not in text and "kund 5 (C5)" in text


def test_the_tolerance_holds(root):
    _, env = _operation(root)
    t0 = year_index(YEAR)[24 * 150 + 11]                 # an hour where agreement 3 has 3 kW
    hours = [t0 + pd.Timedelta(hours=i) for i in range(3)]
    tol = 0.25
    _hourly_file(root, [("C3", hours[0].isoformat(), 3.0 + tol - 0.01),
                        ("C3", hours[1].isoformat(), 3.0 + tol),          # exactly the tolerance: not above
                        ("C3", hours[2].isoformat(), 3.0 + tol + 0.01)])
    r = mc.build(root, "maxmin", tolerance_kw=tol)
    p = _by_cid(r)[3]
    assert r["readings"] == "metered" and r["reading_resolution_min"] == 60
    assert p["intervals_checked"] == 3 and p["intervals_above"] == 1 and p["status"] == "non_compliant"
    assert p["energy_above_kwh"] == pytest.approx(tol + 0.01, abs=1e-6)
    assert _by_cid(r)[5]["status"] == "no_readings"
    # with a wider tolerance the same hours are compliant
    assert _by_cid(mc.build(root, "maxmin", tolerance_kw=0.3))[3]["status"] == "compliant"
    # by default the tolerance is the assumption's
    from kapacitet.config import A
    assert mc.build(root, "maxmin")["tolerance_kw"] == float(A("compliance_tolerance_kw"))


def test_15_minute_readings_aggregate_to_the_hour(root):
    _operation(root)
    # 11:00 to 12:00 summer time on 1 June is 09:00 to 10:00 UTC, the hour starting 10:00 in UTC+1
    q = pd.date_range("2025-06-01T11:00:00+02:00", periods=4, freq="15min")
    kwh = [1.0, 1.5, 1.2, 0.9]                            # 4.6 kWh in the hour: 4.6 kW against 3 kW
    rows = [("C3", t.isoformat(), v) for t, v in zip(q, kwh)]
    rows += [("C3", (q[0] + pd.Timedelta(hours=1, minutes=15 * i)).isoformat(), 2.0) for i in range(3)]  # 3 of 4
    rows += [(c, t.isoformat(), 1.0) for c in ("C9", "C0") for t in q]   # nobody's, and a firm customer's
    (root / "meters").mkdir()
    pd.DataFrame({"customer_id": [r[0] for r in rows], "start": pd.to_datetime([r[1] for r in rows], utc=True),
                  "kwh_export": [r[2] for r in rows]}).to_parquet(root / "meters" / "export_15min.parquet")

    hours = mc.to_hours(pd.read_parquet(root / "meters" / "export_15min.parquet"))
    c3 = hours[hours["customer_id"] == "C3"]
    assert len(c3) == 1 and c3["kwh_export"].iloc[0] == pytest.approx(sum(kwh))
    assert c3["start"].iloc[0] == pd.Timestamp("2025-06-01T09:00:00Z")

    r = mc.build(root, "maxmin")
    p = _by_cid(r)[3]
    assert r["reading_resolution_min"] == 15 and r["interval_min"] == 60
    assert p["intervals_checked"] == 1 and p["intervals_above"] == 1
    assert p["energy_above_kwh"] == pytest.approx(sum(kwh) - 3.0, abs=1e-6)
    assert p["worst"]["hour"] == 151 * 24 + 10 and p["worst"]["start"] == "2025-06-01T09:00:00+00:00"
    assert r["summary"]["unmatched_meters"] == 1 and r["summary"]["meters_without_agreement"] == 1
    assert "mätvärden per kvart" in mc.text_sv(r)


def test_the_period_and_the_readings_outside_it(root):
    _operation(root)
    _hourly_file(root, [("C3", "2025-03-01T10:00:00Z", 1.0), ("C3", "2025-06-01T10:00:00Z", 1.0),
                        ("C3", "2024-06-01T10:00:00Z", 1.0)])
    r = mc.build(root, "maxmin", start="2025-05-01", end="2025-06-30")
    assert r["period"] == {"from": "2025-05-01", "to": "2025-06-30"}
    assert _by_cid(r)[3]["intervals_checked"] == 1 and r["summary"]["outside_period"] == 2
    with pytest.raises(mc.MeterComplianceError, match="study year 2025"):
        mc.build(root, "maxmin", start="2024-05-01")
    with pytest.raises(mc.MeterComplianceError, match="before it starts"):
        mc.build(root, "maxmin", start="2025-06-01", end="2025-05-01")


def test_without_readings_nothing_is_checked_and_injection_needs_the_synthetic_fleet(root):
    _operation(root)
    assert mc.build(root, "maxmin") is None
    with pytest.raises(mc.MeterComplianceError, match="needs --synthetic"):
        mc.build(root, "maxmin", inject={5: 1.0})
    with pytest.raises(mc.MeterComplianceError, match="without a conditional agreement"):
        mc.build(root, "maxmin", synthetic=True, inject={4: 1.0})
    assert mc.parse_inject(["5:0.5", "7:1,3:0"]) == {5: 0.5, 7: 1.0, 3: 0.0}
    for bad in ("5", "5:-1", "x:1"):
        with pytest.raises(mc.MeterComplianceError):
            mc.parse_inject([bad])


def test_only_a_verified_day_ahead_combination_is_checked_against(tmp_path):
    root = tmp_path / "processed"
    root.mkdir()
    with pytest.raises(mc.MeterComplianceError, match="index.json is missing"):
        mc.build(root, "maxmin", synthetic=True)
    _operation(root, verified=False)
    with pytest.raises(mc.MeterComplianceError, match="did not verify"):
        mc.build(root, "maxmin", synthetic=True)
    with pytest.raises(mc.MeterComplianceError, match="dayahead/prorata was not computed"):
        mc.build(root, "prorata", synthetic=True)


def test_the_command_writes_nothing_without_readings_and_both_files_with_them(root, monkeypatch, capsys):
    from kapacitet import paths
    from kapacitet.pipeline import main

    _operation(root)
    monkeypatch.setattr(paths, "PROCESSED", root)
    with pytest.raises(SystemExit) as stop:
        main(["meter-compliance", "--policy", "maxmin"])
    assert stop.value.code == 1 and "no meter readings" in capsys.readouterr().out
    assert not (root / "results" / "meter_compliance.json").exists()

    main(["meter-compliance", "--policy", "maxmin", "--synthetic", "--inject", "5:1"])
    out = capsys.readouterr().out
    assert "1 non-compliant" in out and "synthetic" in out
    report = json.loads((root / "results" / "meter_compliance.json").read_text(encoding="utf-8"))
    assert report["summary"]["non_compliant"] == 1
    assert (root / "results" / "meter_compliance_sv.txt").read_text(encoding="utf-8").startswith("Efterlevnad")


def test_the_ingest_keeps_the_export_at_15_minutes(tmp_path):
    from kapacitet.ingest import meters

    folder = tmp_path / "in"
    mt.write(folder, mt.frame(export=True))
    report, hourly = meters.ingest(meters.meter_files(folder), tmp_path / "out")
    assert report.usable
    q = pd.read_parquet(tmp_path / "out" / "export_15min.parquet")
    assert list(q.columns) == ["customer_id", "start", "kwh_export"]
    assert len(q) == len(mt.instants()) * len(mt.CUSTOMERS)
    # summed to the hour, the quarter-hours give the hourly file's export
    back = mc.to_hours(q).set_index(["customer_id", "start"])["kwh_export"]
    h = hourly.dropna(subset=["kwh_export"]).set_index(["customer_id", "start"])["kwh_export"]
    pd.testing.assert_series_equal(back.reindex(h.index), h, check_names=False, check_index_type=False)
    # without an export column there is no such file
    mt.write(tmp_path / "in2", mt.frame())
    meters.ingest(meters.meter_files(tmp_path / "in2"), tmp_path / "out2")
    assert not (tmp_path / "out2" / "export_15min.parquet").exists()


def test_the_endpoint_serves_the_file_and_names_the_command_without_it(root, monkeypatch):
    from fastapi.testclient import TestClient

    from kapacitet import paths
    from kapacitet.api.main import app, command_for

    assert command_for("meter_compliance.json") == "python -m kapacitet meter-compliance"
    assert command_for("meter_compliance_sv.txt") == "python -m kapacitet meter-compliance"
    _operation(root)
    monkeypatch.setattr(paths, "PROCESSED", root)
    client = TestClient(app)
    gone = client.get("/api/v1/operation/meter-compliance")
    assert gone.status_code == 404 and gone.json()["detail"]["command"] == "python -m kapacitet meter-compliance"
    mc.write(mc.build(root, "maxmin", synthetic=True, inject={7: 1.0}), root)
    got = client.get("/api/v1/operation/meter-compliance")
    assert got.status_code == 200
    body = got.json()
    assert body["data"]["readings"] == "synthetic" and "provenance" in body
    text = client.get("/api/v1/operation/meter-compliance_sv.txt")
    assert text.status_code == 200 and "Efterlevnad" in text.text


def test_quarter_hours_and_hourly_rows_are_merged_per_customer(root):
    _operation(root)
    t0 = year_index(YEAR)[24 * 150 + 11]
    q = pd.date_range(t0, periods=4, freq="15min")
    (root / "meters").mkdir()
    rows = [("C3", t.isoformat(), 0.5) for t in q] + [("C3", (t0 + pd.Timedelta(hours=1)).isoformat(), 1.0)]  # 1 incomplete
    pd.DataFrame({"customer_id": [r[0] for r in rows], "start": pd.to_datetime([r[1] for r in rows], utc=True),
                  "kwh_export": [r[2] for r in rows]}).to_parquet(root / "meters" / "export_15min.parquet")
    _hourly_file(root, [("C5", t0.isoformat(), 6.0), ("C3", t0.isoformat(), 99.0)])   # C3 stays on its quarters
    r = mc.build(root, "maxmin")
    assert r["reading_resolution_min"] == 15 and r["summary"]["incomplete_hours"] == 1
    assert _by_cid(r)[3]["status"] == "compliant" and _by_cid(r)[3]["intervals_checked"] == 1
    p5 = _by_cid(r)[5]
    assert p5["status"] == "non_compliant" and p5["energy_above_kwh"] == pytest.approx(1.0)


def test_a_compliant_customer_has_no_worst_hour_and_a_missing_envelope_is_unchecked(root):
    _, env = _operation(root)
    t0 = year_index(YEAR)[24 * 150 + 11]
    _hourly_file(root, [("C3", t0.isoformat(), 3.1), ("C5", t0.isoformat(), 1.0)])
    p = _by_cid(mc.build(root, "maxmin", tolerance_kw=0.25))
    assert p[3]["status"] == "compliant" and p[3]["worst"] is None
    bad = env.copy()
    bad[0, 24 * 150 + 11] = np.nan
    r = mc.check(pd.DataFrame({"cid": [3], "hour": [24 * 150 + 11], "kwh_export": [9.0]}), bad, CIDS, YEAR, 0.25)
    assert _by_cid(r)[3]["intervals_checked"] == 0 and _by_cid(r)[3]["status"] == "no_readings"
