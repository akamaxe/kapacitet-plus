"""The queue of decisions: an approval re-verifies every year and the next request sees less headroom."""

from __future__ import annotations

import io
import json

import numpy as np
import pytest

from conftest import needs_results, tiny_grid


@pytest.fixture(scope="module")
def engine():
    from kapacitet.engine.run import background_masks, model

    m = model()
    hp, ev = background_masks(m.C)
    return m, hp, ev


@pytest.fixture()
def queue_dir(tmp_path, monkeypatch):
    from kapacitet.engine import queue as qu

    monkeypatch.setattr(qu, "folder", lambda: tmp_path / "queue")
    qu.reset()
    yield qu
    qu.reset()


def _record(engine, job_id: str, ctx=None, **req) -> dict:
    """A saved assessment as the API keeps it (data/requests/<id>.json.gz)."""
    from kapacitet.engine import request as rq

    m, hp, ev = engine
    r = rq.Request(**req)
    out = rq.evaluate(m, r, hp, ev, ctx=ctx)
    return {"id": job_id, "request": dict(r.__dict__), "result": json.loads(json.dumps(out, default=float))}


@needs_results
def test_an_approval_is_verified_every_year_and_later_requests_see_less_headroom(engine, queue_dir):
    from kapacitet.engine import request as rq

    qu = queue_dir
    m, hp, ev = engine
    first = _record(engine, "a00000000001", cid=84, pv_kwp=20.0, baseline="doe")
    item = qu.decide(m, hp, ev, first, "nonfirm", "test approval")
    assert item["decision"] == "nonfirm" and item["pv_kw"] > 0
    left = item["verification"]["all_years"]
    assert left["export_violations_after"] == 0
    assert len(item["verification"]["years_verified"]) > 1

    q = qu.load()
    assert q["version"] == 1 and q["baseline"] == "doe" and len(qu.approved(q)) == 1

    # the grid with the approved house in it stays within its limits in every year
    ctx = qu.context()
    assert 84 in ctx.overrides and ctx.states
    for st in ctx.states.values():
        assert float(st["trafo_loading"].astype(float).max()) <= m.thermal + 1e-2
        assert float((1.0 + st["vdev"].astype(float)).max()) <= m.v_max + 1e-4

    # a neighbour asking after it meets less headroom than against the published scenario
    neighbour = dict(cid=76, pv_kwp=15.0)
    alone = rq.evaluate(m, rq.Request(**neighbour, baseline="doe"), hp, ev)
    after = rq.evaluate(m, rq.Request(**neighbour, baseline="queue"), hp, ev, ctx=ctx)
    assert after["queue"]["version"] == 1
    assert sum(after["profiles"]["envelope_kw"]) <= sum(alone["profiles"]["envelope_kw"]) + 1e-6
    assert after["summary"]["pv_curtailment_ref_pct"] >= alone["summary"]["pv_curtailment_ref_pct"] - 1e-9


@needs_results
def test_the_queue_refuses_what_no_longer_describes_the_grid(engine, queue_dir):
    qu = queue_dir
    m, hp, ev = engine
    a = _record(engine, "b00000000001", cid=84, pv_kwp=20.0, baseline="doe")
    b = _record(engine, "b00000000002", cid=76, pv_kwp=15.0, baseline="doe")
    qu.decide(m, hp, ev, a, "nonfirm")
    with pytest.raises(qu.QueueError, match="evaluate against the queue"):
        qu.decide(m, hp, ev, b, "nonfirm")
    with pytest.raises(qu.QueueError, match="already approved"):
        qu.decide(m, hp, ev, {**a, "id": "b00000000003"}, "nonfirm")
    stale = _record(engine, "b00000000004", ctx=qu.context(), cid=90, pv_kwp=10.0, baseline="queue")
    stale["result"]["queue"]["version"] = 0
    with pytest.raises(qu.QueueError, match="changed since"):
        qu.decide(m, hp, ev, stale, "nonfirm")


@needs_results
def test_a_refusal_is_recorded_without_touching_the_grid_and_firm_needs_the_static_study(engine, queue_dir):
    qu = queue_dir
    m, hp, ev = engine
    rec = _record(engine, "c00000000001", cid=84, pv_kwp=20.0, baseline="static")
    assert rec["result"]["static"]["pv"]["approved"] is False
    with pytest.raises(qu.QueueError, match="static study rejects"):
        qu.decide(m, hp, ev, rec, "firm")
    item = qu.decide(m, hp, ev, rec, "declined", "no capacity this year")
    q = qu.load()
    assert item["decision"] == "declined" and q["version"] == 0 and not qu.approved(q)
    assert not list(qu.folder().glob("state_*.npz"))
    qu.reset()
    assert qu.load()["items"] == []


