"""Module 1 of a study (engine/station.py, study/station.compute, study/upstream.py,
study/collect.py, engine/validation_measured.py), on the tiny grid and its synthetic profiles.

`equipped` is a copy of the tiny grid's folder with equipment records: two houses with existing PV,
recorded heat pumps and a charger, and two metered customers. The contract on a station's
capacity.json is `check_capacity`, run on the one the tiny grid's station computes here and, in
test_results_contract.py's way, on a station folder's own when KAPACITET_DATA_DIR is one.
"""

from __future__ import annotations

import json
import shutil

import numpy as np
import pandas as pd
import pytest
from conftest import grid_model, needs_grid, on_tiny, synthetic_profiles, tiny_grid  # noqa: F401

YEARS = synthetic_profiles.YEARS
BAND = (0.98, 1.06)
EXISTING_PV = (3, 7)          # cids with PV today
HP_TRUE, HP_FALSE, CHARGER = 5, 1, 9
METERED = (HP_TRUE, CHARGER)


@pytest.fixture(scope="module")
def equipped(tiny_processed, tmp_path_factory):
    """The tiny grid with equipment records and two metered customers, in a folder of its own."""
    folder = tmp_path_factory.mktemp("equipped") / "processed"
    shutil.copytree(tiny_processed, folder)
    path = folder / "grid" / "customers.parquet"
    cust = pd.read_parquet(path)
    C = len(cust)
    cust["existing_pv_kwp"] = 0.0
    cust["existing_pv_kw_ac"] = 0.0
    cust.loc[list(EXISTING_PV), "existing_pv_kwp"] = 6.0
    cust.loc[list(EXISTING_PV), "existing_pv_kw_ac"] = 5.0
    cust["existing_pv_phase"] = ""
    cust["existing_battery_kwh"] = 0.0
    cust["existing_battery_kw"] = 0.0
    cust["existing_ev_charger_kw"] = 0.0
    cust.loc[CHARGER, "existing_ev_charger_kw"] = 11.0
    cust["existing_ev_phases"] = 0
    hp = pd.array([pd.NA] * C, dtype="boolean")
    hp[HP_TRUE], hp[HP_FALSE] = True, False
    cust["existing_heat_pump"] = hp
    cust["existing_source"] = ""
    cust.to_parquet(path, index=False)
    with tiny_grid(folder):
        from kapacitet.engine.scenario import year_profiles

        for y in YEARS:
            metered = np.full((C, len(year_profiles(y)["base"][0])), np.nan, dtype=np.float32)
            for c in METERED:
                metered[c] = year_profiles(y)["base"][c] * 1.5
            np.save(folder / "households" / f"metered_{y}.npy", metered)
    return folder


# ---------------------------------------------------------------- existing equipment

def test_existing_pv_is_firm_and_metered_devices_are_not_added_twice(equipped):
    from kapacitet.engine import hosting, request
    from kapacitet.engine.run import background_masks
    from kapacitet.engine.scenario import Scenario, customers, existing, year_profiles

    with tiny_grid(equipped):
        C = len(customers())
        ex = existing()
        assert ex["recorded"] and set(np.flatnonzero(ex["has_pv"])) == set(EXISTING_PV)
        y = YEARS[-1]
        none = np.zeros(C, dtype=bool)
        arr = Scenario(year=y, pv=none, hp=none, ev=none).arrays()
        for c in EXISTING_PV:                           # producing with no mask, at most its own rating
            assert arr["pv"][c].max() > 0 and arr["pv"][c].max() <= 5.0 + 1e-6
        named = Scenario(year=y, pv=np.ones(C, dtype=bool), hp=none, ev=none).arrays()
        assert np.allclose(named["pv"][list(EXISTING_PV)], arr["pv"][list(EXISTING_PV)])     # nothing added
        # the consumption side's state leaves every solar system out, the existing ones too
        p, _ = hosting.consumption_state(y, none, none)
        assert (p <= 1e-9).all()
        # a metered customer's recorded heat pump or charger is in its meter already
        prof = year_profiles(y)
        everything = Scenario(year=y, pv=none, hp=np.ones(C, dtype=bool), ev=np.ones(C, dtype=bool)).arrays()
        base = prof["base"][HP_TRUE]
        assert np.allclose(everything["load"][HP_TRUE], base + prof["ev"][HP_TRUE], atol=1e-4)
        assert np.allclose(everything["load"][CHARGER], prof["base"][CHARGER] + prof["hp"][CHARGER], atol=1e-4)
        hp, ev = background_masks(C)
        assert hp[HP_TRUE] and not hp[HP_FALSE] and not hp[CHARGER]       # a metered house gets no synthetic one
        assert set(np.flatnonzero(ev)) == {CHARGER}
        # the static summer snapshot holds the existing PV too, and the "existing" baseline names it
        p_s, _ = hosting.summer_snapshot(none)
        assert all(p_s[c] > -hosting.min_daytime_load()[c] for c in EXISTING_PV)
        assert request.baseline_definition("existing")["pv_cids"] == list(EXISTING_PV)


