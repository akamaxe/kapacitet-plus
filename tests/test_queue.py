"""The queue of decisions: an approval re-verifies every year and the next request sees less headroom."""

from __future__ import annotations

import json

import numpy as np
import pytest

from conftest import needs_results

pytestmark = needs_results


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