@pytest.mark.files_only
@needs_results
def test_a_crash_while_the_states_are_written_leaves_the_queue_as_it_was(engine, queue_dir, monkeypatch):
    from kapacitet import seals
    from kapacitet.config import settings

    qu = queue_dir
    m, hp, ev = engine
    first = _record(engine, "d00000000001", cid=84, pv_kwp=20.0, baseline="doe")
    item = qu.decide(m, hp, ev, first, "nonfirm")
    assert item["verification"]["years_verified"] == sorted(settings().weather_years)   # every year, all 11
    before = qu.load()
    loading = {y: st["trafo_loading"].copy() for y, st in qu.context().states.items()}

    second = _record(engine, "d00000000002", ctx=qu.context(), cid=76, pv_kwp=15.0, baseline="queue")
    real, written = qu.savez_atomic, []

    def disk_full_at_the_third_year(path, **arrays):
        if path.name.startswith("state_"):
            written.append(path.name)
            if len(written) == 3:
                raise OSError("no space left on device")
        return real(path, **arrays)

    monkeypatch.setattr(qu, "savez_atomic", disk_full_at_the_third_year)
    with pytest.raises(OSError):
        qu.decide(m, hp, ev, second, "nonfirm")
    monkeypatch.setattr(qu, "savez_atomic", real)
    assert len(written) == 3 and all(name.endswith(f".v{before['version'] + 1}.npz") for name in written)

    # read again from disk: the version on record, its states, the one house, its seal
    qu._CACHE.clear()
    assert qu.load() == before
    ctx = qu.context()
    assert set(ctx.overrides) == {84} and set(ctx.states) == set(loading)
    assert all(np.array_equal(ctx.states[y]["trafo_loading"], loading[y]) for y in loading)
    assert seals.entry_for("decision", second["id"]) is None
    assert seals.verify("decision", first["id"], before["items"][0])["ok"]
    qu.recover()                                    # the next start removes what never committed
    assert not list(qu.folder().glob(f"state_*.v{before['version'] + 1}.npz"))
    assert len(list(qu.folder().glob(f"state_*.v{before['version']}.npz"))) == len(loading)


@needs_results
def test_a_seal_that_failed_in_process_is_sealed_by_the_next_decision(engine, queue_dir, monkeypatch):
    from kapacitet import seals

    qu = queue_dir
    m, hp, ev = engine
    first = _record(engine, "d00000000011", cid=84, pv_kwp=20.0, baseline="doe")
    real = seals.seal
    monkeypatch.setattr(seals, "seal", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        qu.decide(m, hp, ev, first, "nonfirm")
    monkeypatch.setattr(seals, "seal", real)
    assert seals.entry_for("decision", first["id"]) is None
    second = _record(engine, "d00000000012", ctx=qu.context(), cid=76, pv_kwp=15.0, baseline="queue")
    qu.decide(m, hp, ev, second, "nonfirm")
    assert seals.entry_for("decision", first["id"]) is not None
    assert seals.entry_for("decision", second["id"]) is not None


# ---------------------------------------------------------------- a rebuild from the DSO's list (tiny grid)

@pytest.fixture()
def tiny_queue(tiny_processed, tmp_path):
    """The tiny grid in a folder of its own, with the "existing" baseline at 0.98 pu precomputed."""
    import shutil

    from kapacitet.config import settings
    from kapacitet.engine import request as rq
    from kapacitet.engine.run import background_masks

    folder = tmp_path / "data" / "processed"
    shutil.copytree(tiny_processed, folder)
    with tiny_grid(folder):
        m = rq.model_at(0.98)
        hp, ev = background_masks(m.C)
        name = rq.band_name("existing", 0.98)
        rq.precompute(m, name, settings().weather_years, hp, ev)
        yield m, hp, ev, name, tmp_path / "data" / "requests" / "queue_mv098"


def _files(where) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(where.iterdir())}


@pytest.mark.files_only
def test_a_rebuild_is_byte_equal_and_a_removed_case_leaves_the_context(tiny_queue):
    from kapacitet.engine import queue as qu

    m, hp, ev, name, where = tiny_queue
    a, b = m.C - 1, m.C - 2
    items = [{"assessment": "0000000000a1", "cid": a, "decision": "nonfirm", "decided": "2026-08-01",
              "request": {"cid": a, "pv_kwp": 8.0}},
             {"assessment": "0000000000a2", "cid": b, "decision": "firm", "decided": "2026-08-02",
              "request": {"cid": b, "ev_kw": 22.0, "hp": True}}]
    q = qu.rebuild(m, hp, ev, name, items, [{"case_id": "P1", "cid": 3}], where=where)
    assert [it["assessment"] for it in q["items"]] == ["0000000000a1", "0000000000a2"] and q["version"] == 2
    assert q["pending"] == [{"case_id": "P1", "cid": 3}]
    # the DSO approved it firm though the static study rejects it: kept, and flagged
    assert "static_rejects_firm" in q["items"][1]["flags"] and q["items"][0]["verified"]
    first = _files(where)
    assert {"queue.json", "0000000000a1.npz", "0000000000a2.npz"} <= set(first)
    qu.rebuild(m, hp, ev, name, items, [{"case_id": "P1", "cid": 3}], where=where)
    assert _files(where) == first                                  # the same list: the same bytes
    ctx = qu.context(where)
    assert set(ctx.overrides) == {a, b} and ctx.label["approved"] == 2

    qu.rebuild(m, hp, ev, name, items[1:], where=where)
    ctx = qu.context(where)
    assert set(ctx.overrides) == {b} and ctx.label["version"] == 1
    assert not (where / "0000000000a1.npz").exists()
    assert not where.with_name(where.name + ".next").exists() and not where.with_name(where.name + ".old").exists()
    y = next(iter(ctx.states))
    with_a = np.load(io.BytesIO(first[f"state_{y}.v2.npz"]))
    assert not np.array_equal(with_a["pt"], ctx.states[y]["pt"])   # the grid no longer holds a's export


@pytest.mark.files_only
def test_recover_finishes_a_swap_a_crash_interrupted(tiny_queue):
    import os

    from kapacitet.engine import queue as qu

    m, hp, ev, name, where = tiny_queue
    items = [{"assessment": "0000000000b1", "cid": 1, "decision": "nonfirm", "decided": "2026-08-01",
              "request": {"cid": 1, "pv_kwp": 5.0}}]
    qu.rebuild(m, hp, ev, name, items, where=where)
    kept = _files(where)
    old = where.with_name(where.name + ".old")
    os.replace(where, old)                                         # stopped between the two renames
    where.with_name(where.name + ".next").mkdir()
    qu.recover(where)
    assert _files(where) == kept and not old.exists() and not where.with_name(where.name + ".next").exists()
