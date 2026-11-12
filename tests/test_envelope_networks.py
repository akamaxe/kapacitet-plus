"""tools/envelope_networks.py: the decision rule of docs/envelopes-by-network.md and its tables.

The stages the script chains have their own tests. What is its own is the rule (which basis may
make a case pass, on which budget, against what) and the summary the document's tables are made
from: both are checked here on made-up cases, without data. Once the experiment's summary is
committed beside the document, the document's tables are the script's rendering of it.
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "envelope_networks.py"
DOC = ROOT / "docs" / "envelopes-by-network.md"
SUMMARY = ROOT / "docs" / "envelopes-by-network.json"


def _load():
    spec = importlib.util.spec_from_file_location("envelope_networks", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["envelope_networks"] = mod
    spec.loader.exec_module(mod)
    return mod


def _case(hourly=100.0, dayahead=95.0, worst=100.0, forecast=None, ceiling=130.0, hours=0) -> dict:
    """A case as `summarise` gives it, with the per-installation figures that the rule reads."""
    def basis(kw, violation_hours=0):
        return {"aggregate": {"kw": kw, "violation_hours": 0}, "installation": {"kw": kw, "violation_hours": violation_hours}}

    return {"network": "demo-landnetz", "name": "Kerber Landnetz, cable", "mv_pu": 1.06, "customers": 27,
            "quick": False, "binds": {"voltage": 18, "transformer": 2}, "static_kw": 60.0, "hourly_kw": hourly,
            "perfect_foresight": {"aggregate": {"kw": ceiling + 5}, "installation": {"kw": ceiling}},
            "dayahead": basis(dayahead), "intraday_worst_change": basis(worst, hours),
            "intraday_forecast": None if forecast is None else basis(forecast),
            "scorecard": {"worst_change": {"aggregate": "fail", "installation": "fail"}}}


def test_a_case_passes_only_on_an_issuable_basis_at_ten_percent_per_installation():
    m = _load()
    assert m.RULE_GAIN_PCT == 10.0 and m.RULE_VIOLATION_HOURS == 0     # the document's rule, never tuned
    r = m.rule(_case(worst=110.0))
    assert r["passes"] and r["bases"]["intraday_worst_change"]["passes"] and not r["bases"]["dayahead"]["passes"]
    assert abs(r["best_gain_pct"] - 10.0) < 1e-9 and r["room"]
    assert not m.rule(_case(worst=109.9))["passes"]                    # just under the threshold
    # the day-ahead envelopes alone can make it pass too
    assert m.rule(_case(dayahead=112.0))["bases"]["dayahead"]["passes"]


def test_perfect_foresight_never_makes_a_case_pass_and_says_whether_there_is_room():
    m = _load()
    r = m.rule(_case(ceiling=150.0))
    assert not r["passes"] and r["room"] and abs(r["ceiling_gain_pct"] - 50.0) < 1e-9
    none = m.rule(_case(ceiling=105.0))
    assert not none["passes"] and not none["room"]
    assert "no room" in m._verdict(none) and "no room" not in m._verdict(r)


def test_an_hour_above_a_limit_or_a_withheld_capacity_fails_the_basis():
    m = _load()
    assert not m.rule(_case(worst=120.0, hours=1))["passes"]
    withheld = m.rule(_case(forecast=None, worst=100.0))
    assert "intraday_forecast" not in withheld["bases"]
    # a variant whose replay broke a limit publishes no capacity (None): it has no gain and cannot pass
    case = _case()
    case["intraday_forecast"] = {"aggregate": {"kw": None, "violation_hours": 3},
                                 "installation": {"kw": None, "violation_hours": 3}}
    r = m.rule(case)
    assert r["bases"]["intraday_forecast"] == {"gain_pct": None, "violation_hours": 3, "passes": False}


def test_the_tables_read_the_summary():
    m = _load()
    case = _case(worst=112.0, forecast=108.0)
    case["rule"] = m.rule(case)
    text = m.tables([case])
    row = next(ln for ln in text.splitlines() if ln.startswith("| Kerber Landnetz, cable | 1.06 | 27 |"))
    assert "voltage 18, transformer 2" in row and "| 60.0 | 100.0 | 95.0 | 112.0 | 108.0 | 130.0 |" in row
    assert "+12.0 %" in row and "+30.0 %" in row and "**passes**" in row
    assert m.tables([]) == "No case is finished."


def test_the_document_states_the_rule_the_script_applies():
    m = _load()
    text = " ".join(DOC.read_text(encoding="utf-8").split())
    assert f"at least {m.RULE_GAIN_PCT:.0f} % more capacity than the hourly study" in text
    assert "per installation" in text and "0 hours above a grid limit" in text
    for network in m.NETWORKS:
        assert network in m.NAMES
    assert all(f"{mv:.2f} pu" in text for mv in m.MV_LEVELS)


def test_the_documents_tables_are_the_summarys():
    """Once the experiment has run: the committed summary, rendered by the script, is in the document."""
    if not SUMMARY.exists():
        return
    m = _load()
    cases = json.loads(SUMMARY.read_text(encoding="utf-8"))
    assert not any(c["quick"] for c in cases), "a smoke run is never reported"
    for c in cases:
        assert c["rule"] == m.rule(c), c["network"]
    doc = DOC.read_text(encoding="utf-8")
    for line in m.tables(cases).splitlines():
        if line.startswith("|"):
            assert line in doc, line


def test_what_the_document_and_the_readme_say_of_the_result_is_the_summarys():
    """The sentences under the tables, and the README's paragraph, give ranges over the six cases.
    Each is computed here again from the committed summary."""
    if not SUMMARY.exists():
        return
    m = _load()
    cases = json.loads(SUMMARY.read_text(encoding="utf-8"))
    doc = " ".join(DOC.read_text(encoding="utf-8").split())
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())

    def pct(x: float) -> str:
        return f"{abs(x):.1f} %"

    def agg(c: dict, basis: str) -> float:
        return 100.0 * (c[basis]["aggregate"]["kw"] / c["hourly_kw"] - 1.0)

    assert len(cases) == 6 and not any(c["rule"]["passes"] for c in cases)
    assert "**No case passes.**" in doc and "No case passes." in readme
    best = [c["rule"]["best_gain_pct"] for c in cases]
    dayahead = [c["rule"]["bases"]["dayahead"]["gain_pct"] for c in cases]
    best_agg = [max(agg(c, b) for b in m.BASES) for c in cases]
    assert max(best) < 0 and max(best_agg) < 0                       # below the hourly study on both budgets
    assert all(c["rule"]["best_gain_pct"] == c["rule"]["bases"]["intraday_forecast"]["gain_pct"] for c in cases)
    assert f"between {pct(max(best))} and {pct(min(best))} less than the hourly study" in doc
    assert f"give between {pct(max(dayahead))} and {pct(min(dayahead))} less" in doc
    assert f"in all six cases, by {pct(max(best_agg))} to {pct(min(best_agg))}" in doc
    hours = {c[b][k]["violation_hours"] for c in cases for b in m.BASES for k in ("aggregate", "installation")}
    assert hours == {0} and "0 hours above a limit" in doc
    assert all(v == "fail" for c in cases for s in c["scorecard"].values() for v in s.values())

    high = [c for c in cases if c["mv_pu"] == 1.06]
    low = [c for c in cases if c["mv_pu"] == 1.0]
    assert len(high) == 3 and all(c["binds"] == {"voltage": 20} for c in high)
    ceilings = sorted(c["rule"]["ceiling_gain_pct"] for c in high)
    assert f"{pct(ceilings[0])}, {pct(ceilings[1])} and {pct(ceilings[2])} more than the hourly study" in doc
    best_high = [c["rule"]["best_gain_pct"] for c in high]
    assert f"it stays {pct(max(best_high))} to {pct(min(best_high))} below" in doc
    trafo = sorted(c["rule"]["ceiling_gain_pct"] for c in low if c["binds"] == {"transformer": 20})
    cable = [c for c in low if c["binds"] == {"cable": 20}]
    assert len(trafo) == 2 and len(cable) == 1 and not cable[0]["rule"]["room"]
    assert f"(ceilings of {pct(trafo[0])} and {pct(trafo[1])})" in doc
    assert f"adds only {pct(cable[0]['rule']['ceiling_gain_pct'])}" in doc
    room = sorted(c["rule"]["ceiling_gain_pct"] for c in cases if c["rule"]["room"])
    assert len(room) == 5 and f"{pct(room[0])} to {pct(room[-1])} in the five cases with room" in doc
    over_static = [100.0 * (c["hourly_kw"] / c["static_kw"] - 1.0) for c in cases]
    assert f"between {pct(min(over_static))} and {pct(max(over_static))} more than the static study" in doc

    assert f"gives {pct(max(best))} to {pct(min(best))} less than the hourly study" in readme
    assert f"up to {pct(room[-1])} more where the voltage limits" in readme
    assert max(high, key=lambda c: c["rule"]["ceiling_gain_pct"])["rule"]["ceiling_gain_pct"] == room[-1]
    assert f"connects {pct(min(over_static))} to {pct(max(over_static))} more than the static one" in readme
    assert "Not computed yet." not in doc
