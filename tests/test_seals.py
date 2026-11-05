"""Seals make every answer and decision tamper-evident (kapacitet/seals.py). No data needed: the
conftest fixture points the chain at the test's own folder."""

from __future__ import annotations

import json
import math

import pytest


def test_canonical_json_is_stable_across_round_trips():
    from kapacitet import seals

    rec = {"b": [1, 2.5, {"z": True, "a": None}], "a": "Smedslättsvägen 71A", "c": 1e-7}
    assert seals.canonical(rec) == seals.canonical(json.loads(json.dumps(rec)))
    assert seals.canonical({"y": 1, "x": 2}) == seals.canonical({"x": 2, "y": 1})
    assert seals.digest(rec) == seals.digest(json.loads(seals.canonical(rec)))


def test_non_finite_numbers_are_written_as_null_deterministically():
    from kapacitet import seals

    a = seals.canonical({"v": math.nan, "w": [math.inf, -math.inf]})
    assert a == seals.canonical({"v": None, "w": [None, None]})
    assert b"NaN" not in a and b"Infinity" not in a


def test_a_sealed_record_verifies_and_a_changed_one_does_not():
    from kapacitet import seals

    rec = {"id": "0123456789ab", "result": {"summary": {"pv_curtailment_ref_pct": 2.1}}}
    entry = seals.seal("assessment", rec["id"], rec)
    assert entry["seq"] == 1 and len(entry["digest"]) == 64
    ok = seals.verify("assessment", rec["id"], current=rec)
    assert ok["ok"] and ok["copy_ok"] and ok["chain_ok"] and ok["record_ok"]

    tampered = json.loads(json.dumps(rec))
    tampered["result"]["summary"]["pv_curtailment_ref_pct"] = 0.0
    bad = seals.verify("assessment", rec["id"], current=tampered)
    assert not bad["ok"] and not bad["record_ok"] and bad["copy_ok"]


def test_the_chain_detects_a_removed_or_reordered_line():
    from kapacitet import seals

    for i in range(3):
        seals.seal("decision", f"a{i:011d}", {"decision": "firm", "i": i})
    assert seals.verify_chain()["ok"]
    path = seals.folder() / "seals.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()

    path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")       # one removed
    assert seals.verify_chain() == {**seals.verify_chain(), "ok": False, "broken_at": 2}

    path.write_text("\n".join([lines[1], lines[0], lines[2]]) + "\n", encoding="utf-8")  # reordered
    assert not seals.verify_chain()["ok"]


def test_an_edited_copy_breaks_its_own_seal():
    from kapacitet import seals

    seals.seal("settings", "20261101T120000Z", {"allocation_policy": "maxmin"})
    copy = seals.folder() / "sealed" / "settings" / "20261101T120000Z.json"
    copy.write_text(copy.read_text(encoding="utf-8").replace("maxmin", "prorata"), encoding="utf-8")
    v = seals.verify("settings", "20261101T120000Z")
    assert v["sealed"] and not v["copy_ok"] and not v["ok"]


def test_a_record_cannot_be_sealed_twice_with_different_content():
    from kapacitet import seals

    seals.seal("assessment", "aaaaaaaaaaaa", {"x": 1})
    seals.seal("assessment", "aaaaaaaaaaaa", {"x": 1})        # the same content again is harmless
    with pytest.raises(ValueError):
        seals.seal("assessment", "aaaaaaaaaaaa", {"x": 2})
    with pytest.raises(ValueError):
        seals.seal("assessment", "../escape", {"x": 1})
    with pytest.raises(ValueError):
        seals.seal("nonsense", "bbbbbbbbbbbb", {"x": 1})


def test_fingerprint_changes_when_an_input_file_changes(tmp_path, monkeypatch):
    from kapacitet import seals

    monkeypatch.setattr(seals, "CONFIG", tmp_path)
    monkeypatch.setattr(seals, "PROCESSED", tmp_path / "none")
    monkeypatch.setattr(seals, "RAW", tmp_path / "none")
    (tmp_path / "assumptions.toml").write_text("a = 1\n", encoding="utf-8")
    first = seals.fingerprint()
    assert set(first["files"]) == {"config/assumptions.toml"}
    (tmp_path / "assumptions.toml").write_text("a = 2\n", encoding="utf-8")
    assert seals.fingerprint()["digest"] != first["digest"]


def test_emptying_the_queue_seals_what_it_held(tmp_path, monkeypatch):
    from kapacitet import seals
    from kapacitet.engine import queue as qu

    monkeypatch.setattr(qu, "folder", lambda: tmp_path / "queue")
    (tmp_path / "queue").mkdir()
    held = {"version": 2, "baseline": "static", "items": [{"assessment": "0123456789ab", "decision": "declined"}]}
    (tmp_path / "queue" / "queue.json").write_text(json.dumps(held), encoding="utf-8")
    qu.reset()
    resets = seals.sealed_of_kind("queue_reset")
    assert len(resets) == 1 and resets[0]["record"]["items"] == held["items"]
    assert not (tmp_path / "queue").exists()