def test_candidate_orders_exclude_existing_pv(equipped):
    from kapacitet.engine import station as es
    from kapacitet.engine.scenario import customers

    with tiny_grid(equipped):
        cand = es.candidates()
        assert not cand[list(EXISTING_PV)].any()
        assert (cand == (customers()["kwp"].to_numpy() > 0) & ~np.isin(np.arange(len(cand)), EXISTING_PV)).all()
        for o in range(4):
            order = es.candidate_order(o, cand)
            assert set(order) == set(np.flatnonzero(cand)) and not set(order) & set(EXISTING_PV)
        g = es.generation_capacity(BAND, YEARS, n_orders=3)
        assert g["status"] == "ok" and not set(g["cids"]) & set(EXISTING_PV)
        assert g["candidates"] == int(cand.sum()) and g["existing_pv_kw_ac"] == pytest.approx(10.0)


# ---------------------------------------------------------------- generation

@pytest.fixture(scope="module")
def generation(tiny_processed):
    from kapacitet.engine import station as es

    with tiny_grid(tiny_processed):
        yield {"band": es.generation_capacity(BAND, YEARS, n_orders=3),
               "unity": es.generation_capacity((1.00, 1.00), YEARS, n_orders=3)}


def test_the_50th_percentile_is_the_per_order_median_and_an_actual_order(generation):
    from kapacitet.engine import station as es

    for end in generation["band"]["by_end"].values():
        kws = [r["kw"] for r in end["per_order"]]
        assert end["kw"] == pytest.approx(float(np.median(kws)))
        assert end["kw"] == next(r["kw"] for r in end["per_order"] if r["order"] == end["order"])
    # with an even number of orders, "lower" is the lower of the two middle orders, never a mean
    assert es.pick([1.0, 4.0, 2.0, 3.0], 50) == (2.0, 2)
    assert es.pick([5.0, 1.0, 9.0], 50) == (5.0, 0)


def test_the_published_generation_is_not_above_either_end_or_1_00_pu(generation):
    g, unity = generation["band"], generation["unity"]
    assert g["status"] == "ok" and unity["status"] == "ok"
    ends = {float(k): r["kw"] for k, r in g["by_end"].items()}
    assert set(ends) == set(BAND)
    assert all(g["kw"] <= kw + 1e-9 for kw in ends.values())
    assert g["kw"] <= unity["kw"] + 1e-9
    assert ends[g["binding_mv_pu"]] == g["kw"]
    # the tiny grid's long feeder binds the voltage at the top of the band
    assert g["binding_mv_pu"] == 1.06 and g["binding"]["limit"] == "voltage"
    assert g["verification"]["full_year_violations"] == 0 and g["verification"]["nonconverged_hours"] == 0
    assert g["doe"]["status"] == "not_approved"


def test_the_device_names_map_in_one_place():
    from kapacitet.engine import hosting
    from kapacitet.engine import station as es
    from kapacitet.study.config import Method

    names = Method.model_fields["consumption_device"].annotation.__args__
    assert set(es.DEVICES) == set(names) and set(es.DEVICES.values()) <= set(hosting.IMPORT_DEVICES)
    assert es.engine_device("heat_pump") == "hp" and es.engine_device("ev") == "ev"
    with pytest.raises(ValueError):
        es.engine_device("hp")


