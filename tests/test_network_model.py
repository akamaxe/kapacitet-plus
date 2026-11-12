"""The network model and the screens that the engine relies on.

On the tiny grid of tests/fixtures/grid_tiny everywhere, and on the reference network too when
it is built (the `grid_model` fixture in conftest.py).
"""

from __future__ import annotations

import numpy as np
import pandapower as pp
import pandapower.topology as top
import pytest
from conftest import on_tiny

from kapacitet.engine.powerflow import PFResult, PowerFlowNotConverged


def test_network_is_radial_and_fully_connected(grid_model):
    net = grid_model.net
    graph = top.create_nxgraph(net, respect_switches=False)
    assert top.connected_components(graph, notravbuses=set()), "graph must have components"
    assert len(list(top.unsupplied_buses(net))) == 0
    # radial: one path from the substation to every bus (edges = buses - 1 in a tree)
    assert len(net.line) + len(net.trafo) == len(net.bus) - 1
    assert len(net.ext_grid) == 1 and len(net.trafo) == 1
    # every customer sits on its own bus, downstream of at least one cable
    assert grid_model.phi.sum(axis=0).min() > 0
    assert len(set(grid_model.cust["bus"])) == grid_model.C


def test_base_case_is_within_limits(grid_model):
    """With no PV and only the modelled loads at zero, voltages sit at the no-load profile."""
    zero = np.zeros((grid_model.C, 1))
    res = grid_model.solve(zero, zero)
    v = np.abs(res.v[grid_model.check_rows])[:, 0]
    assert np.abs(v - grid_model.v_noload).max() < 1e-9
    assert (v > grid_model.v_min).all() and (v < grid_model.v_max).all()


def test_zbus_matches_pandapower(grid_model):
    rng = np.random.default_rng(4)
    p = rng.uniform(-6.0, 9.0, size=(grid_model.C, 3))
    q = rng.uniform(-1.0, 1.0, size=(grid_model.C, 3))
    res = grid_model.solve(p, q)
    net = grid_model.net
    cust_bus = grid_model.cust["bus"].to_numpy()
    for h in range(p.shape[1]):
        net.load["p_mw"] = 0.0
        net.load["q_mvar"] = 0.0
        net.sgen["p_mw"] = 0.0
        net.sgen["q_mvar"] = 0.0
        for i, bus in enumerate(cust_bus):
            sg = net.sgen.index[net.sgen.bus == bus]
            ld = net.load.index[net.load.bus == bus]
            assert len(sg) == 1 and len(ld) == 1
            net.sgen.loc[sg[0], "p_mw"] = p[i, h] / 1000.0
            net.sgen.loc[sg[0], "q_mvar"] = q[i, h] / 1000.0
        net.ext_grid.loc[net.ext_grid.index[0], "vm_pu"] = grid_model.vm_slack
        pp.runpp(net, calculate_voltage_angles=False, init="flat", tolerance_mva=1e-10)
        assert np.abs(np.abs(res.v[:, h]) - net.res_bus["vm_pu"].to_numpy()).max() < 1e-8
        assert np.abs(res.line_loading[:, h] - net.res_line["loading_percent"].to_numpy()).max() < 1e-6


def _per_house_kw(model) -> float:
    """The transformer's rating shared out per house: the draws below scale with it, so they
    stress a grid of any size alike (on the reference network, 400 kVA over 120 houses, they are
    the 2-11 kW and 2-12 kW the screens were first tested with)."""
    return model.trafo_s_max_kw / model.C


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_export_screen_never_misses_a_violation(grid_model, seed, request):
    """The linear screen is conservative: every violating hour must be in its candidate set."""
    rng = np.random.default_rng(seed)
    H, per = 400, _per_house_kw(grid_model)
    p = rng.uniform(0.0, 1.0, size=(grid_model.C, H)) * rng.uniform(0.6, 3.3, size=(grid_model.C, 1)) * per
    q = -0.05 * p
    cand = set(grid_model.screen_export(p, q).tolist())
    found = grid_model.violations(grid_model.solve(p, q), p)
    viol = found.export_side
    assert viol.any(), "the draw must produce violating hours, or the test checks nothing"
    if on_tiny(request):
        assert found.over_v.any() and found.trafo_rev.any(), "on the tiny grid both voltage and transformer"
    assert set(np.flatnonzero(viol).tolist()) <= cand


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_import_screen_never_misses_a_violation(grid_model, seed, request):
    rng = np.random.default_rng(100 + seed)
    H, per = 400, _per_house_kw(grid_model)
    p = -rng.uniform(0.0, 1.0, size=(grid_model.C, H)) * rng.uniform(0.6, 3.6, size=(grid_model.C, 1)) * per
    q = 0.1 * p
    cand = set(grid_model.screen_import(p, q).tolist())
    found = grid_model.violations(grid_model.solve(p, q), p)
    viol = found.import_side
    assert viol.any(), "the draw must produce violating hours, or the test checks nothing"
    if on_tiny(request):
        assert found.under_v.any() and (found.trafo & ~found.trafo_rev).any(), "on the tiny grid both voltage and transformer"
    assert set(np.flatnonzero(viol).tolist()) <= cand