def test_a_torn_last_line_is_left_out_reported_and_cut_off_by_the_next_seal():
    from kapacitet import seals

    for i in range(2):
        seals.seal("decision", f"d{i:011d}", {"decision": "firm", "i": i})
    path = seals.folder() / "seals.jsonl"
    whole = path.read_bytes()
    third = seals.canonical({"seq": 3, "kind": "decision"})
    path.write_bytes(whole + third[: len(third) // 2])               # a crash in the middle of an append
    torn = seals.verify_chain()
    assert torn["ok"] and torn["length"] == 2 and torn["torn_tail"]
    assert seals.verify("decision", "d00000000001")["ok"]
    entry = seals.seal("decision", "d00000000002", {"decision": "nonfirm"})
    assert entry["seq"] == 3
    after = seals.verify_chain()
    assert after == {**after, "ok": True, "length": 3, "torn_tail": False}
    assert path.read_bytes().startswith(whole) and path.read_bytes().count(b"\n") == 3


def test_a_last_line_that_lost_only_its_newline_still_counts():
    from kapacitet import seals

    seals.seal("decision", "e00000000000", {"decision": "firm"})
    path = seals.folder() / "seals.jsonl"
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    assert seals.verify_chain() == {**seals.verify_chain(), "ok": True, "length": 1, "torn_tail": False}
    seals.seal("decision", "e00000000001", {"decision": "firm"})
    assert seals.verify_chain()["ok"] and seals.verify_chain()["length"] == 2


def test_erasing_a_record_removes_its_copy_and_keeps_the_chain_valid():
    from kapacitet import seals

    for i in range(3):
        seals.seal("assessment", f"f{i:011d}", {"result": {"i": i}})
    gone = seals.erase("assessment", "f00000000001", "retention: older than 30 days")
    assert gone["kind"] == "erasure" and gone["seq"] == 4
    assert seals.sealed_copy("assessment", "f00000000001") is None
    v = seals.verify("assessment", "f00000000001")
    assert v["sealed"] and v["erased"] and v["ok"] and v["copy_ok"] is None
    assert v["reason"].startswith("retention") and v["digest"] == seals.digest({"result": {"i": 1}})
    assert seals.verify_chain() == {**seals.verify_chain(), "ok": True, "length": 4}
    for other in ("f00000000000", "f00000000002"):                  # the others are untouched
        assert seals.verify("assessment", other)["ok"] and not seals.verify("assessment", other)["erased"]
    assert [r["entry"]["id"] for r in seals.sealed_of_kind("assessment")] == ["f00000000000", "f00000000002"]
    assert seals.erase("assessment", "f00000000001", "again")["seq"] == 4  # once is enough
    with pytest.raises(ValueError):
        seals.erase("assessment", "ffffffffffff", "never sealed")
    with pytest.raises(ValueError):
        seals.erase("erasure", "assessment.f00000000001", "an erasure stays")


def test_a_decision_whose_seal_a_crash_left_owed_is_sealed_at_the_next_start(tmp_path, monkeypatch):
    from kapacitet import seals
    from kapacitet.engine import queue as qu

    monkeypatch.setattr(qu, "folder", lambda: tmp_path / "queue")
    rec = {"id": "0123456789ab", "request": {"cid": 3, "baseline": "static"}, "result": {"customer": {"cid": 3}}}
    real = seals.seal

    def power_cut(*args, **kwargs):
        raise OSError("power cut")

    monkeypatch.setattr(seals, "seal", power_cut)
    with pytest.raises(OSError):
        qu.decide(None, None, None, rec, "declined")
    monkeypatch.setattr(seals, "seal", real)
    assert qu.load()["items"][0]["assessment"] == rec["id"]                  # committed, its seal owed
    assert seals.entry_for("decision", rec["id"]) is None and (qu.folder() / qu.PENDING).exists()
    assert qu.recover()["sealed"] == rec["id"]
    assert seals.verify("decision", rec["id"], qu.load()["items"][0])["ok"]
    assert not (qu.folder() / qu.PENDING).exists()
    (qu.folder() / qu.PENDING).write_text(json.dumps({"kind": "decision", "id": rec["id"]}), encoding="utf-8")
    assert qu.recover()["sealed"] == rec["id"] and seals.verify_chain()["length"] == 1     # sealed once only
    # a crash before the commit: the marker names a decision the queue never took
    (qu.folder() / qu.PENDING).write_text(json.dumps({"kind": "decision", "id": "fedcba987654"}), encoding="utf-8")
    assert qu.recover()["sealed"] is None and not (qu.folder() / qu.PENDING).exists()
    assert seals.entry_for("decision", "fedcba987654") is None