# ---------------------------------------------------------------- consumption

def test_consumption_is_not_higher_at_the_low_end_and_a_broken_grid_is_a_finding(grid_model, request):
    """The low end of the band is consumption's worst case: there its capacity is not above the
    high end's. On the tiny grid today's load already breaks the voltage limit at 0.98 pu, which
    is a finding with its hours, never 0 kW."""
    from kapacitet.config import settings
    from kapacitet.engine import station as es
    from kapacitet.engine.netmodel import get_model

    years = YEARS if on_tiny(request) else (settings().reference_year,)
    low, high = (es.consumption_capacity(get_model(vm_slack=v), "ev", years, n_orders=1) for v in BAND)
    assert high["status"] == "ok" and high["kw"] > 0
    if low["status"] == "ok":
        assert low["kw"] <= high["kw"] + 1e-9
    else:
        assert low["status"] == "baseline_violates" and low["kw"] is None
        assert sum(low["baseline_violation_hours"].values()) > 0
    if on_tiny(request):
        assert low["status"] == "baseline_violates"
        assert high["verification"]["full_year_violations"] == 0


# ---------------------------------------------------------------- the weakest point

def test_the_vectorised_headroom_is_the_per_customer_one(tiny_processed):
    from kapacitet.engine import request
    from kapacitet.engine.netmodel import get_model
    from kapacitet.engine.run import background_masks

    with tiny_grid(tiny_processed):
        m = get_model(vm_slack=1.02)
        hp, ev = background_masks(m.C)
        from kapacitet.engine.scenario import Scenario

        arr = Scenario(year=YEARS[0], pv=np.zeros(m.C, dtype=bool), hp=hp, ev=ev).arrays()
        st = request.build_state(m, arr["p_net"].astype(float), arr["q"].astype(float))
        ex, im = request.export_headroom_all(m, st, chunk=500), request.import_headroom_all(m, st, chunk=500)
        for j in (0, 7, m.C - 1):
            assert np.allclose(ex[j], request.export_headroom(m, st, j))
            assert np.allclose(im[j], request.import_headroom(m, st, j))


@pytest.mark.parametrize("side,vm", [("export", 1.06), ("import", 1.02)])
def test_the_worst_point_passes_the_nonlinear_check_and_fails_just_above(tiny_processed, side, vm):
    from kapacitet.engine import station as es
    from kapacitet.engine.netmodel import get_model

    with tiny_grid(tiny_processed):
        m = get_model(vm_slack=vm)
        w = es.worst_point(m, side, YEARS)
        assert w["status"] == "ok" and 0 < w["kw"] < es.WORST_MAX_KW
        assert w["kw"] == min(c["kw"] for c in w["checked"]) and len(w["checked"]) == es.WORST_K
        hp, ev = es._masks(m.C, None, None)

        def violating(kw: float) -> int:
            n = 0
            for _y, p, q in es._today(m, side, hp, ev, YEARS):
                p2, q2 = es.add_constant(p, q, w["cid"], kw, side)
                v = m.violations(m.solve(p2, q2), p2)
                n += int((v.export_side if side == "export" else v.import_side).sum())
            return n

        assert violating(w["kw"]) == 0
        assert violating(w["kw"] + 1e-3) > 0
        assert w["binding"]["limit"] in es.EXPORT_LIMITS


# ---------------------------------------------------------------- measured validation

