"""Which phase each meter sits on, from the voltages the meters store per phase.

Without a phase register the model has to place single-phase connections by a rule, and on the
reference grid the static capacity falls from the round-robin figure to the random one
(results/phases.json). A meter that stores its voltage per phase (`v_l1..v_l3` in the readings,
kept at 15 minutes by ingest/meters.py as processed/meters/voltage_15min.parquet) carries the
answer: the voltage on a phase moves with the current on that phase, so the quarter-hour changes
of a single-phase meter's voltage follow those of its own phase at the station and not the other
two (T. A. Short, "Advanced metering for phase identification, transformer identification, and
secondary modeling", IEEE Trans. Smart Grid 4(2), 2013, does the same with correlation).

What is correlated:

    the changes        the difference between consecutive quarter-hours of each voltage, so a
                       slow drift or a meter's calibration offset does not count
    the reference      per phase, the transformer secondary meter's voltages when the DSO gives
                       them (`v_l1..v_l3` in inputs/mv/<station>.csv): labels are the station's.
                       Without one, the three-phase meters, aligned to each other by their best
                       phase order (the order most of them share is called a, b, c); without three
                       of those either, the single-phase meters grouped in three by correlation,
                       whose names a, b and c are arbitrary. The source is in every row
    the differential   each reference phase minus the mean of the three: what moves the phases
                       together (the MV voltage, the balanced load) says nothing about the phase,
                       so it is taken out of the reference, and regressed out of a single-phase
                       meter's changes

A single-phase meter (one voltage column) gets the phase whose differential correlates best; its
confidence is the margin of that correlation over the second best. A three-phase meter gets the
order of its three columns, out of the six, whose correlations sum highest; the order matters
where the customer has a single-phase inverter recorded in equipment.csv, since that inverter's
phase may be written in the meter's labels. A meter whose voltage does not move is "flat", one
with too few changes "too_few"; neither is identified. Below `phase_id_min_confidence`
(config/assumptions.toml) a meter is "low_confidence": the evidence is kept, the phase is not used.

`apply` writes the identified phases into the network's customers: where the DSO recorded none,
always; where it recorded another, only with `prefer_identified` (`--prefer-identified`), and
the conflict is reported either way. Only an identification against the station meter is written:
with the other two references the names a, b, c are relative, so the evidence stays in
phase_id.parquet and nothing goes into the customers (`phase_id_not_applied`).
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..importer.validate import NOTE, WARNING, Report

PHASES = ("a", "b", "c")
CHANNELS = ("v_l1", "v_l2", "v_l3")
STEP = pd.Timedelta(minutes=15)
MIN_CHANGES = 96 * 7            # a week of quarter-hour changes, at the least, to say anything
MIN_FILLED = 0.05               # a voltage column counts for a meter only with this share of readings
FLAT_SHARE = 0.01               # a meter whose voltage changes in fewer of its quarter-hours is flat
MIN_REFERENCE_METERS = 3        # three-phase meters it takes to stand in for the station meter
ORDERS = tuple(itertools.permutations(range(3)))
OUT_COLUMNS = ["customer_id", "service", "channel", "phase", "order", "confidence", "score", "second",
               "corr_a", "corr_b", "corr_c", "changes", "status", "reference", "matters"]


# ---------------------------------------------------------------- reading

def read_reference(path: str | Path) -> pd.DataFrame | None:
    """The transformer secondary meter's phase voltages in inputs/mv/<station>.csv: start (UTC)
    and v_l1..v_l3, or None when the file has no such columns. The separator is sniffed and a
    decimal comma is read as a point; a time without its UTC offset is refused."""
    from ..engine.validation_measured import require_offset

    path = Path(path)
    if not path.is_file():
        return None
    df = pd.read_csv(path, sep=None, engine="python", dtype=str, encoding="utf-8-sig")
    df.columns = [c.strip().lower() for c in df.columns]
    if "start" not in df.columns or not set(CHANNELS) <= set(df.columns):
        return None
    require_offset(df["start"], path)
    out = pd.DataFrame({"start": pd.to_datetime(df["start"], utc=True, errors="coerce", format="ISO8601")})
    for c in CHANNELS:
        out[c] = pd.to_numeric(df[c].str.replace(",", ".", regex=False), errors="coerce")
    return out.dropna(subset=["start"]).groupby("start", as_index=False).mean()


def _grid(volt: pd.DataFrame, reference: pd.DataFrame | None) -> pd.DatetimeIndex:
    starts = [pd.DatetimeIndex(pd.to_datetime(volt["start"], utc=True))]
    if reference is not None and len(reference):
        starts.append(pd.DatetimeIndex(reference["start"]))
    t = starts[0].append(starts[1:]) if len(starts) > 1 else starts[0]
    t = t[(t.asi8 % STEP.value) == 0]
    if not len(t):
        return pd.DatetimeIndex([], tz="UTC")
    return pd.date_range(t.min(), t.max(), freq=STEP)


def _changes(x: np.ndarray) -> np.ndarray:
    """Quarter-hour changes along the last axis (NaN where either end is missing)."""
    return np.diff(x, axis=-1)


def _cube(volt: pd.DataFrame, grid: pd.DatetimeIndex) -> tuple[list[str], np.ndarray]:
    """(meter ids, (M, 3, T) voltages on the grid, NaN where missing)."""
    v = volt.copy()
    v["start"] = pd.to_datetime(v["start"], utc=True)
    v["customer_id"] = v["customer_id"].astype(str)
    for c in CHANNELS:
        if c not in v.columns:
            v[c] = np.nan
    ids = sorted(v["customer_id"].unique())
    pos = pd.Series(np.arange(len(grid)), index=grid)
    t = pos.reindex(v["start"]).to_numpy()
    ok = ~np.isnan(t)
    m = pd.Series(np.arange(len(ids)), index=ids)[v["customer_id"]].to_numpy()
    cube = np.full((len(ids), 3, len(grid)), np.nan)
    for k, c in enumerate(CHANNELS):
        cube[m[ok], k, t[ok].astype(int)] = pd.to_numeric(v[c], errors="coerce").to_numpy(dtype=float)[ok]
    return ids, cube


# ---------------------------------------------------------------- statistics with gaps

def _z(x: np.ndarray) -> np.ndarray:
    """Standardised along the last axis, ignoring NaN (a constant row becomes all NaN)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        mu = np.nanmean(x, axis=-1, keepdims=True)
        sd = np.nanstd(x, axis=-1, keepdims=True)
        return np.where(sd > 0, (x - mu) / sd, np.nan)


