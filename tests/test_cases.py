"""Module 2 of a study (study/cases.py, study/sync.py, engine/request.check), on the tiny grid.

The tiny grid is one study station, built here with `study/station.compute` (quick), so its
"existing" request baselines are precomputed at both ends of the MV band. The station's process is
run in this process (`runner`), reading the station's folder as `kapacitet station cases|sync`
does with KAPACITET_DATA_DIR on it.
"""

from __future__ import annotations

import json
import shutil

import numpy as np
import pytest
from conftest import tiny_grid

HEADER = "case_id;received_at;connection_point_id;pv_kwp;pv_kw_ac;pv_phases;ev_kw;ev_phases;hp;bat_kwh;decision_dso\n"


@pytest.fixture(scope="module")
def station(tiny_processed, tmp_path_factory):
    """A study with the tiny grid as its one built station, `tiny`."""
    from kapacitet.study import init_study
    from kapacitet.study import station as st

    study = init_study(tmp_path_factory.mktemp("cases") / "study", name="Testnät AB")
    folder = study.station_dir("tiny")
    shutil.copytree(tiny_processed, folder / "processed")
    rec = study.new_record("tiny", "Tiny")
    rec["inputs"] = {"network": "x"}
    with tiny_grid(folder / "processed"):
        st.compute(study, "tiny", rec, quick=True)
    study.save({**rec, "status": "ready"})
    return study


def runner(study, sid, action, batch):
    from kapacitet.study import cases, sync

    with tiny_grid(study.station_dir(sid) / "processed"):
        (cases.station_cases if action == "cases" else sync.station_sync)(study, sid, batch)
    return 0


def _csv(path, *rows: str):
    path.write_text(HEADER + "".join(r + "\n" for r in rows), encoding="utf-8")
    return path


def _verdicts(out) -> dict[str, dict]:
    """The rows by case, the second row of a repeated case_id (its own error) left out."""
    return {r["case_id"]: r for r in out["rows"] if r["error"] != "duplicate_case"}


def test_the_baselines_are_precomputed_on_the_model_at_each_end(station):
    from kapacitet.engine import request as rq
    from kapacitet.engine.run import background_masks
    from kapacitet.paths import processed

    with tiny_grid(station.station_dir("tiny") / "processed"):
        cap = json.loads(processed("results", "capacity.json").read_text(encoding="utf-8"))
        assert cap["request_baselines"] == ["existing_mv098", "existing_mv106"]
        y = cap["years"][0]
        for v in (0.98, 1.06):
            name = rq.band_name("existing", v)
            st = np.load(processed("request", f"{name}_{y}.npz"))
            m = rq.model_at(v)
            hp, ev = background_masks(m.C)
            p, q = rq.base_injection(m, rq.baseline_definition(name), y, hp, ev)
            again = rq.build_state(m, p, q)
            assert np.array_equal(st["vdev"], again["vdev"])           # the state is that end's model's
        hi, lo = (np.load(processed("request", f"{rq.band_name('existing', v)}_{y}.npz"))["vdev"] for v in (1.06, 0.98))
        assert float(hi.astype(float).mean()) > float(lo.astype(float).mean()) + 0.05


def test_generation_counts_the_lower_end_and_consumption_the_low_end(station):
    from kapacitet.engine import request as rq
    from kapacitet.engine.run import background_masks

    with tiny_grid(station.station_dir("tiny") / "processed"):
        m = rq.model_at(0.98)
        hp, ev = background_masks(m.C)
        j = m.C - 1
        out = rq.check(rq.Request(cid=j, pv_kwp=15.0, ev_kw=11.0, baseline="existing"), hp, ev, (0.98, 1.06))
        ends = out["by_end"]
        assert set(ends) == {"0.98", "1.06"}
        assert "ev" in ends["0.98"]["static"] and "ev" not in ends["1.06"]["static"]     # consumption: low end only
        res = out["result"]
        worst = max(ends.values(), key=lambda r: (r["risk"]["mean_pct"], r["risk"]["p90_pct"]))
        assert res["risk"]["mean_pct"] == pytest.approx(worst["risk"]["mean_pct"])
        assert res["static"]["pv"]["approved"] == all(r["static"]["pv"]["approved"] for r in ends.values())
        assert res["static"]["ev"] == ends["0.98"]["static"]["ev"]
        assert res["binding_mv_pu"]["consumption"] == 0.98
        # at the high end a full grid's voltage binds: its envelope curtails at least as much as at 1.00 pu
        alone = rq.evaluate(rq.model_at(1.06), rq.Request(cid=j, pv_kwp=15.0), hp, ev,
                            ctx=rq.Context(rq.band_name("existing", 1.06)))
        assert alone["risk"]["mean_pct"] == pytest.approx(ends["1.06"]["risk"]["mean_pct"])