def test_synthetic_measurements_give_no_error_and_a_bias_is_found(tiny_processed):
    from kapacitet.engine import validation_measured as vm
    from kapacitet.engine.netmodel import get_model
    from kapacitet.engine.run import background_masks
    from kapacitet.engine.scenario import customers
    from kapacitet.timeindex import year_index

    with tiny_grid(tiny_processed):
        m = get_model()
        C = m.C
        hp, ev = background_masks(C)
        y = YEARS[-1]
        hours = np.arange(4000, 4048)
        today = vm.replay(y, pd.DataFrame(columns=["customer_id", "year", "hour", "kwh_import", "kwh_export"]),
                          hp, ev)
        p, q = today["p"][:, hours], today["q"][:, hours]
        res = m.solve(p, q, 1.01)
        v = np.abs(res.v[m.cust_bus_row])
        same = vm.compare(m, p, q, 1.01, v, res.trafo_loading)
        assert same["voltage_pu"]["max"] < 1e-9 and same["transformer_pct"]["max"] < 1e-9
        biased = vm.compare(m, p, q, 1.01, v - 0.01, res.trafo_loading - 2.0)
        assert biased["voltage_pu"]["bias"] == pytest.approx(0.01, abs=1e-9)
        assert biased["transformer_pct"]["bias"] == pytest.approx(2.0, abs=1e-9)

        # the files' way: three metered customers, their meters' voltages and the measured MV
        ids = customers()["osm_id"].astype(str).tolist()
        metered = (0, 4, 11)
        start = year_index(y)[hours]
        meters = pd.DataFrame([{"customer_id": ids[c], "start": t, "year": y, "hour": int(h),
                                "kwh_import": float(max(-p[c, k], 0.0)), "kwh_export": float(max(p[c, k], 0.0))}
                               for c in metered for k, (h, t) in enumerate(zip(hours, start))])
        rp = vm.replay(y, meters, hp, ev)
        assert rp["complete"][hours].all() and rp["complete"].sum() == len(hours)
        res2 = m.solve(rp["p"][:, hours], rp["q"][:, hours], 1.01)
        v2 = np.abs(res2.v[m.cust_bus_row])
        for offset, bias in ((0.0, 0.0), (-2.3, 0.01)):
            volt = pd.DataFrame([{"customer_id": ids[c], "start": t, "v_l1": v2[c, k] * 230 + offset,
                                  "v_l2": v2[c, k] * 230 + offset, "v_l3": v2[c, k] * 230 + offset}
                                 for c in metered for k, t in enumerate(start)])
            mv = pd.DataFrame({"start": start, "vm_pu": 1.01})
            out = vm.validate(lambda x: get_model(vm_slack=x), meters, (y,), hp, ev, BAND, volt=volt, mv=mv)
            got = out["bases"]["measured"]
            assert out["mv"] == "measured" and got["hours"] == len(hours)
            assert got["voltage_pu"]["n"] == len(metered) * len(hours)
            assert got["voltage_pu"]["bias"] == pytest.approx(bias, abs=1e-9)
        band = vm.validate(lambda x: get_model(vm_slack=x), meters, (y,), hp, ev, BAND, volt=volt)
        assert set(band["bases"]) == {"band_low", "band_high"} and band["mv"] == "band"


def test_a_dppower_snapshot_and_static_results_are_compared_by_id(tiny_processed):
    from conftest import TINY_GRID
    from kapacitet.engine import hosting
    from kapacitet.engine import validation_measured as vm
    from kapacitet.engine.netmodel import get_model
    from kapacitet.importer.build import bus_index

    buses = pd.read_csv(TINY_GRID / "buses.csv", dtype=str)
    lines = pd.read_csv(TINY_GRID / "lines.csv", dtype=str)["line_id"].tolist()
    lv = pd.read_csv(TINY_GRID / "transformer.csv", dtype=str)["lv_bus"].iloc[0]
    index = bus_index(buses, lv)
    with tiny_grid(tiny_processed):
        m = get_model()
        p, q = hosting.winter_snapshot(np.zeros(m.C, dtype=bool), np.zeros(m.C, dtype=bool))
        res = m.solve(p[:, None], q[:, None])
        b = buses["bus_id"].iloc[3]
        snap = pd.DataFrame([{"mrid": f"_{b.upper()}", "kind": "bus", "vm_pu": float(np.abs(res.v[index[b], 0])) - 0.005},
                             {"mrid": lines[1], "kind": "line", "loading_pct": float(res.line_loading[1, 0])},
                             {"mrid": "T", "kind": "transformer", "loading_pct": float(res.trafo_loading[0]) + 1.0},
                             {"mrid": "nowhere", "kind": "bus", "vm_pu": 1.0}])
        out = vm.compare_snapshot(m, snap, index, lines, None, p, q, None)
        assert out["matched"] == 3 and out["unmatched"] == ["nowhere"]
        assert out["voltage_pu"]["bias"] == pytest.approx(0.005, abs=1e-9)
        assert out["loading_pct"]["max"] == pytest.approx(1.0, abs=1e-9)

        ids = m.cust["osm_id"].astype(str).tolist()
        gen = hosting.nodal_pv_hc(m)
        dso = pd.DataFrame({"customer_id": ids[:3] + ["X"], "gen_kw": [f"{gen[i] - 1:.6f}".replace(".", ",") for i in range(3)] + ["1"]})
        cal = vm.calibrate_static(dso, gen, None)
        assert (cal["matched"], cal["unmatched"]) == (3, 1)
        assert cal["gen_kw"]["bias"] == pytest.approx(1.0, abs=1e-5)