def _corr(x: np.ndarray, Y: np.ndarray) -> np.ndarray:
    """Correlation of x (T,) with each row of Y (k, T) over the times both have."""
    out = np.full(Y.shape[0], np.nan)
    for i, y in enumerate(Y):
        m = np.isfinite(x) & np.isfinite(y)
        if m.sum() < 3:
            continue
        a, b = x[m] - x[m].mean(), y[m] - y[m].mean()
        den = np.sqrt((a * a).sum() * (b * b).sum())
        if den > 0:
            out[i] = float((a * b).sum() / den)
    return out


def _without(x: np.ndarray, c: np.ndarray | None) -> np.ndarray:
    """x with its regression on the common mode c taken out (where both exist)."""
    if c is None:
        return x
    m = np.isfinite(x) & np.isfinite(c)
    if m.sum() < 3:
        return x
    cc = c[m] - c[m].mean()
    var = float((cc * cc).sum())
    if var <= 0:
        return x
    beta = float(((x[m] - x[m].mean()) * cc).sum()) / var
    return x - beta * np.where(np.isfinite(c), c, 0.0)


def _differential(d3: np.ndarray) -> np.ndarray:
    """(3, T) changes minus their mean over the phases."""
    with np.errstate(invalid="ignore"):
        return d3 - np.nanmean(d3, axis=0, keepdims=True) if np.isfinite(d3).any() else d3


