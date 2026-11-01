"""The business case (engine/summaries.business, engine/economics.owner_case): the DSO's value of
deferring the reinforcement, and what connecting now is worth to the house. Pure: no data needed."""

from __future__ import annotations

import numpy as np
import pytest

from conftest import needs_results


def _reinf(lo=150_000.0, hi=210_000.0, k=3_000.0):
    return {"price_level_year": 2022, "transformer_sek": [lo, hi], "with_station_sek": [lo + 400_000, hi + 400_000],
            "envelopes": {"curtailed_sek_per_year": k, "delivered_sek_per_year": 40_000.0, "price_years": [2024, 2025]}}


def test_the_deferral_net_matches_the_closed_form():
    from kapacitet.engine.summaries import business

    r, cost, k = 0.0453, 150_000.0, 3_000.0
    out = business(_reinf(lo=cost, k=k), 4.53, horizon=4)
    for p in out["curves"]["low"]:
        t = p["years"]
        assert p["net_sek"] == pytest.approx((1 - (1 + r) ** -t) * (cost - k / r), rel=1e-12, abs=1e-6)
        # the same as summing each year's curtailment against the cost paid later
        assert p["curtailment_sek"] == pytest.approx(sum(k / (1 + r) ** y for y in range(1, t + 1)), rel=1e-12, abs=1e-6)
    assert out["curves"]["low"][0]["net_sek"] == 0.0
    assert out["at_horizon"]["transformer"][0] == out["curves"]["low"][4]
    assert out["perpetual_curtailment_sek"] == pytest.approx(k / r)
    # a reinforcement worth less than the curtailment forever is never worth deferring
    cheap = business(_reinf(lo=k / r / 2, k=k), 4.53, horizon=4)
    assert all(p["net_sek"] <= 0 for p in cheap["curves"]["low"])


def test_a_real_rate_goes_with_real_costs():
    from kapacitet.engine.summaries import annuity, business

    out = business(_reinf(), 4.53, horizon=4)
    assert out["rate_basis"] == "real" and out["price_level_year"] == 2022
    assert annuity(0.0, 7) == 7.0                     # no discounting: the plain sum
    assert annuity(0.05, 1) == pytest.approx(1 / 1.05)


def _result(year=2025):
    """A saved answer's economics and reference-year profile: a July export and a January one."""
    from kapacitet.timeindex import year_index

    idx = year_index(year)
    desired = np.zeros(len(idx))
    local = idx.tz_convert("Europe/Stockholm")
    july = np.flatnonzero((local.month == 7) & (local.hour == 12))[:10]
    jan = np.flatnonzero((local.month == 1) & (local.dayofweek == 2) & (local.hour == 12))[:2]
    desired[july] = 5.0
    desired[jan] = 5.0
    rows = [{"year": 2024, "self_use_kwh": 2000.0, "self_use_spot_sek": 1000.0, "export_kwh": 3000.0, "export_sek": 1500.0,
             "curtailed_kwh": 100.0, "curtailed_sek_net": 40.0},
            {"year": 2025, "self_use_kwh": 2200.0, "self_use_spot_sek": 1200.0, "export_kwh": 3200.0, "export_sek": 1700.0,
             "curtailed_kwh": 140.0, "curtailed_sek_net": 60.0}]
    return {"economics": {"by_year": rows}, "profiles": {"year": year, "desired_kw": desired.tolist(),
                                                         "curtailed_kw": np.zeros(len(idx)).tolist()}}


def test_the_owner_components_add_up():
    from kapacitet.config import A
    from kapacitet.engine.economics import owner_case

    out = owner_case(_result(), years=4, rate_pct=3.0, start_year=2027)
    vat, tax, fee = A("vat_rate"), A("energy_tax_sek_kwh"), A("grid_transfer_fee_sek_kwh")
    py = out["per_year"]
    assert py["self_use_sek"] == pytest.approx((1100.0 + 2100.0 * (tax + fee)) * (1 + vat))
    assert py["export_sek"] == pytest.approx(1600.0 + 3100.0 * out["feed_in_sek_kwh"])
    assert py["curtailed_sek"] == pytest.approx(50.0 + 120.0 * out["feed_in_sek_kwh"])
    yearly = py["self_use_sek"] + py["export_sek"]
    assert out["undiscounted_sek"] == pytest.approx(4 * yearly)
    assert out["connect_now_sek"] == pytest.approx(sum(yearly / 1.03 ** t for t in range(1, 5)))
    assert out["tax_reduction"]["years_applied"] == []


def test_the_tax_reduction_applies_only_in_its_years():
    from kapacitet.config import A
    from kapacitet.engine.economics import owner_case, tax_reduction_sek

    last = int(A("micro_tax_reduction_last_year"))
    assert tax_reduction_sek(1000.0, last) == pytest.approx(1000.0 * A("micro_tax_reduction_sek_kwh"))
    assert tax_reduction_sek(1000.0, last + 1) == 0.0
    assert tax_reduction_sek(10 * A("micro_tax_reduction_cap_kwh"), last) == pytest.approx(
        A("micro_tax_reduction_cap_kwh") * A("micro_tax_reduction_sek_kwh"))
    out = owner_case(_result(), years=4, rate_pct=3.0, start_year=last - 1)
    assert out["tax_reduction"]["years_applied"] == [last - 1, last]


def test_the_feed_in_rate_weights_peak_load_time():
    from kapacitet.config import A
    from kapacitet.engine.economics import feed_in_rate
    from kapacitet.timeindex import year_index

    idx = year_index(2025)
    local = idx.tz_convert("Europe/Stockholm")
    only_july = np.where((local.month == 7) & (local.hour == 12), 1.0, 0.0)
    only_winter_weekday = np.where((local.month == 1) & (local.dayofweek < 5) & (local.hour == 12), 1.0, 0.0)
    assert feed_in_rate(2025, only_july) == (pytest.approx(A("feed_in_offpeak_sek_kwh")), 0.0)
    rate, share = feed_in_rate(2025, only_winter_weekday)
    assert share == pytest.approx(1.0) and rate == pytest.approx(A("feed_in_peak_sek_kwh"))


@needs_results
def test_the_api_serves_the_dsos_case_on_its_own_horizon(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from kapacitet import dso
    from kapacitet.api.main import app
    from kapacitet.config import A

    monkeypatch.setattr(dso, "path", lambda: tmp_path / "settings.json")
    with TestClient(app) as c:
        b = c.get("/api/v1/business").json()["data"]
        assert b["horizon_years"] == A("regulatory_period_years") and b["rate_pct"] == A("ei_wacc_real_pct")
        lo, hi = b["at_horizon"]["transformer"]
        assert lo["net_sek"] <= hi["net_sek"]
        assert len(b["curves"]["low"]) == 21
        assert c.get("/api/v1/business", params={"years": 10}).json()["data"]["horizon_years"] == 10