# ---------------------------------------------------------------- upstream caps

def _study(tmp_path, upstream: str | None, stations: str | None):
    from kapacitet.study import init_study

    study = init_study(tmp_path / "study", "Test DSO", allow_git=True)
    if upstream is not None:
        (study.inputs / "upstream.csv").write_text(upstream, encoding="utf-8")
    if stations is not None:
        (study.inputs / "stations.csv").write_text(stations, encoding="utf-8")
    return study


def test_the_published_value_is_the_minimum_over_the_levels_and_a_blank_is_not_entered(tmp_path):
    from kapacitet.study import upstream

    study = _study(tmp_path,
                   "level;id;parent_id;gen_headroom_kw;load_headroom_kw;source;as_of\n"
                   "feeder;F1;S1;80,5;;planning;2026-10-01\n"
                   "substation;S1;R1;300;40;planning;2026-10-01\n"
                   "regional;R1;;;;;\n",
                   "station_id,name,feeder_id,hidden\nA,Alpha,F1,\nB,Beta,F1,yes\nC,Gamma,,\n")
    up = upstream.read(study)
    assert not up["findings"]
    chain, published = upstream.apply(up, "A", 120.0, 25.0)
    assert [n["id"] for n in chain["chain"]] == ["F1", "S1", "R1"]
    assert chain["chain"][0]["shared_by"] == ["A", "B"] and not chain["hidden"]
    assert published["generation"] == {"kw": 80.5, "limited_by": "feeder:F1", "not_entered": ["regional:R1"],
                                       "complete": False}
    assert published["consumption"]["kw"] == 25.0 and published["consumption"]["limited_by"] == "station"
    assert published["consumption"]["not_entered"] == ["feeder:F1", "regional:R1"]
    _, p = upstream.apply(up, "A", 30.0, 60.0)
    assert p["generation"]["limited_by"] == "station" and p["consumption"] == {
        "kw": 40.0, "limited_by": "substation:S1", "not_entered": ["feeder:F1", "regional:R1"], "complete": False}
    # a finding of the station stays a finding: no kW is made up for it
    _, p = upstream.apply(up, "A", None, 10.0)
    assert p["generation"]["kw"] is None and p["generation"]["limited_by"] == "finding"
    assert upstream.apply(up, "B", 1.0, 1.0)[0]["hidden"]
    c, _ = upstream.apply(up, "C", 1.0, 1.0)
    assert c["chain"] == [] and "no feeder_id" in c["findings"][0]


# ---------------------------------------------------------------- a station's capacity.json

