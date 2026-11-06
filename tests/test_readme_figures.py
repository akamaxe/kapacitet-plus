"""The README's figures are read back from the published results, at the README's own rounding.

If a recompute moves a number, this fails until the README says the new one.
"""

from __future__ import annotations

import json
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import needs_results
from kapacitet.paths import processed

pytestmark = needs_results

# the README separates thousands with a space; read every kind of space as a plain one
TEXT = re.sub(r"[  ]", " ", (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8"))
API = "/api/v1"


def num(s: str) -> float:
    return float(s.replace(" ", "").replace(",", "."))


def find(pattern: str) -> tuple[str, ...]:
    m = re.search(pattern, TEXT, re.S)
    assert m, f"README no longer contains: {pattern}"
    return m.groups()


def rounded(x: float, digits: int = 0) -> float:
    return float(Decimal(str(x)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


@pytest.fixture(scope="module")
def client():
    from kapacitet.api.main import app

    with TestClient(app) as c:
        yield c


def test_the_headline_table_matches_the_hosting_summary(results):
    m = results("hosting_summary")["measures"]
    fs = results("foresight")
    rows = {
        r"Static study, worst case \| \*\*(\d+) kW AC\*\* \(P10.P90 (\d+).(\d+)\) \| (\d+)": m["static"],
        r"fixed export cap at 3 % curtailment \| (\d+) kW \| (\d+)": m["fixed_cap"],
        r"no control \(every hour of 11 years within limits\) \| (\d+) kW \| (\d+)": m["timeseries"],
        r"3 % per installation \| (\d+) kW \((\d+).(\d+)\) \| (\d+)": m["doe_installation"],
        r"3 % aggregate\*\* \| \*\*(\d+) kW\*\* \((\d+).(\d+)\) \| (\d+)": m["doe"],
    }
    for pattern, d in rows.items():
        got = [int(g) for g in find(pattern)]
        want = [rounded(d["kw_ac"]["median"])]
        if len(got) == 4:
            want += [rounded(d["kw_ac"]["p10"]), rounded(d["kw_ac"]["p90"])]
        want.append(rounded(d["adopters"]["median"]))
        assert got == want, pattern
    clim = fs["kw_ac_stats"]["climatological"]
    got = [int(g) for g in find(r"issued the day before, no forecast, 3 % aggregate \| (\d+) kW \((\d+).(\d+)\) \| (\d+)")]
    assert got == [rounded(clim["median"]), rounded(clim["p10"]), rounded(clim["p90"]),
                   rounded(fs["median_adopters"]["climatological"])]
    ratio = m["doe"]["kw_ac"]["median"] / m["static"]["kw_ac"]["median"]
    assert num(find(r"\*\*(\d\.\d)×\*\* the static")[0]) == rounded(ratio, 1)


def test_uncontrolled_and_the_mv_band(results):
    ver = results("showcase")["verification"]
    assert int(find(r"above its rating in \*\*(\d+) hours\*\* of 2025")[0]) == ver["uncontrolled_violation_hours"]
    assert num(find(r"voltages stay at\s+(\d\.\d+) pu")[0]) == rounded(ver["uncontrolled_max_voltage_pu"], 3)
    levels = {lv["vm_pu"]: lv["published"] for lv in results("mv_band")["levels"]}
    low, high = find(r"overload the transformer in (\d+) hours \(to (\d+\.\d) %\)")
    assert int(low) == levels[0.98]["violation_hours"]
    assert num(high) == rounded(levels[0.98]["max_trafo_loading_pct"], 1)
    assert int(find(r"voltage binds in (\d+) hours")[0]) == levels[1.06]["violation_hours"]


def test_the_reinforcement_and_business_figures(client):
    r = client.get(f"{API}/reinforcement").json()["data"]
    assert int(find(r"transformer studied statically \((\d+) kW")[0]) == rounded(r["bigger"]["static_kw"])
    lo, hi, ylo, yhi = find(r"costs (\d+).(\d+) kSEK, as much as (\d+).(\d+) years")
    assert (int(lo), int(hi)) == tuple(rounded(x / 1000) for x in r["transformer_sek"])
    assert (int(ylo), int(yhi)) == tuple(rounded(x) for x in r["years_of_curtailment"]["transformer"])
    b = client.get(f"{API}/business").json()["data"]
    lo, hi = find(r"is worth (\d+).(\d+) kSEK after the curtailment")
    assert [int(lo), int(hi)] == [rounded(x["net_sek"] / 1000) for x in b["at_horizon"]["transformer"]]
    assert num(find(r"Ei's real rate for 2024.2027 \((\d\.\d+) %\)")[0]) == b["rate_pct"]
    assert num(find(r"(\d \d{3}) SEK a year")[0]) == rounded(b["curtailed_sek_per_year"])
    assert int(find(r"costs more than k/r, about (\d+) kSEK")[0]) == rounded(b["perpetual_curtailment_sek"] / 1000)
    a = b["at_rates"]
    assert num(find(r"\((\d\.\d+) % with the\s+proposed method")[0]) == a["ei_wacc_real_pct_2028_prelim"]["rate_pct"]
    assert num(find(r"proposed method, (\d\.\d+) % with the current")[0]) == a["ei_wacc_real_pct_2028_current_method"]["rate_pct"]


def test_fairness_is_free(results):
    pol = results("showcase")["policies"]
    assert num(find(r"same total energy \((\d\.\d+) % in the showcase year\)")[0]) == rounded(pol["maxmin"]["z_pct"], 2)
    lo, hi = find(r"Jain index (\d\.\d+) . (\d\.\d+)")
    jain = [p["jain_index"] for p in pol.values()]
    assert (num(lo), num(hi)) == (rounded(min(jain), 2), rounded(max(jain), 2))


def test_the_scheme_table_and_the_day_ahead_paragraph(results):
    fc = {r["scheme"]: r for r in results("forecast")["rows"]}
    clim = fc["climatological"]
    share, ref, limited, needed = find(
        r"curtails (\d+\.\d) % of the non-firm export instead of (\d\.\d+) %, in (\d \d{3}) hours of which only (\d+)")
    assert num(share) == rounded(clim["curtailed_share_pct"], 1)
    assert num(ref) == rounded(fc["foresight"]["curtailed_share_pct"], 2)
    assert num(limited) == clim["hours_limited"]
    issued = json.loads(processed("results", "operation", "dayahead", "maxmin", "issued.json").read_text("utf-8"))
    assert int(needed) == issued["necessary_hours"]
    for label, key in (("static worst case, no envelope", "static"), ("day ahead, no forecast", "climatological"),
                       ("day ahead, yesterday's weather", "persistence"), (r"perfect foresight \(published\)", "foresight")):
        row = re.search(rf"\| {label} \| ([^|]*) \| \**(\d+)\** \| (\d[\d ]*) kWh \| (\d+\.\d+) \|", TEXT)
        assert row, label
        r = fc[key]
        if key != "static":
            assert num(row.group(1).replace("h", "")) == r["hours_limited"], label
        assert int(row.group(2)) == r["violation_hours"], label
        assert num(row.group(3)) == rounded(r["curtailed_kwh"]), label
        assert num(row.group(4)) == rounded(r["curtailed_share_pct"], 2), label


def test_the_filed_exceedance_and_the_margins(results):
    issued = json.loads(processed("results", "operation", "dayahead", "maxmin", "issued.json").read_text("utf-8"))
    ex = issued["exceedance"]
    pv, load = find(r"exceptions, (\d+) customer-hours of generation above the bound and (\d+) of load")
    assert (int(pv), int(load)) == (ex["pv_customer_hours_over"], ex["load_customer_hours_under"])
    fx = results("foresight")["exceedance"]
    assert fx["pv_aggregate_hours_over"] == 0 and fx["load_aggregate_hours_under"] == 0
    past = find(r"would have gone (\d\.\d) % past it")[0]
    assert num(past) == rounded((fx["pv_worst_aggregate_ratio_unmargined"] - 1) * 100, 1)


def test_the_day_ahead_capacity_search(results):
    fs = results("foresight")
    m = fs["median_kw"]
    got = find(r"carries a median of (\d+) kW, against (\d+) kW for the\s+hourly study with no control at all, "
               r"(\d+) kW static and (\d+) kW with perfect foresight")
    assert [int(g) for g in got] == [rounded(m[k]) for k in ("climatological", "timeseries", "static", "doe")]
    assert fs["margins"] == {"pv_pct": 3.0, "load_pct": 3.0}


def test_the_shadow_season(client):
    d = client.get(f"{API}/operation/shadow").json()["bases"]
    day, fore = d["dayahead"], d["foresight"]
    n, total, med, worst = find(r"fails the second: 0 hours above a limit, but only (\d+) of (\d+) agreements within 3 % "
                                r"\(median (\d+\.\d) %,\s+worst (\d+\.\d) %\)")
    assert (int(n), int(total)) == (day["within_level"], day["agreements"])
    assert (num(med), num(worst)) == (rounded(day["median_year_share_pct"], 1), rounded(day["worst_year_share_pct"], 1))
    assert day["violation_hours"] == 0
    n2, worst2 = find(r"With perfect foresight (\d+) of 43 would be, worst (\d\.\d) %")
    assert (int(n2), num(worst2)) == (fore["within_level"], rounded(fore["worst_year_share_pct"], 1))
