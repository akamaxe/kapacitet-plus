"""What a study's publication may show (study/disclosure.py): cells of at least k customers, merged
upward from the study's level, with hidden stations left out. No data needed."""

from __future__ import annotations

import random

import pytest

from kapacitet import geo
from kapacitet.study.config import Disclosure
from kapacitet.study.disclosure import assign, lowest, report

E0, N0 = 674000, 6580000          # a SWEREF 99 TM square in Stockholm, on the 1000 m grid


def at(sid: str, de: float, dn: float, customers: int, zone: str | None = None, **extra) -> dict:
    lon, lat = geo.to_wgs84(E0 + de, N0 + dn)
    return {"station_id": sid, "customers": customers, "lon": lon, "lat": lat, "zone": zone, **extra}


def cfg(**kw) -> Disclosure:
    return Disclosure(**kw)


def test_a_station_with_k_customers_stays_and_small_ones_merge_into_their_square():
    rows = [at("big", 100, 100, 40), at("a", 200, 200, 3), at("b", 800, 900, 2)]
    out = assign(rows, cfg(level="station"), "nb")
    assert out["stations"]["big"]["cell"] == "big" and not out["stations"]["big"]["merged"]
    cell = out["stations"]["a"]["cell"]
    assert cell == f"R1000_{E0}_{N0}" and out["stations"]["b"]["cell"] == cell
    c = out["cells"][cell]
    # the merged cell holds only the stations that needed it, not the big one
    assert c["stations"] == ["a", "b"] and c["customers"] == 5 and not c["suppressed"]


def test_squares_double_until_they_hold_k_and_nest():
    rows = [at("a", 500, 500, 3), at("b", 1500, 500, 3)]          # neighbouring 1000 m squares
    out = assign(rows, cfg(level="grid", grid_m=1000), "nb")
    cell = out["stations"]["a"]["cell"]
    assert cell == out["stations"]["b"]["cell"] == f"R2000_{E0}_{N0}"
    poly = out["cells"][cell]["geometry"]
    assert poly["type"] == "Polygon"
    corners = [geo.to_sweref(*p) for p in poly["coordinates"][0][:4]]
    assert corners[0] == pytest.approx((E0, N0), abs=0.5) and corners[2] == pytest.approx((E0 + 2000, N0 + 2000), abs=0.5)


def test_without_a_position_a_station_goes_to_its_zone_and_then_the_study():
    rows = [{"station_id": "x", "customers": 2, "lon": None, "lat": None, "zone": "Norr"},
            {"station_id": "y", "customers": 4, "lon": None, "lat": None, "zone": "Norr"},
            {"station_id": "z", "customers": 1, "lon": None, "lat": None, "zone": "Syd"},
            at("w", 100, 100, 4, zone="Syd")]
    out = assign(rows, cfg(level="station"), "nb")
    assert out["stations"]["x"]["cell"] == out["stations"]["y"]["cell"] == "Z_Norr"
    # z and w (4 customers in its square, then in zone Syd) reach k only together in the zone
    assert out["stations"]["z"]["cell"] == out["stations"]["w"]["cell"] == "Z_Syd"
    assert out["cells"]["Z_Norr"]["geometry"] is None


def test_too_few_customers_in_the_whole_study_are_suppressed():
    rows = [at("a", 100, 100, 2), at("b", 300, 300, 1)]
    out = assign(rows, cfg(level="zone", min_customers=5), "nb")
    assert out["stations"]["a"]["cell"] == "ALL" and out["cells"]["ALL"]["suppressed"]
    assert all(r["suppressed"] for r in report(rows, out))


def test_hidden_stations_are_left_out_and_never_count_towards_k():
    rows = [at("a", 100, 100, 3), at("secret", 150, 150, 30, hidden=True), at("c", 200, 200, 3)]
    out = assign(rows, cfg(level="station", hidden=["c"]), "nb")
    assert out["stations"]["secret"] == {"cell": None, "hidden_by": "stations.csv"}
    assert out["stations"]["c"]["hidden_by"] == "study.toml"
    assert out["cells"][out["stations"]["a"]["cell"]]["customers"] == 3 and out["cells"]["ALL"]["suppressed"]
    out = assign(rows[:1] + rows[2:], cfg(level="station"), "nb", fixes={"a": {"hidden": {"value": True}}})
    assert out["stations"]["a"]["hidden_by"] == "correction"
    assert not any("secret" in c["stations"] or "a" in c["stations"] for c in out["cells"].values())


def test_every_published_cell_holds_at_least_k_customers():
    rng = random.Random(7)
    for trial in range(30):
        rows = [at(f"s{i}", rng.uniform(-20000, 20000), rng.uniform(-20000, 20000), rng.randint(0, 9),
                   zone=rng.choice(["A", "B", None])) for i in range(rng.randint(1, 40))]
        k = rng.randint(1, 12)
        for level in ("station", "grid", "zone"):
            out = assign(rows, cfg(level=level, min_customers=k, grid_m=500), "nb")
            for c in out["cells"].values():
                assert c["suppressed"] or c["customers"] >= k
                assert c["suppressed"] == (c["customers"] < k)
            assert sorted(s for c in out["cells"].values() for s in c["stations"]) == sorted(r["station_id"] for r in rows)


def test_a_cell_shows_the_lowest_figure_and_none_when_a_station_has_none():
    assert lowest([120.0, 30.0, 50.0]) == 30.0
    assert lowest([120.0, None]) is None and lowest([]) is None