def test_sensitivities_are_conservative_for_export(grid_model):
    """The clipped linear estimate must not underestimate the voltage rise it screens with."""
    rng = np.random.default_rng(9)
    p = rng.uniform(0.0, 8.0, size=(grid_model.C, 60))
    v_nl = np.abs(grid_model.solve(p, np.zeros_like(p)).v[grid_model.check_rows])
    v_lin = grid_model.v_noload[:, None] + grid_model.dv_dp @ p
    assert (v_lin.max(axis=0) >= v_nl.max(axis=0) - 1e-3).all()


# ------------------------------------------------------------------ hours without an answer

def _export_draw(model, seed: int, H: int = 300) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    per = _per_house_kw(model)
    p = rng.uniform(0.0, 1.0, size=(model.C, H)) * rng.uniform(0.6, 3.3, size=(model.C, 1)) * per
    return p, -0.05 * p


def _import_draw(model, seed: int, H: int = 300) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(100 + seed)
    per = _per_house_kw(model)
    p = -rng.uniform(0.0, 1.0, size=(model.C, H)) * rng.uniform(0.6, 3.6, size=(model.C, 1)) * per
    return p, 0.1 * p


def test_a_state_that_is_not_finite_is_a_violation_on_both_sides(grid_model):
    """A NaN compares False with every limit, so an all-NaN state used to read as a clean hour."""
    H = 3
    nan = np.full(H, np.nan)
    res = PFResult(v=np.full((len(grid_model.net.bus), H), np.nan + 0j),
                   i_line_ka=np.full((len(grid_model.net.line), H), np.nan),
                   line_loading=np.full((len(grid_model.net.line), H), np.nan),
                   trafo_loading=nan, trafo_p_mw=nan, iterations=60, max_mismatch=np.nan)
    v = grid_model.violations(res, np.zeros((grid_model.C, H)))
    assert v.nonconverged.all() and v.export_side.all() and v.import_side.all() and v.any.all()


def test_an_hour_that_does_not_converge_is_a_violation_and_strict_raises(grid_model):
    """Six times the per-house share of the transformer as load: no power-flow solution exists."""
    per = _per_house_kw(grid_model)
    p = np.full((grid_model.C, 3), 0.5 * per)
    p[:, 1] = -6.0 * per
    q = np.zeros_like(p)
    res = grid_model.solve(p, q)
    assert res.converged.tolist() == [True, False, True]
    v = grid_model.violations(res, p)
    assert v.nonconverged.tolist() == [False, True, False]
    assert v.import_side[1] and v.export_side[1] and v.any[1]
    with pytest.raises(PowerFlowNotConverged) as err:
        grid_model.solve(p, q, strict=True)
    assert err.value.columns.tolist() == [1]


def test_non_finite_input_is_refused_by_name_and_kept_by_the_screens(grid_model):
    from kapacitet.engine import doe

    p, q = _export_draw(grid_model, 0, H=20)
    p[grid_model.C - 1, 7] = np.nan
    with pytest.raises(ValueError, match=r"columns 7\)"):
        grid_model.solve(p, q)
    with pytest.raises(ValueError, match=r"columns 7\)"):     # the hour, not its place among the candidates
        grid_model.export_critical_hours(p, q)
    with pytest.raises(ValueError, match=r"columns 7\)"):
        doe.compute(grid_model, {"p_net": p, "q": q}, np.arange(grid_model.C)[-3:])
    assert 7 in grid_model.screen_export(p, q) and 7 in grid_model.screen_import(p, q)
    q2 = q.copy()
    q2[0, 4] = np.inf
    with np.errstate(invalid="ignore"):                       # inf times the zeros of the incidence
        assert 4 in grid_model.screen_export(np.nan_to_num(p), q2)


