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
