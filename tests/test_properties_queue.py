"""Property test of the queue (engine/queue.py): what it reserves never exceeds the grid's headroom.

Hypothesis draws a sequence of requests on the tiny grid (solar, a charger, a heat pump, a battery,
at houses of its choosing) and the planner's decision on each (with an envelope, or firm), each one
evaluated against the queue as the previous decisions left it. A decision the queue refuses is
fine (the static study rejects a firm connection, or the house still breaks a limit); after every
approval the grid with every approved house in it, solved with the full nonlinear flow in every
weather year, breaks no limit in an hour the baseline did not already break, on either side.
"""

from __future__ import annotations

import itertools
import shutil

import numpy as np
import pytest

pytest.importorskip("hypothesis")

from hypothesis import event, given, settings
from hypothesis import strategies as st

from conftest import examples, tiny_grid

pytestmark = pytest.mark.files_only

_COUNTER = itertools.count()
VM = 0.98


@pytest.fixture(scope="module")
def queue_grid(tiny_processed, tmp_path_factory):
    """The tiny grid in a folder of its own, its "existing" baseline at 0.98 pu precomputed, and the
    baseline's own violations per year (the hours the queue is not asked to fix)."""
    from kapacitet.config import settings as site
    from kapacitet.engine import request as rq
    from kapacitet.engine.run import background_masks

    root = tmp_path_factory.mktemp("queue")
    folder = root / "data" / "processed"
    shutil.copytree(tiny_processed, folder)
    with tiny_grid(folder):
        m = rq.model_at(VM)
        hp, ev = background_masks(m.C)
        name = rq.band_name("existing", VM)
        rq.precompute(m, name, site().weather_years, hp, ev)
        base = {}
        for y in site().weather_years:
            p, q = rq.Context(name).pq(m, y, hp, ev)
            v = m.violations(m.solve(p, q), p)
            base[y] = (v.export_side, v.import_side)
        yield m, hp, ev, name, root / "data" / "requests", base


requests = st.fixed_dictionaries({
    "pv_kwp": st.floats(1.0, 16.0).map(lambda x: round(x, 1)),
    "ev_kw": st.sampled_from((0.0, 0.0, 3.7, 11.0)),
    "hp": st.booleans(),
    "bat_kwh": st.sampled_from((0.0, 0.0, 5.0, 13.5)),
    "decision": st.sampled_from(("nonfirm", "nonfirm", "firm")),
})


@settings(max_examples=examples(4))
@given(data=st.data())
def test_the_queues_reservations_never_take_the_grid_past_a_limit(queue_grid, data):
    import json

    from kapacitet.engine import queue as qu
    from kapacitet.engine import request as rq

    m, hp, ev, name, requests_dir, base = queue_grid
    n = next(_COUNTER)
    where = requests_dir / f"queue_e{n:04d}"         # a queue, and assessment ids, of the example's own
    cids = data.draw(st.lists(st.integers(0, m.C - 1), min_size=2, max_size=3, unique=True), label="houses")
    approved = 0
    for k, cid in enumerate(cids):
        ask = data.draw(requests, label=f"request {k}")
        decision = ask.pop("decision")
        ctx = qu.context(where)
        r = rq.Request(cid=cid, baseline="queue" if ctx else name, **ask)
        out = rq.evaluate(m, r, hp, ev, ctx=ctx)
        record = {"id": f"{n:06d}{k:06d}", "request": dict(r.__dict__), "result": json.loads(json.dumps(out, default=float))}
        try:
            qu.decide(m, hp, ev, record, decision, where=where)
        except qu.QueueError as e:
            assert e.code in ("static_rejects_firm", "still_violates"), e.code
            event(f"refused: {e.code}")
            continue
        approved += 1
        ctx = qu.context(where)
        assert ctx is not None and cid in ctx.overrides
        for y in ctx.states:
            p, q = ctx.pq(m, y, hp, ev)
            v = m.violations(m.solve(p, q), p)
            exp0, imp0 = base[y]
            new_exp = np.flatnonzero(v.export_side & ~exp0)
            new_imp = np.flatnonzero(v.import_side & ~imp0)
            assert not len(new_exp), (f"{y}: the queue's houses break an export limit in {len(new_exp)} hours "
                                      f"the baseline was within, first {new_exp[:5]}")
            assert not len(new_imp), (f"{y}: the queue's houses break an import limit in {len(new_imp)} hours "
                                      f"the baseline was within, first {new_imp[:5]}")
    event(f"approved {approved} of {len(cids)}")