MV_BAND = [0.98, 1.00, 1.02, 1.06]


def _patterns(model, side: str, seed: int, n: int = 48) -> tuple[np.ndarray, np.ndarray]:
    """(C, n) shapes of load (import) or generation (export) and their reactive power: spread over
    every house, concentrated on a few, or on the houses farthest from the substation, each with
    reactive power of either sign or none, so that the hours that hit a limit do so by voltage at
    the far end, by a cable or by the transformer."""
    rng = np.random.default_rng(seed)
    C = model.C
    far = model.dist_m >= np.quantile(model.dist_m, 0.75)
    shapes = []
    for j in range(n):
        kind = j % 3
        if kind == 0:
            s = rng.uniform(0.2, 1.0, C)
        elif kind == 1:
            s = (rng.random(C) < rng.uniform(0.05, 0.4)) * rng.uniform(0.5, 1.0, C)
        else:
            s = far * rng.uniform(0.5, 1.0, C) + (~far) * rng.uniform(0.0, 0.2, C)
        shapes.append(s if s.any() else np.ones(C))
    s = np.array(shapes).T
    s *= model.trafo_s_max_kw / s.sum(axis=0)          # each pattern totals the transformer's rating
    tan = np.array([0.0, 0.33, -0.33, 0.1])[np.arange(n) % 4]
    sign = -1.0 if side == "import" else 1.0
    return sign * s, -sign * tan * s if side == "import" else tan * s


def _edge_of_the_limit(model, side: str, p: np.ndarray, q: np.ndarray, vm: float, steps: int = 40):
    """Each column scaled to just past the first limit it meets on `side`: the smallest scale (to
    1e-12 of it) at which the nonlinear check flags the hour, the exact criterion the screen has to
    keep. Returns the injections there and the flags, which are all True."""
    def flagged(scale):
        ps, qs = p * scale, q * scale
        return getattr(model.violations(model.solve(ps, qs, vm), ps), f"{side}_side")

    lo, hi = np.zeros(p.shape[1]), np.full(p.shape[1], 3.0)
    assert flagged(hi).all(), "every pattern must break a limit at the top of the search"
    for _ in range(steps):
        mid = 0.5 * (lo + hi)
        bad = flagged(mid)
        hi = np.where(bad, mid, hi)
        lo = np.where(bad, lo, mid)
    return p * hi, q * hi, flagged(hi), flagged(lo)


@pytest.mark.parametrize("vm", MV_BAND)
@pytest.mark.parametrize("side", ["import", "export"])
def test_the_screens_keep_an_hour_just_past_the_limit_across_the_mv_band(grid_model, vm, side, request):
    """The hour that breaks a limit by the smallest amount the nonlinear check flags (0.909999 pu
    against 0.91 at 1.02 pu used to pass the import screen, and reactive power injected with
    the export used to be left out of the rise) is a candidate, at every MV voltage of the
    band, for load and generation spread out, concentrated or at the far end, with reactive
    power of either sign. The scale below it is clean, so the edge is the limit's own."""
    p, q = _patterns(grid_model, side, seed=7 if side == "import" else 8)
    _assert_the_edges_are_kept(grid_model, side, p, q, vm)
    if on_tiny(request):        # on the reference network the far-end voltage comes first everywhere
        pe, qe, _, _ = _edge_of_the_limit(grid_model, side, p, q, vm)
        v = grid_model.violations(grid_model.solve(pe, qe, vm), pe)
        if side == "import":
            assert v.under_v.any() and (v.trafo | v.line).any(), "the edges must reach both kinds of limit"
        else:
            assert v.over_v.any() and (v.trafo_rev | v.line_rev).any(), "the edges must reach both kinds of limit"


def _assert_the_edges_are_kept(model, side: str, p: np.ndarray, q: np.ndarray, vm: float) -> None:
    pe, qe, past, before = _edge_of_the_limit(model, side, p, q, vm)
    assert past.all() and not before.any()
    cand = set(getattr(model, f"screen_{side}")(pe, qe, vm_slack=vm).tolist())
    missed = sorted(set(range(pe.shape[1])) - cand)
    assert not missed, f"the {side} screen at {vm} pu misses the edge of patterns {missed}"
    got = getattr(model, f"{side}_critical_hours")(pe, qe, vm)
    assert got.tolist() == list(range(pe.shape[1])), "the confirmation must keep every edge"


