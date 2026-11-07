"""The three-phase check of a station's published figures (engine/station.three_phase_check), on the
tiny grid and its synthetic profiles.

The tiny grid records no phase, so as it is the check publishes no three-phase figure
(`phases_unknown`). `phased` is a copy with every phase known: four single-phase services on a
recorded phase (a, b, c, a) and the rest three-phase services that the phase identification
confirmed against the transformer secondary meter. The band starts at 1.02 pu because at 0.98 pu
the tiny grid's load already breaks the voltage limit (a consumption finding, no figure to check).
"""

from __future__ import annotations

import shutil

import numpy as np
import pandas as pd
import pytest
from conftest import synthetic_profiles, tiny_grid

YEARS = synthetic_profiles.YEARS
BAND = (1.02, 1.06)
ORDERS = 3
SINGLE = ("a", "b", "c", "a")          # the recorded single-phase services of `phased`, customers 0-3


def _with_phases(tiny_processed, tmp_path_factory, name: str, labels, confirm_rest: bool = False):
    """A copy of the tiny grid's folder with customers.phase set to `labels` (the rest blank) and,
    with `confirm_rest`, the blank ones confirmed three-phase in processed/meters/phase_id.parquet."""
    folder = tmp_path_factory.mktemp(name) / "processed"
    shutil.copytree(tiny_processed, folder)
    path = folder / "grid" / "customers.parquet"
    cust = pd.read_parquet(path)
    cust["phase"] = [labels[i] if i < len(labels) else "" for i in range(len(cust))]
    cust.to_parquet(path, index=False)
    if confirm_rest:
        rest = cust.loc[cust["phase"] == "", "osm_id"].astype(str).tolist()
        (folder / "meters").mkdir(exist_ok=True)
        pd.DataFrame({"customer_id": rest, "service": "three", "status": "identified",
                      "reference": "station"}).to_parquet(folder / "meters" / "phase_id.parquet")
    return folder


def _figures(folder, phase=None, **kwargs) -> dict:
    """The balanced figures over BAND and their three-phase check; `phase(gen, C)` gives the phases."""
    from kapacitet.engine import request
    from kapacitet.engine import station as es
    from kapacitet.engine.run import background_masks

    with tiny_grid(folder):
        gen = es.generation_capacity(BAND, YEARS, n_orders=ORDERS)
        low = request.model_at(BAND[0])
        hp, ev = background_masks(low.C)
        cons = es.consumption_capacity(low, "ev", YEARS, ORDERS, 50.0, hp, ev)
        three = es.three_phase_check(gen, cons, BAND, YEARS, hp, ev,
                                     phase=None if phase is None else phase(gen, low.C), **kwargs)
        caps = es.doe.customer_caps()
    return {"gen": gen, "cons": cons, "three": three, "caps": caps}


def _adopters_on_a(gen: dict, C: int) -> np.ndarray:
    """Every connection three-phase except the published order's new PV, all on phase a."""
    from kapacitet.engine.phases import THREE_PHASE

    phase = np.full(C, THREE_PHASE)
    phase[gen["cids"]] = 0
    return phase


@pytest.fixture(scope="module")
def phased(tiny_processed, tmp_path_factory):
    return _figures(_with_phases(tiny_processed, tmp_path_factory, "phased", SINGLE, confirm_rest=True))


@pytest.fixture(scope="module")
def on_a(tiny_processed):
    return _figures(tiny_processed, phase=_adopters_on_a, min_known_share=0.0)


def _never_above(r: dict) -> None:
    """Every side with a three-phase figure: not above the balanced one, and verified on every hour."""
    three = r["three"]
    assert three["status"] in ("ok", "limited")
    for side, block in (("generation", r["gen"]), ("consumption", r["cons"])):
        t = three[side]
        assert block["status"] == "ok" and t["balanced_kw"] == pytest.approx(block["kw"]), side
        if t["kw"] is None:
            assert t["status"] == "baseline_violates" and three["status"] == "limited", side
            continue
        assert t["status"] in ("ok", "limited"), side
        assert t["kw"] <= block["kw"] + 1e-9 and t["adopters"] <= block["adopters"], side
        assert (t["status"] == "ok") == (t["adopters"] == block["adopters"]), side
        assert t["verification"]["full_year_violations"] == 0 and t["verification"]["hours_checked"] > 0, side
    if three["generation"]["kw"] is not None:
        assert set(three["generation"]["verification"]["by_end"]) == {f"{v:.2f}" for v in BAND}


def test_the_three_phase_figure_is_never_above_the_balanced_one(phased, on_a):
    for r in (phased, on_a):
        _never_above(r)
    t = phased["three"]
    assert t["known_share"] == 1.0 and t["recorded_single"] == len(SINGLE)
    assert t["confirmed_three"] == t["customers"] - len(SINGLE)
    assert t["phases"]["consumption"] == {"a": 2, "b": 1, "c": 1, "three_phase": t["customers"] - len(SINGLE)}


