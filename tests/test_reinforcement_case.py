"""The reinforcement comparison prices one unit (engine/summaries.reinforcement)."""

from __future__ import annotations

import pytest

from kapacitet.engine import summaries

COST = {"reinforce_trafo_500_sek": 100.0, "reinforce_trafo_800_sek": 200.0, "reinforce_station_800_sek": 300.0,
        "reinforce_urban_supplement_sek": 50.0, "reinforce_price_level_year": 2024}
OVER = {"budget": {"curtailed_sek": 10.0, "kw_ac": 500.0, "kw_ac_nonfirm": 100.0, "delivered_sek": 900.0},
        "price_years": [2025], "order": 0}


def _tornado(levels: list[str]) -> dict:
    case = {"static_kw": 1.0, "timeseries_kw": 2.0, "doe_kw": 3.0}
    return {"order": 0, "base": dict(case),
            "cases": [{"key": "trafo", "label": "Transformer rating", "level": lv, **case} for lv in levels]}


def test_the_comparison_is_made_only_for_the_unit_the_costs_are_for():
    """Ei's norm values on file bracket a 630 kVA unit. A network whose sensitivity has no 0.63 MVA
    case (its transformer is not the 0.4 MVA one) has no reinforcement priced: the comparison says
    so instead of stopping on a missing case, or pricing a unit nobody costed."""
    made = summaries.reinforcement(_tornado(["0.25 MVA", "0.63 MVA"]), OVER, COST)
    assert made["bigger"]["level"] == "0.63 MVA" and made["transformer_sek"] == [100.0, 200.0]
    for levels in (["0.25 MVA"], ["0.4 MVA"], []):
        with pytest.raises(summaries.NoReinforcementCase):
            summaries.reinforcement(_tornado(levels), OVER, COST)


def test_the_api_says_why_there_is_no_comparison(tmp_path, monkeypatch):
    import json

    from fastapi.testclient import TestClient

    from kapacitet.api import main

    over = tmp_path / "overinstallation.json"
    tornado = tmp_path / "tornado.json"
    tornado.write_text(json.dumps(_tornado(["0.25 MVA"])), encoding="utf-8")
    over.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(main, "result", lambda name: json.loads((tmp_path / name).read_text(encoding="utf-8")))
    monkeypatch.setattr(main, "result_path", lambda name: tmp_path / name)
    monkeypatch.setattr("kapacitet.engine.summaries.overinstallation_summary", lambda doc: OVER)
    with TestClient(main.app) as c:
        r = c.get("/api/v1/reinforcement")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "no_reinforcement_case"
