"""EV charger limits as an OCPP 1.6J SetChargingProfile request (engine/ocpp.py and
GET /operation/schedule.ocpp.json), and the import envelopes issued the day before that it sends
(doe_import.issue_day_ahead). Everything runs without data: the route reads a folder the test
writes, and the envelopes are issued on the tiny grid."""

from __future__ import annotations

import copy
import json
from datetime import date

import numpy as np
import pytest

from conftest import processed_at

YEAR = 2025
DAY = "2025-06-15"
FIRM = 11.0


def _envelopes(n: int = 2) -> np.ndarray:
    """(n, H) kW: the firm limit everywhere but a few hours of the test day, one of them zero."""
    from kapacitet.engine.delivery import day_of

    env = np.full((n, 8760), FIRM, dtype=np.float32)
    h0 = day_of(YEAR, date.fromisoformat(DAY)) * 24
    env[0, h0 + 17: h0 + 21] = [3.25, 1.0, 0.0, 6.04999]
    env[1, h0 + 7] = 10.5
    return env


@pytest.fixture()
def api(tmp_path):
    """The API reading a data folder with nothing but the issued import envelopes."""
    from fastapi.testclient import TestClient

    from kapacitet.api.main import app

    folder = tmp_path / "data" / "processed"
    (folder / "results" / "operation" / "dayahead").mkdir(parents=True)
    (folder / "ui" / "operation" / "dayahead").mkdir(parents=True)
    meta = {"basis": "dayahead", "year": YEAR, "rule": "maxmin", "cids": [7, 42], "firm_kw": FIRM,
            "verified": True, "planning_violation_hours": 0}
    (folder / "results" / "operation" / "dayahead" / "import.json").write_text(json.dumps(meta), encoding="utf-8")
    np.save(folder / "ui" / "operation" / "dayahead" / "import_envelopes.npy", _envelopes())
    with processed_at(folder), TestClient(app) as c:
        c.folder = folder
        yield c


def _get(c, cid=7, day=DAY, resolution=60):
    params = {"cid": cid, "date": day}
    if resolution is not None:
        params["resolution"] = resolution
    return c.get("/api/v1/operation/schedule.ocpp.json", params=params)


def test_the_periods_are_the_issued_envelope_and_the_default_covers_the_rest(api):
    from kapacitet.engine import ocpp

    r = _get(api)
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers["content-disposition"] and r.headers["x-ocpp-action"] == "SetChargingProfile"
    assert "not certified" in r.headers["x-format-notice"]
    body = r.json()
    assert ocpp.validate(body) == []
    prof = body["csChargingProfiles"]
    sch = prof["chargingSchedule"]
    assert body["connectorId"] == 0 and prof["chargingProfilePurpose"] == "ChargePointMaxProfile"
    assert prof["chargingProfileKind"] == "Absolute" and sch["chargingRateUnit"] == "W"
    # local midnight of the study day (UTC+1) is 23:00 UTC the day before
    assert sch["startSchedule"] == "2025-06-14T23:00:00Z" and sch["duration"] == 24 * 3600
    periods = sch["chargingSchedulePeriod"]
    assert [p["startPeriod"] for p in periods] == [3600 * h for h in range(24)]
    limits = [p["limit"] for p in periods]
    # the envelope in watts, rounded down to a tenth so it never exceeds what was issued
    assert limits[17:21] == [3250.0, 1000.0, 0.0, 6049.9]
    # every hour without an envelope keeps the firm import limit
    assert all(w == FIRM * 1000 for h, w in enumerate(limits) if not 17 <= h <= 20)
    other = _get(api, cid=42).json()["csChargingProfiles"]["chargingSchedule"]["chargingSchedulePeriod"]
    assert other[7]["limit"] == 10500.0 and sum(p["limit"] == FIRM * 1000 for p in other) == 23


