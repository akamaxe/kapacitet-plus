"""The controls Operation's Settings section draws come from the API, never typed in the interface:
GET /settings gives every setting's kind, default and, for a number, the bounds, step and unit. Here
those bounds are compared with the pydantic model's own (DsoSettings, and the change PUT /settings
validates, DsoSettingsChange), so a bound changed in one place and not the other fails. Also: when
the settings were last changed, and under which role, from the latest seal of a change.

Runs anywhere: the settings and the seals are in the test's own folder.
"""

from __future__ import annotations

import pytest
from annotated_types import Ge, Le

from kapacitet import actor, dso


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from kapacitet.api.main import app

    monkeypatch.delenv("KAPACITET_DATABASE_URL", raising=False)
    monkeypatch.setattr(dso, "path", lambda: tmp_path / "requests" / "settings.json")
    with TestClient(app) as c:
        yield c


def _bounds(model, name: str) -> tuple[float | None, float | None]:
    lo = hi = None
    for m in model.model_fields[name].metadata:
        if isinstance(m, Ge):
            lo = m.ge
        if isinstance(m, Le):
            hi = m.le
    return lo, hi


NUMERIC = [n for n, f in dso.DsoSettings.model_fields.items() if dso._kind(f.annotation)[0] in ("number", "integer")]


def test_the_numeric_settings_are_the_ones_the_screen_expects():
    # the six without another screen, and the import window, which has one
    assert set(NUMERIC) == {"agreed_level_pct", "auto_curtailment_mean_pct", "auto_curtailment_p90_pct",
                            "auto_ev_unserved_pct", "deferral_years", "redispatch_compensation_sek",
                            "import_window_days"}


@pytest.mark.parametrize("name", NUMERIC)
def test_the_bounds_the_api_gives_are_the_models(client, name):
    spec = client.get("/api/v1/settings").json()["schema"][name]
    lo, hi = _bounds(dso.DsoSettings, name)
    assert (spec["min"], spec["max"]) == (lo, hi)
    # the change PUT /settings validates takes the same bounds, so a value the control allows is accepted
    assert _bounds(dso.DsoSettingsChange, name) == (lo, hi)
    assert spec["step"] > 0 and spec["unit"]
    assert spec["kind"] == ("integer" if dso.DsoSettings.model_fields[name].annotation is int else "number")
    # the bounds hold: the lowest and the highest are accepted, one step past either is refused
    assert client.put("/api/v1/settings", json={name: lo}).status_code == 200
    assert client.put("/api/v1/settings", json={name: hi}).status_code == 200
    assert client.put("/api/v1/settings", json={name: hi + spec["step"]}).status_code == 422
    assert client.put("/api/v1/settings", json={name: lo - spec["step"]}).status_code == 422


def test_every_setting_has_a_control_and_its_default(client):
    reply = client.get("/api/v1/settings").json()
    schema, settings = reply["schema"], reply["settings"]
    assert set(schema) == set(settings) == set(dso.DsoSettings.model_fields)
    fresh = dso.DsoSettings().model_dump(mode="json")
    for name, spec in schema.items():
        assert spec["default"] == fresh[name], name
    assert schema["market_result_local"]["kind"] == "time"
    assert schema["inbox_baseline"] == {**schema["inbox_baseline"], "kind": "choice", "options": ["static", "doe"]}
    assert schema["auto_curtailment_mean_pct"]["nullable"] is True
    assert schema["agreed_level_pct"]["nullable"] is False


def test_the_last_change_says_when_and_under_which_role(client):
    assert client.get("/api/v1/settings").json()["changed"] is None
    client.put("/api/v1/settings", json={"deferral_years": 6})
    changed = client.get("/api/v1/settings").json()["changed"]
    assert changed["at"] and changed["role"] is None     # sign-in off: no role
    with actor.acting_as({"sub": "s-1", "name": "Ada", "role": "admin"}):
        dso.update(dso.DsoSettingsChange(deferral_years=7))
    assert client.get("/api/v1/settings").json()["changed"]["role"] == "admin"


def test_a_null_threshold_follows_the_agreed_level_again_and_other_nulls_change_nothing(client):
    s = client.put("/api/v1/settings", json={"auto_curtailment_mean_pct": 2.5, "deferral_years": 5}).json()["settings"]
    assert (s["auto_curtailment_mean_pct"], s["deferral_years"]) == (2.5, 5)
    assert dso.thresholds().curtailment_mean_pct == 2.5
    s = client.put("/api/v1/settings", json={"auto_curtailment_mean_pct": None, "deferral_years": None}).json()["settings"]
    assert s["auto_curtailment_mean_pct"] is None and s["deferral_years"] == 5
    assert dso.thresholds().curtailment_mean_pct == s["agreed_level_pct"]


def test_putting_the_whole_get_reply_back_changes_nothing(client):
    client.put("/api/v1/settings", json={"auto_curtailment_mean_pct": 2.5, "deferral_years": 6})
    before = client.get("/api/v1/settings").json()
    body = {k: v for k, v in before["settings"].items() if k in dso.DsoSettingsChange.model_fields}
    r = client.put("/api/v1/settings", json=body)
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/settings").json()["changed"] == before["changed"]
