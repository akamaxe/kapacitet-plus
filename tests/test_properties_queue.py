"""Property test of the queue (engine/queue.py): what it reserves never exceeds the grid's headroom.

Hypothesis draws a sequence of requests on the tiny grid (solar, a charger, a heat pump, a battery,
at houses of its choosing) and the planner's decision on each (with an envelope, or firm), each one
evaluated against the queue as the previous decisions left it. A decision the queue refuses is
fine (the static study rejects a firm connection, or the house still breaks a limit); after every
approval the grid with every approved house in it, solved with the full nonlinear flow in every
weather year, breaks no limit the baseline did not already break, element by element and on either
side: no bus or cable newly out of its limit, and none the baseline already breaks pushed further
out (beyond the solver's noise), even in an hour the baseline breaks elsewhere.

Every example opens with a request the queue can approve (a small solar system with an envelope),
so each one reaches a queue of two or more houses unless a later request is refused, and the last
test checks that at least one example did.
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
# per element, how much further out than the baseline counts as deeper: the solver's noise between
# two solves of one hour is ~1e-8 pu, and each approval may deepen by up to 1e-7 pu (`worsened`)
DEEPER_V, DEEPER_PCT = 1e-5, 1e-3
_MOST_APPROVED = [0]


@pytest.fixture(scope="module")
def queue_grid(tiny_processed, tmp_path_factory):
    """The tiny grid in a folder of its own, its "existing" baseline at 0.98 pu precomputed, and the
    baseline's own violations per year and per element (what the queue is not asked to fix)."""
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
            res = m.solve(p, q)
            base[y] = {side: m.side_elements(res, p, side) for side in ("export", "import")}
        yield m, hp, ev, name, root / "data" / "requests", base


requests = st.fixed_dictionaries({
    "pv_kwp": st.floats(1.0, 16.0).map(lambda x: round(x, 1)),
    "ev_kw": st.sampled_from((0.0, 0.0, 0.0, 3.7, 11.0)),
    "hp": st.sampled_from((False, False, True)),
    "bat_kwh": st.sampled_from((0.0, 0.0, 5.0, 13.5)),
    "decision": st.sampled_from(("nonfirm", "nonfirm", "nonfirm", "firm")),
})
# the opening request: solar alone, with an envelope, which the queue approves on the tiny grid
opening = st.fixed_dictionaries({
    "pv_kwp": st.floats(1.0, 6.0).map(lambda x: round(x, 1)),
    "ev_kw": st.just(0.0), "hp": st.just(False), "bat_kwh": st.just(0.0), "decision": st.just("nonfirm"),
})


def _beyond_baseline(now: dict, was: dict, side: str) -> list[str]:
    """The elements `now` breaks that `was` does not, or breaks further out (on the export side a
    higher voltage, on the import side a lower one; a higher loading on both)."""
    out = []
    worse_v = (now["v"] - was["v"]) * (1.0 if side == "export" else -1.0)
    bus = now["bus"] & (~was["bus"] | (worse_v > DEEPER_V))
    line = now["line"] & (~was["line"] | (now["loading"] - was["loading"] > DEEPER_PCT))
    trafo = now["trafo"] & (~was["trafo"] | (now["trafo_loading"] - was["trafo_loading"] > DEEPER_PCT))
    for what, mask in (("bus", bus.any(axis=0)), ("cable", line.any(axis=0)), ("transformer", trafo),
                       ("no answer", now["nonconverged"])):
        hrs = np.flatnonzero(mask)
        if len(hrs):
            out.append(f"{what} in {len(hrs)} hours, first {hrs[:5]}")
    return out


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
        ask = data.draw(opening if k == 0 else requests, label=f"request {k}")
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
            res = m.solve(p, q)
            for side in ("export", "import"):
                worse = _beyond_baseline(m.side_elements(res, p, side), base[y][side], side)
                assert not worse, f"{y}: the queue's houses go past the baseline on the {side} side: {worse}"
    _MOST_APPROVED[0] = max(_MOST_APPROVED[0], approved)
    event(f"approved {approved} of {len(cids)}")


def test_some_example_reached_a_queue_of_two_houses():
    """A run where every decision after the first was refused would test nothing about a queue."""
    if not _MOST_APPROVED[0]:
        pytest.skip("the property test did not run (selected on its own, or skipped)")
    assert _MOST_APPROVED[0] >= 2, f"no example approved more than {_MOST_APPROVED[0]} house"