def test_a_batch_gives_a_row_per_case_and_an_unknown_point_is_a_row_error(station, tmp_path):
    from kapacitet.study import cases

    path = _csv(tmp_path / "batch1.csv",
                "K1;2026-09-01;C20;10;;;;;;;firm",
                "K2;2026-09-02;C19;6;5;1;;;;5;nonfirm",
                "K3;2026-09-03;C18;;;;11;3;;;declined",
                "K4;2026-09-04;NOPE;5;;;;;;;",
                "K5;2026-09-05;C17;;;;;;;;",
                "K1;2026-09-06;C16;4;;;;;;;")
    out = cases.run(station, path, compare=True, runner=runner)
    rows = _verdicts(out)
    assert set(rows) == {"K1", "K2", "K3", "K4", "K5"}
    assert rows["K4"]["error"] == "unknown_point" and rows["K4"]["proposal"] is None
    assert rows["K5"]["error"] == "nothing_requested"
    assert any(e["error"] == "duplicate_case" for e in out["errors"])
    for k in ("K1", "K2", "K3"):
        r = rows[k]
        assert r["proposal"] in cases.PROPOSALS and r["station"] == "tiny" and not r["error"]
        assert len(r["assessment_id"]) == 12 and len(r["seal_digest"]) == 64 and r["fingerprint"]
        assert r["reason_sv"] and r["method_version"]
        assert (out["folder"] / "annex" / f"{k}.md").is_file()
        doc = json.loads((out["folder"] / "annex" / f"{k}.json").read_text(encoding="utf-8"))
        assert doc["proposal"] == r["proposal"] and doc["reasons_sv"]
        assert (out["folder"] / "drafts" / k / "tidsplan.md").is_file()
        assert (r["proposal"] == "review") == (out["folder"] / "drafts" / k / "status_3m.md").is_file()
    assert rows["K1"]["curtailment_mean_pct"] is not None and rows["K3"]["curtailment_mean_pct"] is None
    text = (out["folder"] / "verdicts.csv").read_bytes()
    assert text.startswith("﻿".encode("utf-8")) and b";" in text.splitlines()[0]
    assert (out["folder"] / "verdicts.machine.csv").is_file() and (out["folder"] / "confusion.csv").is_file()
    total = sum(line["total"] for line in out["confusion"])
    assert total == 3 and {line["proposal"] for line in out["confusion"]} == set(cases.PROPOSALS)
    md = (out["folder"] / "annex" / "K1.md").read_text(encoding="utf-8")
    assert "dpPower" in md and "0,98" in md                         # protection's fixed declaration, the band


def test_the_same_case_id_gives_the_same_assessment_and_never_declined(station, tmp_path):
    from kapacitet.api import records
    from kapacitet.study import cases

    path = _csv(tmp_path / "again.csv", "A1;2026-10-01;C15;12;;;;;;8;", "A2;2026-10-02;C14;;;;22;3;1;;")
    first = _verdicts(cases.run(station, path, runner=runner))
    with tiny_grid(station.station_dir("tiny") / "processed"):
        index = (records.folder() / "index.jsonl").read_text(encoding="utf-8").splitlines()
    second = _verdicts(cases.run(station, path, runner=runner))
    assert first == second
    with tiny_grid(station.station_dir("tiny") / "processed"):
        assert (records.folder() / "index.jsonl").read_text(encoding="utf-8").splitlines() == index   # nothing new
    rows = [r for r in first.values()]
    assert all(r["proposal"] in ("firm", "nonfirm", "review") for r in rows)
    verdicts = (cases.batch_dir(station, "again") / "verdicts.machine.csv").read_text(encoding="utf-8")
    assert "declin" not in verdicts and "avslag" not in verdicts.lower()


def test_a_static_rejection_drafts_the_next_transformer_verified(station, tmp_path):
    from kapacitet.study import cases

    path = _csv(tmp_path / "big.csv", "R1;2026-10-01;C13;40;;;;;;;", "R2;2026-10-02;C12;;;;22;3;1;;")
    out = cases.run(station, path, runner=runner)
    drafted = [r for r in out["results"] if "reinforcement" in r]
    assert drafted, "a 40 kWp system on a 100 kVA station is not approved firm"
    for r in drafted:
        rf = r["reinforcement"]
        assert rf["current"]["sn_mva"] == pytest.approx(0.1)
        assert rf["next"]["std_type"] == "0.25 MVA 10/0.4 kV" and rf["by_end"]
        text = (out["folder"] / "drafts" / r["case_id"] / "forstarkning.md").read_text(encoding="utf-8")
        assert "0.25 MVA 10/0.4 kV" in text and "UTKAST" in text and "avslå" not in text


