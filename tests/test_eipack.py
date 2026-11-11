"""The evidence pack for Ei (engine/eipack.py, config/ei_conditions.toml). Pure: no data needed."""

from __future__ import annotations

import pytest

from conftest import needs_operation


def _index(unneeded=0, limited=148):
    combo = {"hours_limited": limited, "activations_unnecessary_hours": unneeded}
    return {"year": 2025, "necessary_hours": 148, "combos": {"foresight": {"maxmin": combo}, "dayahead": {"maxmin": combo}}}


def _pack(basis="dayahead", unneeded=998, agreements=None, **settings):
    from kapacitet.dso import DsoSettings
    from kapacitet.engine.eipack import assess

    agreements = agreements if agreements is not None else [{"customer_id": 1, "static_approved": False}]
    return assess(basis, "maxmin", DsoSettings(**settings), _index(unneeded, 1146 if unneeded else 148), agreements,
                  {"hours_with_curtailment": 148, "curtailed_kwh": 7261.3})


def _state(pack, key):
    return next(c for c in pack["conditions"] if c["key"] == key)


def test_every_condition_has_its_source_in_both_languages():
    from kapacitet.engine.eipack import conditions

    cfg = conditions()
    assert cfg["source"]["url"].startswith("https://ei.se/")
    keys = [c["key"] for c in cfg["condition"]]
    assert len(keys) == len(set(keys)) >= 6
    for c in cfg["condition"]:
        assert c["evidence"] in ("model", "declaration"), c["key"]
        assert c["source"] and all(c[f] for f in ("label", "label_sv")), c["key"]
        assert not [f for f in c if f.endswith("_es")], f"{c['key']}: the interface has no Spanish"


def test_a_source_that_cites_the_repealed_law_also_cites_the_one_that_replaces_it():
    """Ellagen is repealed on 1 January 2027: its paragraphs are cited with the new law's, and the
    pack carries which law replaced which and when the pairs were read."""
    from kapacitet.engine.eipack import conditions

    cfg = conditions()
    law = cfg["law"]
    assert law["repealed"] == "ellagen (1997:857)" and law["repealed_on"] == "2027-01-01"
    assert law["replaced_by"] == "elmarknadslagen (2026:1281)" and "2026:1282" in law["repealed_by"]
    assert law["checked"] and all(u.startswith("https://data.riksdagen.se/") for u in law["urls"])
    by_key = {c["key"]: c["source"] for c in cfg["condition"]}
    cited = [k for k, src in by_key.items() if "ellagen" in src]
    assert cited == ["capacity_shortage", "method_approved"]
    for k in cited:
        assert f"från {law['repealed_on']}: elmarknadslagen" in by_key[k], k
    assert "ellagen 4 kap. 1 och 2 §§" in by_key["capacity_shortage"] and "6 kap. 1 och 3 §§" in by_key["capacity_shortage"]
    assert "ellagen 4 kap. 46 §" in by_key["method_approved"] and "6 kap. 19 och 20 §§" in by_key["method_approved"]
    assert _pack()["law"] == law


def test_an_empty_statement_is_never_met_and_waits_for_the_dso_instead_of_failing():
    blank = _pack(market_alternative_statement="   ", method_approval_reference="", review_statement="")
    for key in ("market_first", "method_approved", "review_when_changed"):
        assert _state(blank, key)["state"] == "awaiting"
    assert blank["counts"]["declared"] == 0 and blank["counts"]["awaiting"] == 3
    assert sum(blank["counts"].values()) == len(blank["conditions"])
    stated = _pack(market_alternative_statement="Sthlmflex was asked; no bids for this area.",
                   method_approval_reference="Ei 2026-100000")
    assert _state(stated, "market_first")["state"] == "declared"
    assert _state(stated, "method_approved")["fact"]["params"]["text"] == "Ei 2026-100000"


def test_after_the_market_does_not_apply_to_perfect_foresight_and_the_day_ahead_says_its_unneeded_hours():
    foresight = _pack("foresight", unneeded=0)
    assert _state(foresight, "after_market_result")["state"] == "not_applicable"
    assert _state(foresight, "only_when_overloaded")["state"] == "evidence"
    ahead = _pack("dayahead", unneeded=998)
    assert _state(ahead, "after_market_result")["state"] == "not_met"
    only = _state(ahead, "only_when_overloaded")
    assert only["state"] == "not_met" and only["fact"]["params"] == {"limited": 1146, "needed": 148, "unneeded": 998}


def test_the_day_ahead_pack_never_cites_the_foresight_business_case():
    """The deferral value is priced on the perfect-foresight deployment: the day-ahead pack states
    its terms without it (terms_plain), so no kSEK figure of the other basis reaches it."""
    from kapacitet.dso import DsoSettings
    from kapacitet.engine.eipack import assess

    business = {"at_horizon": {"transformer": [{"net_sek": 15_000.0}, {"net_sek": 24_000.0}]}}
    args = (DsoSettings(), _index(998, 1146), [{"customer_id": 1, "static_approved": False}], None, business)
    ahead = _state(assess("dayahead", "maxmin", *args), "terms_how_when")["fact"]
    assert ahead["code"] == "terms_plain" and not {"lo", "hi"} & set(ahead["params"])
    foresight = _state(assess("foresight", "maxmin", *args), "terms_how_when")["fact"]
    assert foresight["code"] == "terms" and foresight["params"]["lo"] == 15_000.0


@pytest.mark.parametrize("rejected, state", [(True, "evidence"), (False, "not_met")])
def test_a_shortage_is_shown_only_when_the_static_study_rejected_every_agreement(rejected, state):
    agreements = [{"customer_id": 1, "static_approved": False}, {"customer_id": 2, "static_approved": not rejected}]
    assert _state(_pack(agreements=agreements), "capacity_shortage")["state"] == state


@needs_operation
def test_the_api_serves_the_pack_and_seals_what_it_exports(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from kapacitet import dso, seals
    from kapacitet.api.main import app

    monkeypatch.setattr(dso, "path", lambda: tmp_path / "settings.json")
    with TestClient(app) as c:
        shown = c.get("/api/v1/eipack", params={"basis": "dayahead"}).json()
        assert shown["basis"] == "dayahead" and len(shown["conditions"]) >= 6
        assert not seals.sealed_of_kind("eipack")                 # reading it leaves no record
        assert "seal" not in c.get("/api/v1/eipack", params={"basis": "dayahead", "export": "true"}).json()
        assert not seals.sealed_of_kind("eipack")                 # not even when a GET asks for the export
        sent = c.post("/api/v1/eipack/export", json={"basis": "dayahead"})
        assert "attachment" in sent.headers["content-disposition"] and sent.json()["seal"]["digest"]
        assert sent.headers["x-seal-digest"] == sent.json()["seal"]["digest"]   # for the page's receipt
        assert len(seals.sealed_of_kind("eipack")) == 1
        # the same Idempotency-Key exports once: a retry gets the pack and the seal of the first
        key = {"Idempotency-Key": "ei-export-0001"}
        first = c.post("/api/v1/eipack/export", json={"basis": "dayahead"}, headers=key).json()
        again = c.post("/api/v1/eipack/export", json={"basis": "dayahead"}, headers=key).json()
        assert first == again and len(seals.sealed_of_kind("eipack")) == 2