@pytest.mark.parametrize("vm", MV_BAND)
@pytest.mark.parametrize("seed", [3, 4])
def test_the_screens_keep_every_violating_hour_of_load_and_generation_extremes(grid_model, vm, seed):
    """Seeded draws of one sign at every house and mixed, with reactive power of both signs:
    whatever side an hour breaks, the screen of that side keeps it, including the hours that do
    not converge (a violation on both sides)."""
    _assert_the_extremes_are_kept(grid_model, vm, seed)


def _assert_the_extremes_are_kept(model, vm: float, seed: int, H: int = 200) -> None:
    rng = np.random.default_rng(seed)
    per, C = _per_house_kw(model), model.C
    gen = rng.uniform(0.0, 1.0, (C, H)) * rng.uniform(0.3, 5.0, (C, 1)) * per
    load = -rng.uniform(0.0, 1.0, (C, H)) * rng.uniform(0.3, 6.0, (C, 1)) * per
    both = rng.uniform(-1.0, 1.0, (C, H)) * rng.uniform(0.3, 6.0, (C, 1)) * per
    p = np.hstack([gen, load, both])
    q = p * rng.choice([-0.33, -0.05, 0.0, 0.1, 0.33], size=(1, p.shape[1]))
    found = model.violations(model.solve(p, q, vm), p)
    for side in ("export", "import"):
        truth = getattr(found, f"{side}_side")
        assert truth.any() and not truth.all(), f"the draw must break {side} limits in some hours only"
        cand = set(getattr(model, f"screen_{side}")(p, q, vm_slack=vm).tolist())
        missed = sorted(set(np.flatnonzero(truth).tolist()) - cand)
        assert not missed, f"the {side} screen at {vm} pu misses hours {missed[:10]}"


@pytest.mark.parametrize("vm", MV_BAND)
@pytest.mark.parametrize("seed", [0, 1])
def test_the_screens_follow_the_mv_voltage_they_are_given(grid_model, vm, seed):
    """At any MV voltage the screen keeps every hour the nonlinear flow finds violating, whether
    the voltage comes as an argument or as the model default. A model built for 1.00 pu used to
    screen at 1.00 pu whatever it was told, and missed hours at 1.06."""
    from kapacitet.engine.netmodel import get_model

    own = get_model(vm_slack=vm)
    for draw, side, screen, critical in ((_export_draw, "export_side", "screen_export", "export_critical_hours"),
                                         (_import_draw, "import_side", "screen_import", "import_critical_hours")):
        p, q = draw(grid_model, seed)
        truth = set(np.flatnonzero(getattr(grid_model.violations(grid_model.solve(p, q, vm), p), side)).tolist())
        assert truth, f"the {side} draw must violate at {vm} pu, or the test checks nothing"
        cand = getattr(grid_model, screen)(p, q, vm_slack=vm)
        assert truth <= set(cand.tolist())
        assert np.array_equal(cand, getattr(own, screen)(p, q))
        got = getattr(grid_model, critical)(p, q, vm)
        assert set(got.tolist()) == truth
        assert np.array_equal(got, getattr(own, critical)(p, q))


def test_the_screens_take_the_mv_voltage_hour_by_hour(grid_model):
    rng = np.random.default_rng(21)
    p, q = _export_draw(grid_model, 2)
    vm = rng.choice([0.98, 1.0, 1.06], size=p.shape[1])
    truth = grid_model.violations(grid_model.solve(p, q, vm), p).export_side
    assert truth.any()
    assert set(np.flatnonzero(truth).tolist()) <= set(grid_model.screen_export(p, q, vm_slack=vm).tolist())
    assert np.array_equal(grid_model.export_critical_hours(p, q, vm), np.flatnonzero(truth))


# ------------------------------------------------------------------ the same on the unbalanced model

@pytest.fixture(scope="module")
def model3(grid_model):
    """The unbalanced model of the same grid, every connection single-phase at random."""
    from kapacitet.engine.netmodel3ph import NetModel3Ph
    from kapacitet.engine.phases import phase_of

    return NetModel3Ph(phase=phase_of(grid_model.cust, np.zeros(grid_model.C), rule="random", seed=3))


def test_the_unbalanced_model_treats_an_hour_without_an_answer_alike(grid_model, model3):
    per = _per_house_kw(grid_model)
    p = np.full((model3.C, 3), 0.3 * per)
    p[:, 1] = -6.0 * per
    q = np.zeros_like(p)
    res = model3.solve(p, q)
    assert res.converged.tolist() == [True, False, True]
    v = model3.violations(res, p)
    assert v.nonconverged.tolist() == [False, True, False] and v.import_side[1] and v.export_side[1]
    with pytest.raises(PowerFlowNotConverged):
        model3.solve(p, q, strict=True)
    p[0, 2] = np.nan
    with pytest.raises(ValueError, match=r"columns 2\)"):
        model3.solve(p, q)
    assert 2 in model3.screen_export(p, q) and 2 in model3.screen_import(p, q)


