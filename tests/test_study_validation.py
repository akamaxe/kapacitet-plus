"""study/validation.py: a station's model against the DSO's own acceptance criteria.

The comparison with measurements has its own tests (test_station_capacity.py). Here: what the
verdict says of a comparison, criterion by criterion, and that only `within` is ever "validated".
"""

from __future__ import annotations

import pytest

from kapacitet.study.config import StudyConfigError, Validation, parse
from kapacitet.study.validation import STATUSES, SV, verdict


def stats(p95: float | None, n: int = 100) -> dict:
    return {"n": n if p95 is not None else 0, "bias": 0.0, "mae": p95, "rmse": p95, "p95": p95, "max": p95}


def doc(measured: dict | None = None, band: dict | None = None, metered: int = 20) -> dict:
    """validation_measured.json with a replay at the measured MV voltage, at the band's ends, or neither."""
    bases = {}
    if measured is not None:
        bases["measured"] = {"hours": 100, "voltage_pu": stats(measured.get("v")), "transformer_pct": stats(measured.get("t"))}
    for name, b in (band or {}).items():
        bases[name] = {"hours": 100, "voltage_pu": stats(b.get("v")), "transformer_pct": stats(b.get("t"))}
    if not bases:
        return {"measured": {"status": "no meter readings"}}
    return {"measured": {"hours_complete": 100, "mv": "measured" if measured is not None else "band",
                         "bases": bases, "metered_customers": metered}}


def criteria(**kw) -> Validation:
    return Validation(source="nätbolagets valideringsbeslut 2026-11", **kw)


def test_only_a_station_inside_every_criterion_is_validated():
    assert set(SV) == set(STATUSES)
    assert verdict(None, criteria(voltage_p95_pu=0.01), 40)["status"] == "not_validated"
    assert verdict(doc(), criteria(voltage_p95_pu=0.01), 40)["status"] == "not_measured"
    # compared with measurements, and nothing to judge it by: never "validated"
    v = verdict(doc({"v": 0.004, "t": 2.0}), Validation(), 40)
    assert v == {"status": "no_criterion", "checks": []}
    inside = verdict(doc({"v": 0.004, "t": 2.0}), criteria(voltage_p95_pu=0.005, transformer_p95_pct=3.0), 40)
    assert inside["status"] == "within" and [c["ok"] for c in inside["checks"]] == [True, True]
    outside = verdict(doc({"v": 0.006, "t": 2.0}), criteria(voltage_p95_pu=0.005, transformer_p95_pct=3.0), 40)
    assert outside["status"] == "outside"
    assert [(c["criterion"], c["ok"]) for c in outside["checks"]] == [("voltage_p95_pu", False), ("transformer_p95_pct", True)]


def test_the_voltage_is_judged_only_at_the_measured_mv_voltage():
    """At the band's two ends the voltage error is the width of the band, not the model's."""
    band = {"band_low": {"v": 0.03, "t": 2.0}, "band_high": {"v": 0.05, "t": 4.0}}
    v = verdict(doc(band=band), criteria(voltage_p95_pu=0.01), 40)
    assert v["status"] == "not_measured" and v["checks"][0]["value"] is None and v["checks"][0]["ok"] is None
    # the transformer's loading is judged at the end where its error is larger
    t = verdict(doc(band=band), criteria(transformer_p95_pct=3.0), 40)
    assert t["status"] == "outside" and t["checks"][0]["value"] == 4.0 and t["checks"][0]["basis"] == "band_high"
    assert verdict(doc(band=band), criteria(transformer_p95_pct=4.0), 40)["status"] == "within"
    # with a measured MV voltage, that replay is the one judged
    both = verdict(doc({"v": 0.004, "t": 1.0}, band), criteria(transformer_p95_pct=3.0), 40)
    assert both["status"] == "within" and both["checks"][0]["basis"] == "measured"


def test_the_share_of_metered_customers_is_a_criterion_of_its_own():
    c = criteria(min_metered_share=0.8)
    assert verdict(doc({"v": 0.004, "t": 1.0}, metered=32), c, 40)["status"] == "within"
    low = verdict(doc({"v": 0.004, "t": 1.0}, metered=20), c, 40)
    assert low["status"] == "outside" and low["checks"][0]["value"] == 0.5
    assert verdict(doc({"v": 0.004, "t": 1.0}, metered=20), c, None)["status"] == "not_measured"


def test_a_criterion_that_fails_outweighs_one_that_cannot_be_judged():
    c = criteria(voltage_p95_pu=0.005, transformer_p95_pct=3.0)
    # no transformer meter (not judged), and the voltage outside: outside
    assert verdict(doc({"v": 0.02, "t": None}), c, 40)["status"] == "outside"
    # no transformer meter, the voltage inside: the station is not measured for what the DSO asks
    assert verdict(doc({"v": 0.004, "t": None}), c, 40)["status"] == "not_measured"


def test_a_tolerance_needs_its_source_and_none_has_a_default():
    assert not Validation().entered and parse({}).validation == Validation()
    with pytest.raises(StudyConfigError, match="source"):
        parse({"validation": {"voltage_p95_pu": 0.01}})
    cfg = parse({"validation": {"voltage_p95_pu": 0.01, "source": "beslut 2026-11-12"}})
    assert cfg.validation.entered and cfg.validation.transformer_p95_pct is None
    with pytest.raises(StudyConfigError):
        parse({"validation": {"voltage_p95_pu": 0.0, "source": "x"}})          # a tolerance of nothing
    # the criteria rebuild no station: they are judged after the computation
    assert "validation" not in cfg.computation() and "disclosure" not in cfg.computation()