def test_the_default_step_is_the_quarter_hour_from_local_midnight(api):
    """Without a resolution the profile follows schedule_resolution_min (15 minutes): the Stockholm
    delivery day, 22:00 UTC the day before in summer, with each quarter carrying its hour's envelope."""
    from kapacitet.engine import ocpp

    body = _get(api, resolution=None).json()
    assert ocpp.validate(body) == []
    sch = body["csChargingProfiles"]["chargingSchedule"]
    assert sch["startSchedule"] == "2025-06-14T22:00:00Z" and sch["duration"] == 24 * 3600
    periods = sch["chargingSchedulePeriod"]
    assert [p["startPeriod"] for p in periods] == [900 * q for q in range(96)]
    hourly = [p["limit"] for p in _get(api).json()["csChargingProfiles"]["chargingSchedule"]["chargingSchedulePeriod"]]
    quarters = [p["limit"] for p in periods]
    # the Stockholm day starts an hour before the UTC+1 study day in summer, so quarter q of the
    # local day is hour (q // 4) - 1 of the study day; every quarter keeps that hour's limit
    assert all(quarters[q] == hourly[q // 4 - 1] for q in range(4, 96))


def test_the_route_refuses_what_it_cannot_send(api):
    r = _get(api, cid=8)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "no_charger"
    r = _get(api, day="2026-01-01")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "outside_year"
    path = api.folder / "results" / "operation" / "dayahead" / "import.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**meta, "verified": False, "planning_violation_hours": 3}), encoding="utf-8")
    r = _get(api)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "import_unverified"
    path.unlink()
    r = _get(api)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_computed"
    assert "--only operation" in r.json()["detail"]["command"]


def test_a_changed_window_leaves_the_import_envelopes_stale_until_issued_again(api):
    """import.json names its window; once the DSO's setting differs, the API says the envelopes are
    stale, with the command that issues them again, and the OCPP route refuses to send them. An
    import.json that names no window (written before the setting) counts as the default's."""
    from kapacitet import dso, seals

    s = api.get("/api/v1/settings").json()
    assert s["settings"]["import_window_days"] == dso.IMPORT_WINDOW_DEFAULT
    assert s["import_envelopes"] == {"computed": True, "window_days": dso.IMPORT_WINDOW_DEFAULT,
                                     "setting_window_days": dso.IMPORT_WINDOW_DEFAULT, "stale": False,
                                     "actual_violation_hours": s["import_envelopes"]["actual_violation_hours"],
                                     "exceedance_hours": s["import_envelopes"]["exceedance_hours"],
                                     "command": s["import_envelopes"]["command"]}
    r = api.put("/api/v1/settings", json={"import_window_days": 0})
    assert r.status_code == 200 and r.json()["import_envelopes"]["stale"] is True
    assert "--only operation" in r.json()["import_envelopes"]["command"]
    assert seals.sealed_of_kind("settings")[-1]["record"]["after"]["import_window_days"] == 0
    r = _get(api)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "import_stale"
    assert r.json()["detail"]["params"]["issued"] == dso.IMPORT_WINDOW_DEFAULT
    assert r.json()["detail"]["params"]["setting"] == 0
    # issued again under the setting: current, and sent
    path = api.folder / "results" / "operation" / "dayahead" / "import.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**meta, "planning_state": {"window_days": 0}}), encoding="utf-8")
    assert api.get("/api/v1/settings").json()["import_envelopes"]["stale"] is False
    assert _get(api).status_code == 200
    assert api.put("/api/v1/settings", json={"import_window_days": -1}).status_code == 422


def test_only_planners_and_above_fetch_it_with_sign_in_on():
    from kapacitet.api import auth, guard

    path = "/api/v1/operation/schedule.ocpp.json"
    assert guard.roles_for("GET", path) == guard.PLANNER_UP
    assert "auditor" not in guard.roles_for("GET", path)
    assert guard.roles_for("GET", "/api/v1/operation/schedule.xml") == guard.ANY_ROLE
    assert auth._needs()["ocpp"] == list(guard.PLANNER_UP)


def _good() -> dict:
    from kapacitet.engine import ocpp

    steps = [{"start_epoch": 1749942000 + 3600 * h, "limit_kw": None if h % 3 else 2.5} for h in range(24)]
    return ocpp.charging_profile(steps, FIRM, 3600)


def _broken(edit) -> list[str]:
    from kapacitet.engine import ocpp

    p = copy.deepcopy(_good())
    edit(p)
    return ocpp.validate(p)


def test_the_validator_accepts_the_payload_it_is_written_for():
    from kapacitet.engine import ocpp

    assert ocpp.validate(_good()) == []


@pytest.mark.parametrize("edit, field", [
    (lambda p: p.pop("connectorId"), "connectorId"),
    (lambda p: p.update(connectorId=1), "connector 0"),
    (lambda p: p.update(connectorId="0"), "connectorId"),
    (lambda p: p["csChargingProfiles"].pop("chargingProfileId"), "chargingProfileId"),
    (lambda p: p["csChargingProfiles"].update(stackLevel=-1), "stackLevel"),
    (lambda p: p["csChargingProfiles"].update(chargingProfilePurpose="MaxProfile"), "chargingProfilePurpose"),
    (lambda p: p["csChargingProfiles"].update(chargingProfileKind="absolute"), "chargingProfileKind"),
    (lambda p: p["csChargingProfiles"].update(transactionId=5), "transactionId"),
    (lambda p: p["csChargingProfiles"].update(validFrom="tomorrow"), "validFrom"),
    (lambda p: p["csChargingProfiles"]["chargingSchedule"].update(chargingRateUnit="kW"), "chargingRateUnit"),
    (lambda p: p["csChargingProfiles"]["chargingSchedule"].pop("startSchedule"), "startSchedule"),
    (lambda p: p["csChargingProfiles"]["chargingSchedule"].update(startSchedule="2025-06-14T23:00:00"), "startSchedule"),
    (lambda p: p["csChargingProfiles"]["chargingSchedule"].update(duration=0), "duration"),
    (lambda p: p["csChargingProfiles"]["chargingSchedule"].update(chargingSchedulePeriod=[]), "chargingSchedulePeriod"),
    (lambda p: p["csChargingProfiles"]["chargingSchedule"]["chargingSchedulePeriod"][0].update(startPeriod=60), "starts at 0"),
    (lambda p: p["csChargingProfiles"]["chargingSchedule"]["chargingSchedulePeriod"][5].update(startPeriod=3600), "ascend"),
    (lambda p: p["csChargingProfiles"]["chargingSchedule"]["chargingSchedulePeriod"][2].update(limit=-1), "limit"),
    (lambda p: p["csChargingProfiles"]["chargingSchedule"]["chargingSchedulePeriod"][2].update(limit=1.25), "one decimal"),
    (lambda p: p["csChargingProfiles"]["chargingSchedule"]["chargingSchedulePeriod"][2].update(limit="11000"), "limit"),
    (lambda p: p["csChargingProfiles"]["chargingSchedule"]["chargingSchedulePeriod"][2].update(numberPhases=4), "numberPhases"),
    (lambda p: p["csChargingProfiles"]["chargingSchedule"]["chargingSchedulePeriod"][23].update(startPeriod=86400), "ends"),
    (lambda p: p.pop("csChargingProfiles"), "csChargingProfiles"),
])
def test_the_validator_rejects_a_malformed_payload(edit, field):
    errs = _broken(edit)
    assert errs and any(field in e for e in errs), errs


def test_issued_import_envelopes_keep_every_limit_with_every_charger_at_its_envelope(grid_model):
    """On a heavy evening load every charger drawing its envelope at once breaks nothing the
    state without them did not already break, and an hour with room keeps the charger's rating."""
    from kapacitet.config import A
    from kapacitet.engine import doe_import

    m = grid_model
    C, H = m.C, 48
    rng = np.random.default_rng(3)
    tanphi = float(np.tan(np.arccos(A("load_power_factor"))))
    load = np.zeros((C, H))
    load[:, 24:] = rng.uniform(2.0, 6.0, size=(C, 24))     # a light day, then a heavy one
    plan = {"p_net": -load, "q": -load * tanphi}
    actual = {"p_net": -0.8 * load, "q": -0.8 * load * tanphi}
    flex = np.zeros(C, dtype=bool)
    flex[rng.choice(C, size=max(2, C // 2), replace=False)] = True
    r = doe_import.issue_day_ahead(m, plan, actual, flex)
    assert r.verified and r.envelope.shape == (int(flex.sum()), H)
    assert (r.envelope >= 0).all() and (r.envelope <= r.firm_kw + 1e-6).all()
    draw = np.zeros((C, H))
    draw[flex] = r.envelope
    p, q = plan["p_net"] - draw, plan["q"] - draw * tanphi
    firm = m.violations(m.solve(plan["p_net"], plan["q"]), plan["p_net"]).import_side
    v = m.violations(m.solve(p, q), p).import_side
    assert not (v & ~firm).any()
    # an hour the linear screen clears with every charger at its rating needs no envelope
    corner = np.where(flex[:, None], r.firm_kw, 0.0) * np.ones((1, H))
    clear = np.setdiff1d(np.arange(H), m.import_critical_hours(plan["p_net"] - corner, plan["q"] - corner * tanphi))
    assert np.allclose(r.envelope[:, clear], r.firm_kw)
    assert r.actual_violation_hours == 0
    # the real year imports less than the plan everywhere, so the audit has nothing to count
    assert r.exceedance["customer_hours_over"] == r.exceedance["aggregate_hours_over"] == 0


def test_the_import_audit_counts_an_injected_exceedance():
    """One house importing 2 kW more than planned in one hour is one customer-hour, one hour and one
    hour of the total over the plan; importing less is never counted."""
    from kapacitet.engine.doe_import import import_exceedance

    rng = np.random.default_rng(5)
    load = rng.uniform(0.5, 4.0, size=(6, 48))
    plan = {"p_net": -load, "q": -0.3 * load}
    assert import_exceedance(plan, plan)["customer_hours_over"] == 0
    actual = {"p_net": plan["p_net"] + 0.5, "q": plan["q"]}     # every house imports 0.5 kW less...
    actual["p_net"][2, 30] = plan["p_net"][2, 30] - 2.0          # ...but one, once, 2 kW more
    flex = np.zeros(6, dtype=bool)
    flex[2] = True
    a = import_exceedance(plan, actual, flex)
    assert a["customer_hours_over"] == a["hours_over"] == 1 and a["flex_customer_hours_over"] == 1
    assert a["worst_customer_kw"] == pytest.approx(2.0)
    assert a["aggregate_hours_over"] == 0                       # the other five houses make up for it
    assert a["first_hours"]["customer"] == [30]
    actual["p_net"][:, 30] = plan["p_net"][:, 30] - 1.0          # now every house, so the total too
    a = import_exceedance(plan, actual, ~flex)
    assert a["customer_hours_over"] == 6 and a["aggregate_hours_over"] == 1 and a["flex_customer_hours_over"] == 5
    assert a["worst_aggregate_kw"] == pytest.approx(6.0) and a["first_hours"]["aggregate"] == [30]


def test_both_import_windows_verify_and_the_audit_explains_any_real_violation(tiny_processed, tmp_path):
    """The operation stage's import envelopes under the DSO's window, 14 days (the default) and 0
    (the same calendar day only): both verify against their plan, and import.json names the window
    and its audit. At 14 days the real year keeps every limit. At 0 days the tiny grid's ceiling is
    one sample per hour (it has two weather years), the real year beats it in most hours, and the
    real violations it causes can only fall in hours the audit counts; the OCPP route then sends
    nothing. The narrower window is a lower ceiling, so its plan is beaten at least as often."""
    import shutil

    from conftest import synthetic_profiles, tiny_grid
    from kapacitet import dso
    from kapacitet.api.operation import _has_charging_profile
    from kapacitet.engine import operation, run
    from kapacitet.engine.scenario import Scenario, customers

    folder = tmp_path / "processed"
    shutil.copytree(tiny_processed, folder)
    audits = {}
    with tiny_grid(folder):
        m = run.model()
        hp, ev = run.background_masks(m.C)
        assert ev.any()
        y = max(synthetic_profiles.YEARS)
        pv = np.zeros(m.C, dtype=bool)
        pv[::2] = True
        arr = Scenario(year=y, pv=pv, hp=hp, ev=ev).arrays()
        cids = customers()["cid"].to_numpy()
        for window in (dso.IMPORT_WINDOW_DEFAULT, 0):
            dso.update(dso.DsoSettingsChange(import_window_days=window))
            out = operation.write_import(m, y, hp, ev, arr, cids)
            meta = json.loads((folder / "results" / "operation" / "dayahead" / "import.json").read_text(encoding="utf-8"))
            assert meta["planning_state"]["window_days"] == out["window_days"] == window
            assert meta["verified"] and meta["planning_violation_hours"] == 0
            ex = meta["exceedance"]
            assert out["exceedance_hours"] == ex["hours_over"]
            assert meta["actual_violation_hours"] <= ex["hours_over"]       # a dominated hour cannot break
            if window == dso.IMPORT_WINDOW_DEFAULT:
                assert meta["actual_violation_hours"] == 0
            assert _has_charging_profile(meta["cids"][0]) == (meta["actual_violation_hours"] == 0)
            audits[window] = ex
    assert audits[0]["customer_hours_over"] >= audits[dso.IMPORT_WINDOW_DEFAULT]["customer_hours_over"]
    assert audits[0]["aggregate_hours_over"] >= audits[dso.IMPORT_WINDOW_DEFAULT]["aggregate_hours_over"]


def _published_import() -> bool:
    from kapacitet.paths import processed_ro

    return processed_ro("results", "operation", "dayahead", "import.json").exists()


@pytest.mark.skipif(not _published_import(), reason="run: python -m kapacitet compute --stage extra --only operation")
def test_published_import_envelopes_are_verified_and_sent_as_issued():
    from fastapi.testclient import TestClient

    from kapacitet.api.main import app
    from kapacitet.engine import ocpp
    from kapacitet.engine.delivery import day_of
    from kapacitet.paths import processed_ro

    meta = json.loads(processed_ro("results", "operation", "dayahead", "import.json").read_text(encoding="utf-8"))
    env = np.load(processed_ro("ui", "operation", "dayahead", "import_envelopes.npy"))
    assert meta["verified"] and meta["planning_violation_hours"] == 0
    assert env.shape == (len(meta["cids"]), 24 * (len(env[0]) // 24))
    assert (env >= 0).all() and (env <= meta["firm_kw"] + 1e-6).all()
    day = f"{meta['year']}-01-15"
    h0 = day_of(meta["year"], date.fromisoformat(day)) * 24
    with TestClient(app) as c:
        for i, cid in enumerate(meta["cids"][:3]):
            # the hourly step, so each period is one issued hour of the study day
            body = c.get("/api/v1/operation/schedule.ocpp.json",
                         params={"cid": cid, "date": day, "resolution": 60}).json()
            assert ocpp.validate(body) == []
            sent = [p["limit"] for p in body["csChargingProfiles"]["chargingSchedule"]["chargingSchedulePeriod"]]
            issued = env[i, h0:h0 + 24].astype(float) * 1000.0
            assert len(sent) == 24
            assert all(s <= w + 1e-6 and w - s < 0.1 + 1e-6 for s, w in zip(sent, issued))
            # the default quarter-hour step never sends more than the hour it falls in
            quarters = c.get("/api/v1/operation/schedule.ocpp.json", params={"cid": cid, "date": day}).json()
            assert ocpp.validate(quarters) == []
            assert max(p["limit"] for p in quarters["csChargingProfiles"]["chargingSchedule"]["chargingSchedulePeriod"])                 <= max(issued) + 1e-6
