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


def _get(c, cid=7, day=DAY):
    return c.get("/api/v1/operation/schedule.ocpp.json", params={"cid": cid, "date": day})


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
            body = c.get("/api/v1/operation/schedule.ocpp.json", params={"cid": cid, "date": day}).json()
            assert ocpp.validate(body) == []
            sent = [p["limit"] for p in body["csChargingProfiles"]["chargingSchedule"]["chargingSchedulePeriod"]]
            issued = env[i, h0:h0 + 24].astype(float) * 1000.0
            assert all(s <= w + 1e-6 and w - s < 0.1 + 1e-6 for s, w in zip(sent, issued))