def test_a_signed_rule_is_said_on_each_row(station, tmp_path):
    from kapacitet import seals
    from kapacitet.study import StudyError, cases

    with pytest.raises(StudyError):
        cases.sign_rule(station, "nobody", ["pv"], 10, "2099-12-31")
    doc = cases.sign_rule(station, "A. Planner, network planning manager", ["pv"], 10.0, "2099-12-31")
    assert doc["role"] == "network planning manager"
    with seals.chain_at(station.evidence):
        assert seals.entry_for("rule_signature", doc["seal"]["id"])
    path = _csv(tmp_path / "signed.csv", "S1;2026-10-01;C11;8;;;;;;;", "S2;2026-10-01;C10;;;;11;;;;")
    rows = _verdicts(cases.run(station, path, runner=runner))
    assert rows["S1"]["rule"] == "signed" and rows["S2"]["rule"] == "out_of_scope"
    (station.root / "rule_signature.json").unlink()


def test_sync_rebuilds_the_queues_and_a_removed_case_leaves_the_context(station, tmp_path):
    from kapacitet.engine import queue as qu
    from kapacitet.study import cases, sync

    head = HEADER.rstrip("\n").replace(";decision_dso", "") + ";decision;decided_at\n"
    approved = tmp_path / "approved.csv"
    approved.write_text(head + "Y1;2026-08-01;C09;8;;;;;;;nonfirm;2026-08-10\n"
                               "Y2;2026-08-02;C08;;;;11;3;;;firm;2026-08-11\n"
                               "Y3;2026-08-03;C07;6;;;;;;;maybe;2026-08-12\n", encoding="utf-8")
    pending = _csv(tmp_path / "pending.csv", "P1;2026-09-01;C06;5;;;;;;;")
    out = sync.run(station, approved, pending, runner=runner)
    rows = {r["case_id"]: r for r in out["rows"]}
    assert rows["Y1"]["status"] == rows["Y2"]["status"] == "approved" and rows["Y1"]["order"] == 1
    assert rows["Y3"]["error"] == "bad_decision" and rows["P1"]["status"] == "pending"
    assert (out["folder"] / "sync_report.csv").is_file()
    folder = station.station_dir("tiny") / "requests"
    expected = {cid for found in cases.locate(station, ["C09", "C08"]).values() for _, cid in found}
    assert len(expected) == 2
    with tiny_grid(station.station_dir("tiny") / "processed"):
        for e in (0.98, 1.06):
            ctx = qu.context(qu.band_folder(e))
            assert set(ctx.overrides) == expected
            q = qu.load(qu.band_folder(e))
            assert [it["case_id"] for it in q["items"]] == ["Y1", "Y2"] and q["pending"][0]["case_id"] == "P1"
            assert q["baseline"] == f"existing_mv{int(round(e * 100)):03d}"
        before = {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.glob("queue_mv*/*")}
        cids = set(qu.context(qu.band_folder(0.98)).overrides)

    # the same lists again: the same bytes
    sync.run(station, approved, pending, runner=runner)
    after = {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.glob("queue_mv*/*")}
    assert after == before

    # a case met against the synced queue is checked against it
    path = _csv(tmp_path / "after.csv", "Q1;2026-10-01;C05;7;;;;;;;", "Q2;2026-10-01;C09;3;;;;;;;")
    rows = _verdicts(cases.run(station, path, runner=runner))
    assert rows["Q2"]["error"] == "already_approved" and not rows["Q1"]["error"]

    # Y1 taken off the list: it leaves the grid the next case meets
    approved.write_text(head + "Y2;2026-08-02;C08;;;;11;3;;;firm;2026-08-11\n", encoding="utf-8")
    sync.run(station, approved, None, runner=runner)
    with tiny_grid(station.station_dir("tiny") / "processed"):
        for e in (0.98, 1.06):
            q = qu.load(qu.band_folder(e))
            assert [it["case_id"] for it in q["items"]] == ["Y2"]
            left = set(qu.context(qu.band_folder(e)).overrides)
            assert len(left) == 1 and left < cids
        assert not list(folder.glob("queue_mv*.next")) and not list(folder.glob("queue_mv*.old"))
    rows = _verdicts(cases.run(station, path, runner=runner))
    assert not rows["Q2"]["error"]                                  # its house is free again

    # an empty list empties every station's queue
    approved.write_text(head, encoding="utf-8")
    sync.run(station, approved, None, runner=runner)
    with tiny_grid(station.station_dir("tiny") / "processed"):
        assert qu.load(qu.band_folder(0.98))["items"] == []


def test_the_protection_row_comes_from_kapacitet_only_when_the_study_asks(station, tmp_path):
    import dataclasses

    from kapacitet.study import cases
    from kapacitet.study.config import Cases

    asked = dataclasses.replace(station, config=station.config.model_copy(update={"cases": Cases(protection="kapacitet")}))
    path = _csv(tmp_path / "prot.csv", "F1;2026-10-01;C04;5;;;;;;;")
    out = cases.run(asked, path, runner=runner)
    r = out["results"][0]
    assert r["protection_mode"] == "kapacitet" and r["protection"]["cid"] == r["cid"]
    md = (out["folder"] / "annex" / "F1.md").read_text(encoding="utf-8")
    assert "IEC 60909" in md and "Bekräftas i dpPower" in md