def _check_binding(t: dict, phase: str | None = None) -> None:
    from kapacitet.engine.phases import PHASE_NAMES

    assert t["status"] == "limited" and t["kw"] < t["balanced_kw"]
    b = t["binding"]
    assert b["limit"] in ("voltage", "cable", "transformer", "unbalance") and b["phase"] in PHASE_NAMES
    assert phase is None or b["phase"] == phase
    assert b["component"]["type"] in ("bus", "line", "transformer") and b["component"]["id"]
    assert b["vm_pu"] in BAND and b["hours"] > 0


def test_a_limited_figure_names_the_phase_and_the_component(phased, on_a):
    # the new PV all on phase a: the generation figure falls and phase a binds
    assert on_a["three"]["status"] == "limited"
    _check_binding(on_a["three"]["generation"], "a")
    # two single-phase services on phase a: the chargers they add load that phase first
    assert phased["three"]["status"] == "limited" and phased["three"]["generation"]["status"] == "ok"
    _check_binding(phased["three"]["consumption"], "a")


def test_with_balanced_phases_the_two_models_agree(tiny_processed):
    """Every connection three-phase: the unbalanced model is the balanced one, so the figures hold
    (at most one adopter apart, for the flows' last decimals at the limit)."""
    from kapacitet.engine.phases import THREE_PHASE

    r = _figures(tiny_processed, phase=lambda gen, C: np.full(C, THREE_PHASE), min_known_share=0.0)
    _never_above(r)
    for side in ("generation", "consumption"):
        t = r["three"][side]
        assert t["status"] in ("ok", "limited") and t["balanced_adopters"] - t["adopters"] <= 1, side
    assert r["three"]["generation"]["balanced_kw"] - r["three"]["generation"]["kw"] <= float(r["caps"].max()) + 1e-9


def test_unknown_phases_publish_no_three_phase_figure(tiny_processed):
    from kapacitet.engine import station as es

    with tiny_grid(tiny_processed):
        known = es.known_phases()
        three = es.three_phase_check({"status": "ok", "kw": 10.0, "adopters": 2, "cids": [0, 1]},
                                     {"status": "ok", "kw": 11.0, "adopters": 1, "cids": [0], "device": "ev"},
                                     BAND, YEARS)
    assert known["known"] == 0 and known["known_share"] == 0.0
    assert three["status"] == "phases_unknown" and "known phase" in three["reason"]
    assert three["generation"]["kw"] is None and three["consumption"]["kw"] is None
    assert three["min_known_share"] == pytest.approx(0.8)


def test_a_three_phase_service_counts_as_known_only_when_the_meters_confirm_it(tiny_processed, tmp_path_factory):
    from kapacitet.engine import station as es

    folder = _with_phases(tiny_processed, tmp_path_factory, "confirmed", ("a", "b"))
    cust = pd.read_parquet(folder / "grid" / "customers.parquet")
    blank = cust.loc[cust["phase"] == "", "osm_id"].astype(str).tolist()
    (folder / "meters").mkdir(exist_ok=True)
    # three confirmed; one below the confidence and one against a relative reference are not
    pd.DataFrame({"customer_id": blank[:5], "service": "three",
                  "status": ["identified"] * 3 + ["low_confidence", "identified"],
                  "reference": ["station"] * 4 + ["clusters"]}).to_parquet(folder / "meters" / "phase_id.parquet")
    with tiny_grid(folder):
        known = es.known_phases()
    assert known["recorded_single"] == 2 and known["confirmed_three"] == 3 and known["known"] == 5
    assert known["known_share"] == pytest.approx(5 / len(cust))


def test_a_limited_generation_figure_holds_and_the_next_adopter_breaks_a_limit(tiny_processed, on_a):
    """Solved again with fresh unbalanced models, independently of the check: the published count
    breaks no limit (the voltage unbalance factor included) at either end, and one more adopter does."""
    from kapacitet.engine import station as es
    from kapacitet.engine.netmodel3ph import NetModel3Ph
    from kapacitet.engine.run import background_masks

    t, cids = on_a["three"]["generation"], np.asarray(on_a["gen"]["cids"], dtype=int)
    assert t["status"] == "limited"

    def bad(m3, n, hp, ev):
        out = 0
        for _y, p, q in es._pv_states(cids, n, m3.C, hp, ev, YEARS):
            res = m3.solve(p, q)
            v = m3.violations(res, p)
            out += int((v.export_side | m3.unbalance(res).over_limit).sum())
        return out

    with tiny_grid(tiny_processed):
        phase = _adopters_on_a(on_a["gen"], len(on_a["caps"]))
        models = {e: NetModel3Ph(phase=phase, vm_slack=e) for e in BAND}
        hp, ev = background_masks(len(phase))
        assert all(bad(m, t["adopters"], hp, ev) == 0 for m in models.values())
        assert bad(models[t["binding"]["vm_pu"]], t["adopters"] + 1, hp, ev) > 0
