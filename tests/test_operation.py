"""The operation stage (engine/operation.py), the DSO's settings (dso.py) and the resolver that serves
a combination of sharing rule and basis (api/combos.py).

The settings and the resolver's refusals need no data; the rest reads the stage's output.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from conftest import needs_operation


# ---------------------------------------------------------------- settings (no data)

def test_settings_are_validated_written_atomically_and_sealed(tmp_path, monkeypatch):
    from kapacitet import dso, seals

    monkeypatch.setattr(dso, "path", lambda: tmp_path / "settings.json")
    assert dso.load().allocation_policy == "maxmin"                    # the default, nothing saved
    after = dso.update(dso.DsoSettingsChange(allocation_policy="prorata"))
    assert after.allocation_policy == "prorata" and dso.load().allocation_policy == "prorata"
    assert not (tmp_path / "settings.json.tmp").exists()
    sealed = seals.sealed_of_kind("settings")
    assert len(sealed) == 1
    assert sealed[0]["record"]["before"]["allocation_policy"] == "maxmin"
    assert sealed[0]["record"]["after"]["allocation_policy"] == "prorata"
    dso.update(dso.DsoSettingsChange(allocation_policy="prorata"))     # no change, no new seal
    assert len(seals.sealed_of_kind("settings")) == 1
    with pytest.raises(ValueError):
        dso.DsoSettingsChange(allocation_policy="cheapest")
    with pytest.raises(ValueError):
        dso.DsoSettingsChange.model_validate({"allocation_policy": "maxmin", "smuggled": 1})


def test_unknown_or_unverified_combinations_are_refused(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from kapacitet.api import combos

    monkeypatch.setattr(combos, "processed_ro", lambda *parts: tmp_path.joinpath(*parts))
    combos._index.cache_clear()
    with pytest.raises(HTTPException) as e:
        combos.resolve("dayahead", "maxmin")
    assert e.value.status_code == 404 and e.value.detail["code"] == "not_computed"

    folder = tmp_path / "results" / "operation" / "dayahead" / "maxmin"
    folder.mkdir(parents=True)
    (folder / "issued.json").write_text("{}", encoding="utf-8")
    (tmp_path / "results" / "operation" / "index.json").write_text(json.dumps(
        {"combos": {"dayahead": {"maxmin": {"verified": False}}}}), encoding="utf-8")
    with pytest.raises(HTTPException) as e:
        combos.resolve("dayahead", "maxmin")
    assert e.value.status_code == 409 and e.value.detail["code"] == "unverified"
    with pytest.raises(HTTPException) as e:
        combos.resolve("hindsight", "maxmin")
    assert e.value.status_code == 422


# ---------------------------------------------------------------- the stage's output

def _read(*parts):
    from kapacitet.paths import processed_ro

    return processed_ro("results", "operation", *parts)


@needs_operation
def test_every_published_combination_is_verified_over_the_full_year():
    idx = json.loads(_read("index.json").read_text(encoding="utf-8"))
    for basis, rules in idx["combos"].items():
        for rule, info in rules.items():
            issued = json.loads(_read(basis, rule, "issued.json").read_text(encoding="utf-8"))
            assert info["verified"] and issued["verification"]["full_year_violation_hours"] == 0, (basis, rule)


@needs_operation
def test_every_published_combination_converged_in_every_hour():
    idx = json.loads(_read("index.json").read_text(encoding="utf-8"))
    for basis, rules in idx["combos"].items():
        for rule in rules:
            ver = json.loads(_read(basis, rule, "issued.json").read_text(encoding="utf-8"))["verification"]
            if "pf_nonconverged_hours" not in ver:
                pytest.skip("not published yet; run: python -m kapacitet compute --stage extra --only operation")
            assert ver["pf_nonconverged_hours"] == 0, (basis, rule)


@needs_operation
def test_foresight_maxmin_reproduces_the_published_envelopes_and_filing():
    from kapacitet.paths import processed_ro

    legacy = json.loads(processed_ro("results", "compliance.json").read_text(encoding="utf-8"))
    combo = json.loads(_read("foresight", "maxmin", "compliance.json").read_text(encoding="utf-8"))
    for key in ("conditional_agreements", "hours_with_curtailment", "verified_violations"):
        assert combo[key] == legacy[key], key
    assert combo["curtailed_kwh"] == pytest.approx(legacy["curtailed_kwh"], abs=0.5)
    old = np.load(processed_ro("ui", "envelopes_doe.npy")).astype(float)
    new = np.load(processed_ro("ui", "operation", "foresight", "maxmin", "envelopes.npy")).astype(float)
    assert old.shape == new.shape and np.allclose(old, new, atol=0.05)


@needs_operation
def test_the_day_ahead_trail_marks_the_activations_nobody_needed():
    trail = pd.read_csv(_read("dayahead", "maxmin", "audit_trail.csv"))
    report = json.loads(_read("dayahead", "maxmin", "compliance.json").read_text(encoding="utf-8"))
    assert (~trail["necessary"]).any()
    assert report["activations_unnecessary_hours"] > 0
    hours = trail.groupby("hour")["necessary"].first()
    assert report["activations_necessary_hours"] == int(hours.sum())
    assert report["activations_unnecessary_hours"] == int((~hours).sum())
    # with perfect foresight every limited hour was a necessary one
    fore = pd.read_csv(_read("foresight", "maxmin", "audit_trail.csv"))
    assert fore["necessary"].all()


@needs_operation
def test_every_combination_trail_adds_up_to_its_filing():
    idx = json.loads(_read("index.json").read_text(encoding="utf-8"))
    for basis, rules in idx["combos"].items():
        for rule in rules:
            trail = pd.read_csv(_read(basis, rule, "audit_trail.csv"))
            report = json.loads(_read(basis, rule, "compliance.json").read_text(encoding="utf-8"))
            assert trail["curtailed_kwh"].sum() == pytest.approx(report["curtailed_kwh"], rel=1e-3, abs=0.5)
            assert trail["hour"].nunique() == report["hours_with_curtailment"]


@needs_operation
def test_the_default_control_is_below_every_issued_limit():
    from kapacitet.paths import processed_ro

    for basis in ("foresight", "dayahead"):
        issued = json.loads(_read(basis, "maxmin", "issued.json").read_text(encoding="utf-8"))
        env = np.load(processed_ro("ui", "operation", basis, "maxmin", "envelopes.npy")).astype(float)
        default = np.asarray(issued["default_control"]["kw"], dtype=float)
        assert (default <= env.min(axis=1) + 1e-6).all()


@needs_operation
def test_every_agreement_is_beyond_the_static_limit():
    agree = json.loads(_read("agreements.json").read_text(encoding="utf-8"))["agreements"]
    idx = json.loads(_read("index.json").read_text(encoding="utf-8"))
    assert len(agree) == len(idx["nonfirm_cids"])
    assert not any(a["static_approved"] for a in agree)


@needs_operation
def test_the_api_serves_a_rule_and_a_basis():
    from fastapi.testclient import TestClient

    from kapacitet.api.main import app

    with TestClient(app) as c:
        default = c.get("/api/v1/compliance").json()["data"]
        ahead = c.get("/api/v1/compliance", params={"basis": "dayahead", "policy": "prorata"}).json()["data"]
        assert default["basis"] == "foresight" and ahead["basis"] == "dayahead" and ahead["policy"] == "prorata"
        assert ahead["curtailed_kwh"] > default["curtailed_kwh"]
        rows = c.get("/api/v1/audit", params={"basis": "dayahead", "limit": 5}).json()["rows"]
        assert all(isinstance(r["necessary"], bool) for r in rows)
