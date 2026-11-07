"""Property tests of the linear screens: they never skip an hour the nonlinear check flags.

Hypothesis draws the grid (the committed tiny grid, and random radial grids written in the import
contract and built by the real importer, tests/fixtures/radial.py) and the hours: one-sided
patterns of load or generation, spread out, on a few houses, on the far end or on one house, with
reactive power of either sign, and mixed hours of both signs, each at its own MV voltage of the
band 0.98-1.06 pu. A one-sided pattern is scaled to just past the first limit it meets (the
smallest scale, to 1e-9 of it, at which the nonlinear check flags the hour: the exact criterion the
screen has to keep); a mixed hour is taken as drawn. Every flagged hour must be a candidate of the
screen of its side, and the confirmation must return it.

The hand-written screen tests in test_network_model.py stay: these add grids nobody chose.
"""

from __future__ import annotations

import functools

import numpy as np
import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings
from hypothesis import strategies as st

from conftest import FIXTURES, examples, fixture_module, processed_at, tiny_grid

radial = fixture_module(FIXTURES / "radial.py", "radial")

MV_LO, MV_HI = 0.98, 1.06
TANS = (0.0, 0.1, -0.1, 0.33, -0.33)
KINDS = ("spread", "few", "far", "one")


# ------------------------------------------------------------------------------- the grids

customers = st.builds(radial.Customer,
                      service_m=st.floats(8.0, 45.0).map(lambda x: round(x, 1)),
                      service_cable=st.sampled_from(radial.SERVICE_CABLES),
                      fuse_a=st.sampled_from(radial.FUSES_A))


@st.composite
def radial_specs(draw) -> "radial.Spec":
    n = draw(st.integers(1, 6))
    cabinets = []
    for k in range(1, n + 1):
        cabinets.append(radial.Cabinet(
            parent=draw(st.integers(0, k - 1)),
            length_m=round(draw(st.floats(15.0, 260.0)), 1),
            cable=draw(st.sampled_from(radial.MAIN_CABLES)),
            customers=tuple(draw(st.lists(customers, min_size=0 if k > 1 else 1, max_size=4)))))
    sn = draw(st.sampled_from((0.05, 0.1, 0.16, 0.25, 0.4)))
    vk = round(draw(st.floats(3.5, 6.0)), 3)
    vkr = round(draw(st.floats(0.8, 1.6)), 3)
    return radial.Spec(sn, vk, vkr, tuple(cabinets))


@pytest.fixture(scope="module")
def grids(tmp_path_factory):
    """Each drawn grid built once (shrinking redraws the same ones): spec -> its network model.

    The model holds everything its screens and its power flow use, so it is built inside the
    grid's folder and used after the engine has gone back to its own."""
    root = tmp_path_factory.mktemp("radial")

    @functools.cache
    def build(spec):
        from kapacitet.engine.netmodel import get_model
        from kapacitet.grid.builder import save
        from kapacitet.importer.build import build_from_export
        from kapacitet.importer.validate import read_export, validate

        key = f"g{len(list(root.iterdir())):04d}"
        data = read_export(radial.write(spec, root / key / "contract"))
        report = validate(data)
        assert report.usable, report.text()
        network, _ = build_from_export(data)
        folder = root / key / "processed"
        with processed_at(folder):
            save(network)
            return get_model()

    return build


@pytest.fixture(scope="module")
def tiny_model(tiny_processed):
    from kapacitet.engine.netmodel import get_model

    with tiny_grid(tiny_processed):
        return get_model()


# ------------------------------------------------------------------------------- the hours

def _pattern(model, kind: str, weights: np.ndarray, pick: int) -> np.ndarray:
    C = model.C
    if kind == "spread":
        s = 0.2 + 0.8 * weights
    elif kind == "few":
        s = np.where(weights > 0.7, weights, 0.0)
    elif kind == "far":
        far = model.dist_m >= np.quantile(model.dist_m, 0.75)
        s = np.where(far, 0.5 + 0.5 * weights, 0.2 * weights)
    else:
        s = np.zeros(C)
        s[pick % C] = 1.0
    return s if s.sum() > 0 else np.ones(C)