def check_capacity(doc: dict) -> None:
    """The contract on a station's results/capacity.json."""
    from kapacitet.engine import station as es

    assert doc["schema"] == es.SCHEMA and doc["method_version"] == es.METHOD_VERSION
    for key in ("station", "computed_at", "fingerprint", "mv_band_pu", "years", "percentile", "generation",
                "consumption", "worst_point", "upstream", "published", "peak_check", "verification"):
        assert key in doc, key
    lo, hi = doc["mv_band_pu"]
    assert lo <= hi and len(doc["fingerprint"]) == 64
    assert doc["verification"]["full_year_violations"] == 0
    for side in ("generation", "consumption"):
        block = doc[side]
        assert block["status"] in ("ok", "baseline_violates"), side
        if block["status"] == "ok":
            assert block["kw"] >= 0 and block["verification"]["full_year_violations"] == 0
        else:                                  # a finding with its hours, never 0 kW
            assert block["kw"] is None and sum(block["baseline_violation_hours"].values()) > 0
            assert doc["published"][side]["kw"] is None
        pub = doc["published"][side]
        if block["kw"] is not None:
            assert pub["kw"] <= block["kw"] + 1e-9
    g = doc["generation"]
    if g["status"] == "ok":
        assert g["binding_mv_pu"] in (lo, hi)
        assert all(g["kw"] <= r["kw"] + 1e-9 for r in g["by_end"].values() if r["status"] == "ok")
        if g["doe"]["status"] == "ok":
            assert g["doe"]["approval_reference"] and g["doe"]["full_year_violations"] == 0
    assert doc["consumption"]["mv_pu"] == lo
    pc = doc["peak_check"]
    assert pc["velander_kw"] > 0 and pc["modelled_peak_kw"] > 0


def test_a_station_computes_its_capacity_json_and_the_study_collects_it(equipped, tmp_path):
    import duckdb

    from kapacitet.paths import processed
    from kapacitet.study import collect
    from kapacitet.study import station as st

    study = _study(tmp_path, "level,id,parent_id,gen_headroom_kw,load_headroom_kw,source,as_of\n"
                             "feeder,F1,,1,,the DSO's planning,2026-10-01\n",
                   "station_id,name,feeder_id\ntiny,Tiny,F1\n")
    rec = study.new_record("tiny", "Tiny")
    rec["inputs"] = {"network": "x"}
    study.station_dir("tiny").mkdir(parents=True)
    with tiny_grid(equipped):
        out = st.compute(study, "tiny", rec, quick=True)
        doc = json.loads(processed("results", "capacity.json").read_text(encoding="utf-8"))
        assert processed("results", "peak_check.json").is_file()
        assert processed("request", "existing.json").is_file()
    check_capacity(doc)
    assert out["generation_status"] == "ok" and doc["published"]["generation"]["kw"] == 1.0
    assert doc["published"]["generation"]["limited_by"] == "feeder:F1"
    assert out["consumption_kw"] == doc["consumption"]["kw"] and doc["consumption"]["device"] == "ev"
    assert not set(doc["generation"]["cids"]) & set(EXISTING_PV)

    # collected: the station's folder holds the file as a built station's would
    results = study.station_dir("tiny") / "processed" / "results"
    results.mkdir(parents=True)
    (results / "capacity.json").write_text(json.dumps(doc), encoding="utf-8")
    study.save({**rec, "status": "ready"})
    (study.inputs / "upstream.csv").write_text("level,id,parent_id,gen_headroom_kw,load_headroom_kw,source,as_of\n"
                                               "feeder,F1,,,,,\n", encoding="utf-8")
    got = collect.collect(study, "2026Q4")
    assert got["rows"] == 1 and got["rewritten"] == ["tiny"]
    rows = duckdb.sql(f"SELECT * FROM read_parquet('{got['path'].as_posix()}')").df()
    r = rows.iloc[0]
    assert r["station_id"] == "tiny" and r["generation_kw"] == pytest.approx(doc["generation"]["kw"])
    assert r["published_generation_kw"] == pytest.approx(doc["generation"]["kw"])     # the cap is now blank
    assert r["published_generation_limited_by"] == "station" and not r["upstream_complete"]
    assert r["consumption_status"] == doc["consumption"]["status"]
    again = json.loads((results / "capacity.json").read_text(encoding="utf-8"))
    assert again["published"]["generation"]["not_entered"] == ["feeder:F1"]
    with pytest.raises(Exception, match="quarter"):
        collect.collect(study, "2026-4")


@needs_grid
def test_a_station_folders_capacity_json_holds_its_contract():
    """When KAPACITET_DATA_DIR is a built station's folder (kapacitet study build <dir>)."""
    from kapacitet.paths import processed_ro

    path = processed_ro("results", "capacity.json")
    if not path.is_file():
        pytest.skip("no capacity.json here (run: kapacitet study build <dir>, with KAPACITET_DATA_DIR on a station)")
    check_capacity(json.loads(path.read_text(encoding="utf-8")))