def _best_order(z3: np.ndarray, ref: np.ndarray) -> tuple[tuple[int, int, int], float, float, np.ndarray]:
    """The order (channel k -> phase order[k]) whose correlations with the reference sum highest:
    (order, its mean correlation, the second best's, the correlations of channel k with phase order[k])."""
    c = np.array([_corr(z3[k], ref) for k in range(3)])          # (channel, phase)
    scores = []
    for o in ORDERS:
        vals = c[np.arange(3), list(o)]
        scores.append(np.nan if np.isnan(vals).any() else float(vals.mean()))
    s = np.array(scores)
    if np.isnan(s).all():
        return ORDERS[0], np.nan, np.nan, np.full(3, np.nan)
    rank = np.argsort(np.where(np.isnan(s), -np.inf, s))[::-1]
    best = ORDERS[int(rank[0])]
    return best, float(s[rank[0]]), float(s[rank[1]]), c[np.arange(3), list(best)]


# ---------------------------------------------------------------- the identification

def _service(cube: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per meter: how many voltage columns it fills, and the column of a single-phase one."""
    filled = np.isfinite(cube).mean(axis=2) >= MIN_FILLED          # (M, 3): a column with a real share
    n = filled.sum(axis=1)
    channel = np.where(n == 1, filled.argmax(axis=1), -1)
    return n, channel


def identify(volt: pd.DataFrame, reference: pd.DataFrame | None = None, threshold: float | None = None,
             pv_phase: pd.Series | None = None) -> tuple[pd.DataFrame, Report]:
    """Each meter's phase (single-phase) or phase order (three-phase), with its confidence and the
    correlations behind it, and the check report. `volt`: customer_id, start, v_l1..v_l3 at 15
    minutes; `reference`: start, v_l1..v_l3 of the transformer secondary meter, or None;
    `pv_phase`: existing_pv_phase by customer_id, to say where a three-phase meter's order matters."""
    from ..config import A

    threshold = float(A("phase_id_min_confidence")) if threshold is None else float(threshold)
    report = Report(title="Phase identification check")
    if volt is None or not len(volt) or not any(c in volt.columns for c in CHANNELS):
        report.add(NOTE, "no voltages", "phase_id_no_voltages")
        return pd.DataFrame(columns=OUT_COLUMNS), report
    grid = _grid(volt, reference)
    ids, cube = _cube(volt, grid)
    report.tables = {"meters": len(ids)}
    dv = _changes(cube)                                            # (M, 3, T-1)
    n_ch, channel = _service(cube)
    valid = np.isfinite(dv)
    moved = valid & (np.abs(dv) > 1e-9)

    rows = []
    for i, cid in enumerate(ids):
        k = [channel[i]] if n_ch[i] == 1 else list(range(3)) if n_ch[i] == 3 else []
        changes = int(valid[i, k].all(axis=0).sum()) if k else 0
        service = {1: "single", 3: "three"}.get(int(n_ch[i]), "")
        status = ""
        if not service:
            status = "channels"
        elif changes < MIN_CHANGES:
            status = "too_few"
        elif moved[i, k].any(axis=0).sum() < FLAT_SHARE * changes:
            status = "flat"
        rows.append(dict(customer_id=cid, service=service,
                         channel=CHANNELS[channel[i]] if service == "single" else "",
                         phase="", order="", confidence=np.nan, score=np.nan, second=np.nan,
                         corr_a=np.nan, corr_b=np.nan, corr_c=np.nan, changes=changes, status=status,
                         reference="", matters=False))
    out = pd.DataFrame(rows, columns=OUT_COLUMNS)
    usable = out["status"].eq("").to_numpy()
    single = np.flatnonzero(usable & out["service"].eq("single").to_numpy())
    three = np.flatnonzero(usable & out["service"].eq("three").to_numpy())

    # the reference: (3, T-1) differential changes and the common mode, from the station meter,
    # the three-phase meters or, failing both, groups of the single-phase ones
    ref = common = None
    source = ""
    rejected = -1                                                  # usable changes of a refused reference
    loo: dict[int, np.ndarray] = {}
    if reference is not None and len(reference):
        r = reference.set_index("start").reindex(grid)[list(CHANNELS)].to_numpy(dtype=float).T
        rd = _changes(r)
        rejected = int(np.isfinite(rd).all(axis=0).sum())
        if rejected >= MIN_CHANGES:
            with np.errstate(invalid="ignore"):
                common = np.nanmean(rd, axis=0)
            ref, source = _z(_differential(rd)), "station"
    if ref is None and len(three) >= MIN_REFERENCE_METERS:
        ref, common, loo = _from_three_phase(dv, three)
        source = "three_phase_meters"
    if ref is None and len(single) > 3:
        ref, common, loo = _from_clusters(dv, channel, single)
        source = "clusters"

    if ref is None:
        out.loc[usable, "status"] = "no_reference"
    else:
        out.loc[usable, "reference"] = source
        for i in single:
            x = _without(dv[i, channel[i]], common)
            r = loo.get(int(i), ref)
            c = _corr(x, r)
            if np.isnan(c).all():
                out.loc[i, "status"] = "flat"
                continue
            rank = np.argsort(np.where(np.isnan(c), -np.inf, c))[::-1]
            best, second = float(c[rank[0]]), float(c[rank[1]]) if np.isfinite(c[rank[1]]) else -1.0
            out.loc[i, ["corr_a", "corr_b", "corr_c"]] = c
            out.loc[i, ["score", "second", "confidence"]] = [best, second, best - second]
            ok = best > 0 and best - second >= threshold
            out.loc[i, "phase"] = PHASES[int(rank[0])] if ok else ""
            out.loc[i, "status"] = "identified" if ok else "low_confidence"
        for i in three:
            z3 = _z(_differential(dv[i]))
            order, best, second, diag = _best_order(z3, loo.get(int(i), ref))
            if np.isnan(best):
                out.loc[i, "status"] = "flat"
                continue
            out.loc[i, ["score", "second", "confidence"]] = [best, second, best - second]
            # corr_<phase>: the correlation of the column taken for that phase with the phase
            by_phase = np.full(3, np.nan)
            by_phase[list(order)] = diag
            out.loc[i, ["corr_a", "corr_b", "corr_c"]] = by_phase
            ok = best > 0 and best - second >= threshold
            out.loc[i, "order"] = "".join(PHASES[p] for p in order) if ok else ""
            out.loc[i, "status"] = "identified" if ok else "low_confidence"
    if pv_phase is not None:
        pv = pv_phase.astype(str).str.strip().str.lower()
        on_one = out["customer_id"].map(pv).isin(PHASES)
        out["matters"] = on_one & out["service"].eq("three") & out["order"].ne("") & out["order"].ne("abc")
    if rejected >= 0 and source != "station":
        report.add(WARNING, "reference", "phase_id_reference_unusable", rejected, usable=str(rejected), n=str(MIN_CHANGES))
    _report(out, report, source, threshold)
    return out, report


def _from_three_phase(dv: np.ndarray, three: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict]:
    """The reference from the three-phase meters: each one's differential changes aligned to the
    others' by its best order, the alignment repeated until no order changes, and the phases then
    named so that the order most of them share is a, b, c. Each meter is scored against the others
    only (`loo`), so it never confirms itself."""
    z = {int(i): _z(_differential(dv[i])) for i in three}
    seed = max(z, key=lambda i: int(np.isfinite(z[i]).all(axis=0).sum()))
    ref = z[seed]
    orders: dict[int, tuple] = {}
    for _ in range(10):
        new = {i: _best_order(zi, ref)[0] for i, zi in z.items()}
        S, N = _stack(z, new)
        with np.errstate(invalid="ignore"):
            ref = S / N
        if new == orders:
            break
        orders = new
    shared = max(set(orders.values()), key=lambda o: (sum(v == o for v in orders.values()), o == (0, 1, 2)))
    ref = ref[list(shared)]                       # the shared order becomes a, b, c
    inv = np.argsort(shared)
    orders = {i: tuple(int(inv[p]) for p in o) for i, o in orders.items()}
    S, N = _stack(z, orders)
    loo = {}
    for i, zi in z.items():
        own = _aligned(zi, orders[i])
        with np.errstate(invalid="ignore", divide="ignore"):
            loo[i] = (S - np.nan_to_num(own)) / (N - np.isfinite(own))
    with np.errstate(invalid="ignore"):
        common = np.nanmean(np.nanmean(dv[three], axis=1), axis=0)
    return S / N, common, loo


def _aligned(z3: np.ndarray, order: tuple) -> np.ndarray:
    a = np.full_like(z3, np.nan)
    a[list(order)] = z3
    return a


def _stack(z: dict, orders: dict) -> tuple[np.ndarray, np.ndarray]:
    first = next(iter(z.values()))
    S, N = np.zeros_like(first), np.zeros_like(first)
    for i, zi in z.items():
        a = _aligned(zi, orders[i])
        S += np.nan_to_num(a)
        N += np.isfinite(a)
    return S, N


def _from_clusters(dv: np.ndarray, channel: np.ndarray, single: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict]:
    """Three groups of single-phase meters by correlation (k-means on standardised changes, the
    common mode taken out first), named a, b and c by size; each meter scored against its group
    without itself."""
    x = np.array([dv[i, channel[i]] for i in single])
    with np.errstate(invalid="ignore"):
        common = np.nanmean(x, axis=0)
    z = _z(np.array([_without(xi, common) for xi in x]))
    n = np.isfinite(z).sum(axis=1)
    cent = [int(np.argmax(n))]
    while len(cent) < 3:
        sims = np.array([[_corr(z[j], z[[c]])[0] for c in cent] for j in range(len(z))])
        sims = np.where(np.isnan(sims), np.inf, sims).max(axis=1)
        sims[cent] = np.inf
        cent.append(int(np.argmin(sims)))
    C = z[cent]
    label = np.full(len(z), -1)
    for _ in range(30):
        new = np.array([int(np.nanargmax(np.nan_to_num(_corr(zj, C), nan=-2.0))) for zj in z])
        if (new == label).all():
            break
        label = new
        C = np.array([np.nanmean(z[label == k], axis=0) if (label == k).any() else C[k] for k in range(3)])
    sizes = np.array([(label == k).sum() for k in range(3)])
    rank = np.argsort(-sizes, kind="stable")
    S = np.array([np.nansum(z[label == k], axis=0) for k in rank])
    N = np.array([np.isfinite(z[label == k]).sum(axis=0) for k in rank]).astype(float)
    where = {int(k): r for r, k in enumerate(rank)}
    loo = {}
    for j, i in enumerate(single):
        Sj, Nj = S.copy(), N.copy()
        r = where[int(label[j])]
        Sj[r] -= np.nan_to_num(z[j])
        Nj[r] -= np.isfinite(z[j])
        with np.errstate(invalid="ignore", divide="ignore"):
            loo[int(i)] = Sj / Nj
    with np.errstate(invalid="ignore", divide="ignore"):
        return S / N, common, loo


def _first(out: pd.DataFrame, mask) -> str:
    return str(out.loc[mask, "customer_id"].iloc[0])


def _report(out: pd.DataFrame, report: Report, source: str, threshold: float) -> None:
    single, three = out["service"].eq("single"), out["service"].eq("three")
    if source == "station":
        report.add(NOTE, "reference", "phase_id_reference_station")
    elif source == "three_phase_meters":
        report.add(WARNING, "reference", "phase_id_reference_meters")
    elif source == "clusters":
        report.add(WARNING, "reference", "phase_id_reference_clusters")
    elif (out["status"] == "no_reference").any():
        report.add(WARNING, "reference", "phase_id_no_reference", int((out["status"] == "no_reference").sum()))
    ident = out["status"].eq("identified")
    if (single & ident).any():
        report.add(NOTE, "identified", "phase_id_identified", int((single & ident).sum()),
                   threshold=f"{threshold:g}")
    if (three & ident).any():
        report.add(NOTE, "identified", "phase_id_order_identified", int((three & ident).sum()))
    low = out["status"].eq("low_confidence")
    if low.any():
        report.add(WARNING, "confidence", "phase_id_low_confidence", int(low.sum()),
                   threshold=f"{threshold:g}", meter=_first(out, low))
    for status, code in (("flat", "phase_id_flat"), ("too_few", "phase_id_too_few"), ("channels", "phase_id_channels")):
        m = out["status"].eq(status)
        if m.any():
            extra = {"n": str(MIN_CHANGES)} if status == "too_few" else {}
            report.add(WARNING, status, code, int(m.sum()), meter=_first(out, m), **extra)
    other = three & ident & out["order"].ne("abc")
    if other.any():
        ex = out.loc[other].iloc[0]
        report.add(NOTE, "phase order", "phase_id_order", int(other.sum()), meter=str(ex["customer_id"]),
                   order=str(ex["order"]))
    if out["matters"].any():
        report.add(WARNING, "phase order", "phase_id_order_matters", int(out["matters"].sum()),
                   meter=_first(out, out["matters"]))


# ---------------------------------------------------------------- writing and applying

def write(out: pd.DataFrame, report: Report, folder: str | Path) -> Path:
    """phase_id.parquet and its check report, phase_id.json, in `folder` (processed/meters)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "phase_id.parquet"
    out.to_parquet(path, index=False)
    summary = {"meters": int(len(out)),
               "by_status": {str(k): int(v) for k, v in out["status"].value_counts().sort_index().items()},
               "reference": next((r for r in out["reference"] if r), "")}
    (folder / "phase_id.json").write_text(json.dumps({"schema": "kapacitet.phase_id/1", **summary,
                                                      "report": report.to_dict()}, indent=1), encoding="utf-8")
    return path


def apply(customers: pd.DataFrame, ident: pd.DataFrame, prefer_identified: bool = False,
          report: Report | None = None) -> tuple[pd.DataFrame, dict]:
    """customers (processed/grid/customers.parquet, `osm_id` the customer_id and `phase` the recorded
    phase, '' for none) with the identified single-phase phases written in: where none was recorded,
    and where another was only with `prefer_identified`. Returns (the customers, the counts); a
    conflict goes into `report` when one is given."""
    out = customers.copy()
    if "phase" not in out.columns:
        out["phase"] = ""
    recorded = out["phase"].fillna("").astype(str).str.strip().str.lower()
    relative = "reference" in ident.columns and bool((set(ident["reference"]) - {"", "station"}))
    ok = ident.loc[ident["status"].eq("identified") & ident["service"].eq("single") & ident["phase"].isin(PHASES)]
    found = out["osm_id"].astype(str).map(ok.set_index("customer_id")["phase"]).fillna("")
    have = found != ""
    blank = have & (recorded == "")
    agree = have & (recorded == found)
    conflict = have & (recorded != "") & (recorded != found)
    write_mask = (blank | (conflict if prefer_identified else False)) & (not relative)
    out.loc[write_mask, "phase"] = found[write_mask]
    counts = {"identified": int(have.sum()), "written": int(write_mask.sum()), "agree": int(agree.sum()),
              "conflict": int(conflict.sum()), "prefer_identified": bool(prefer_identified),
              "applied": not relative}
    if report is not None and relative and have.any():
        report.add(WARNING, "recorded phase", "phase_id_not_applied", int(have.sum()))
    if report is not None and conflict.any():
        j = int(np.flatnonzero(conflict.to_numpy())[0])
        code = "phase_id_conflict_identified" if prefer_identified else "phase_id_conflict_recorded"
        report.add(WARNING, "recorded phase", code, int(conflict.sum()), customer=str(out["osm_id"].iloc[j]),
                   recorded=str(recorded.iloc[j]), identified=str(found.iloc[j]))
    return out, counts