@st.composite
def hours(draw, model):
    """One-sided patterns (for the edge) and mixed hours (as drawn), each with its MV voltage."""
    C = model.C
    unit = st.floats(0.0, 1.0, allow_nan=False)
    one_sided = []
    for _ in range(draw(st.integers(1, 6))):
        side = draw(st.sampled_from(("export", "import")))
        s = _pattern(model, draw(st.sampled_from(KINDS)), np.array(draw(st.lists(unit, min_size=C, max_size=C))),
                     draw(st.integers(0, C - 1)))
        s = s * model.trafo_s_max_kw / s.sum()          # the pattern totals the transformer's rating
        sign = 1.0 if side == "export" else -1.0
        one_sided.append((side, sign * s, sign * draw(st.sampled_from(TANS)) * s,
                          draw(st.floats(MV_LO, MV_HI))))
    per = model.trafo_s_max_kw / C
    mixed = []
    for _ in range(draw(st.integers(1, 6))):
        p = np.array(draw(st.lists(st.floats(-1.0, 1.0), min_size=C, max_size=C))) * draw(st.floats(0.3, 6.0)) * per
        mixed.append((p, p * draw(st.sampled_from(TANS)), draw(st.floats(MV_LO, MV_HI))))
    return one_sided, mixed


def _flagged(model, side: str, p, q, vm) -> np.ndarray:
    return getattr(model.violations(model.solve(p, q, vm), p), f"{side}_side")


def _just_past_the_edge(model, side: str, p, q, vm, steps: int = 32):
    """Each column at the smallest scale (to 1e-9 of it) the nonlinear check flags on `side`. `hi`
    only ever takes a flagged scale, so every column returned is flagged whatever the shape of the
    flag in the scale."""
    lo, hi = np.zeros(p.shape[1]), np.full(p.shape[1], 4.0)
    top = _flagged(model, side, p * hi, q * hi, vm)
    assert top.all(), "four times the transformer's rating must break a limit"
    for _ in range(steps):
        mid = 0.5 * (lo + hi)
        bad = _flagged(model, side, p * mid, q * mid, vm)
        hi, lo = np.where(bad, mid, hi), np.where(bad, lo, mid)
    return p * hi, q * hi


def _assert_the_screens_keep_every_flagged_hour(model, drawn) -> None:
    one_sided, mixed = drawn
    for side in ("export", "import"):
        mine = [h for h in one_sided if h[0] == side]
        if not mine:
            continue
        p = np.stack([h[1] for h in mine], axis=1)
        q = np.stack([h[2] for h in mine], axis=1)
        vm = np.array([h[3] for h in mine])
        pe, qe = _just_past_the_edge(model, side, p, q, vm)
        cand = set(getattr(model, f"screen_{side}")(pe, qe, vm_slack=vm).tolist())
        missed = sorted(set(range(pe.shape[1])) - cand)
        assert not missed, f"the {side} screen misses the edge of hours {missed} at {vm[missed]} pu"
        got = getattr(model, f"{side}_critical_hours")(pe, qe, vm)
        assert got.tolist() == list(range(pe.shape[1])), "the confirmation must keep every edge"

    p = np.stack([m[0] for m in mixed], axis=1)
    q = np.stack([m[1] for m in mixed], axis=1)
    vm = np.array([m[2] for m in mixed])
    found = model.violations(model.solve(p, q, vm), p)
    for side in ("export", "import"):
        truth = set(np.flatnonzero(getattr(found, f"{side}_side")).tolist())
        cand = set(getattr(model, f"screen_{side}")(p, q, vm_slack=vm).tolist())
        assert truth <= cand, f"the {side} screen misses mixed hours {sorted(truth - cand)}"
        assert set(getattr(model, f"{side}_critical_hours")(p, q, vm).tolist()) == truth


# ------------------------------------------------------------------------------- the tests

@settings(max_examples=examples(10))
@given(data=st.data())
def test_on_the_tiny_grid_the_screens_never_skip_a_flagged_hour(tiny_model, data):
    _assert_the_screens_keep_every_flagged_hour(tiny_model, data.draw(hours(tiny_model), label="hours"))


@settings(max_examples=examples(8))
@given(spec=radial_specs(), data=st.data())
def test_on_random_radial_grids_the_screens_never_skip_a_flagged_hour(grids, spec, data):
    model = grids(spec)
    assert model.C == spec.n_customers
    _assert_the_screens_keep_every_flagged_hour(model, data.draw(hours(model), label="hours"))