@pytest.mark.parametrize("vm", MV_BAND)
def test_the_unbalanced_screens_follow_the_mv_voltage(grid_model, model3, vm):
    from kapacitet.engine.netmodel3ph import NetModel3Ph

    own = NetModel3Ph(phase=model3.phase, vm_slack=vm)
    for draw, side, screen, critical in ((_export_draw, "export_side", "screen_export", "export_critical_hours"),
                                         (_import_draw, "import_side", "screen_import", "import_critical_hours")):
        p, q = draw(grid_model, 0, H=150)
        truth = set(np.flatnonzero(getattr(model3.violations(model3.solve(p, q, vm), p), side)).tolist())
        assert truth, f"the {side} draw must violate at {vm} pu, or the test checks nothing"
        cand = getattr(model3, screen)(p, q, vm_slack=vm)
        assert truth <= set(cand.tolist())
        assert np.array_equal(cand, getattr(own, screen)(p, q))
        assert np.array_equal(getattr(model3, critical)(p, q, vm), getattr(own, critical)(p, q))


@pytest.mark.parametrize("vm", MV_BAND)
@pytest.mark.parametrize("side", ["import", "export"])
def test_the_unbalanced_screens_keep_an_hour_just_past_the_limit(model3, vm, side):
    """As on the balanced model, with every connection single-phase: a house's load raises the
    other two phases through the neutral and its generation lowers them, so a load-only hour can
    break the upper limit. The screens read that from sensitivities turned into each phase's own
    frame; read without the turn they missed it."""
    p, q = _patterns(model3, side, seed=9 if side == "import" else 10, n=24)
    _assert_the_edges_are_kept(model3, side, p, q, vm)


@pytest.mark.parametrize("vm", MV_BAND)
def test_the_unbalanced_screens_keep_every_violating_hour_of_the_extremes(model3, vm):
    """The draws of the balanced test above on the unbalanced model, where a load-only hour can
    break the upper limit on another phase and a generation-only hour the lower one."""
    _assert_the_extremes_are_kept(model3, vm, seed=5, H=100)



def test_a_network_with_a_loop_never_reaches_the_engine(tmp_path):
    """A tie between the two feeders of the tiny grid. The check refuses the export, and the model
    refuses the tables if they are built all the same: the engine takes each cable's direction of
    flow from the customers behind it, which a section of a ring does not have, so an overload on
    the tie was never counted as the generation's."""
    import conftest
    import pandas as pd

    from kapacitet.engine.netmodel import NotRadialError, get_model, require_radial
    from kapacitet.grid.builder import save
    from kapacitet.importer.build import build_from_export
    from kapacitet.importer.validate import read_export, validate

    data = read_export(conftest.TINY_GRID)
    assert validate(data).usable
    tie = {c: None for c in data["lines"].columns}
    tie.update(line_id="LTIE", from_bus="KA3", to_bus="KB5", length_m=30.0, std_type="NAYY 4x50 SE", parallel=1)
    data["lines"] = pd.concat([data["lines"], pd.DataFrame([tie])], ignore_index=True)
    report = validate(data)
    assert not report.usable and [f.code for f in report.errors] == ["meshed_network"]
    # built all the same, as a caller that skipped the check would
    network, _ = build_from_export(data)
    folder = tmp_path / "ring" / "processed"
    with conftest.processed_at(folder, conftest.synthetic_profiles.YEARS):
        save(network)
        conftest.synthetic_profiles.write(folder, network.customers, pd.read_csv(conftest.TINY_GRID / "roofs.csv"))
        with pytest.raises(NotRadialError, match="not radial: 1 cable sections close a loop"):
            get_model()
    # the rule itself: every bus has one section leading to it
    tree = pd.DataFrame({"line": [0, 1, 2], "from_bus": [0, 1, 1], "to_bus": [1, 2, 3]})
    require_radial(tree)
    with pytest.raises(NotRadialError, match="at bus 3"):
        require_radial(pd.concat([tree, pd.DataFrame({"line": [3], "from_bus": [2], "to_bus": [3]})], ignore_index=True))
