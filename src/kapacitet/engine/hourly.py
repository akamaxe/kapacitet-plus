"""The hourly study's deployment, for the map and the capacity screen.

Where the envelopes are off (kapacitet/features.py), the capacity screen shows the hourly study's
own answer: how many houses of an adoption order connect, firm, with no limit exceeded in any hour
of any weather year. This module takes the order whose hourly capacity is the median, as the
showcase takes the envelopes' median, and writes what the screens read of its deployment:

- which houses the static study connects and which the hourly study adds;
- the reference year on the map, the same houses with no control (`state_hourly`, the contract of
  engine/stateblob.py), and its verification: the hours above a limit (none, by the search's own
  construction), the highest voltage and the highest loadings; and the static study's houses of the
  same order (`state_hourly_static`), so the map's two scenarios are one order seen two ways;
- what the next house of the order would break, in the first weather year it breaks anything: the
  transformer, a cable or the voltage, and in how many hours (`next`). That is the limit that binds.

A build writes it with the capacity (results/hourly.json, ui/state_hourly.*). A network computed
before this module has only hosting_summary.json: the server computes the deployment the first time
it is asked and keeps it in data/requests/cache/hourly/, where the API writes, never in
data/processed.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..atomic import write_bytes_atomic, write_json_atomic
from ..config import settings
from ..timeindex import year_index


def pick(summary: dict) -> dict | None:
    """The adoption order whose hourly capacity is the median of hosting_summary.json's orders, with
    how many houses the static and the hourly study connect in it; None without orders."""
    per = summary.get("per_order") or []
    if not per:
        return None
    kw = [float(r["timeseries"]["kw_ac"]) for r in per]
    r = per[sorted(range(len(per)), key=kw.__getitem__)[len(per) // 2]]
    return {"order": int(r["order"]), "static": int(r["static"]["adopters"]),
            "timeseries": int(r["timeseries"]["adopters"])}


def _counts(v, hours: np.ndarray | None = None) -> dict[str, int]:
    """Hours of each export-side limit broken (a transformer, a cable, the voltage)."""
    def n(mask) -> int:
        mask = np.asarray(mask, dtype=bool)
        return int(mask.sum())
    return {"trafo": n(v.trafo_rev), "line": n(v.line_rev), "voltage": n(v.over_v)}


def next_house(model, order: np.ndarray, k: int, years, hp: np.ndarray, ev: np.ndarray) -> dict:
    """What the (k+1)-th house of `order` would break, in the first of `years` it breaks anything.

    The linear screen is conservative, so the hours it does not name break nothing, and only the
    hours it names are run through the nonlinear flow. Every house fitting is said as such."""
    from .run import pv_mask
    from .scenario import Scenario

    C = model.C
    if k >= len(order):
        return {"fits_all": True}
    mask = pv_mask(order, k + 1, C)
    for y in years:
        arr = Scenario(year=int(y), pv=mask, hp=hp, ev=ev).arrays()
        hours = np.asarray(model.screen_export(arr["p_net"], arr["q"]), dtype=int)
        if not len(hours):
            continue
        p, q = arr["p_net"][:, hours], arr["q"][:, hours]
        v = model.violations(model.solve(p, q), p)
        broken = np.asarray(v.export_side, dtype=bool)
        if broken.any():
            counts = _counts(v)
            binding = max(counts, key=counts.get)
            return {"fits_all": False, "adopters": k + 1, "year": int(y), "hours": int(broken.sum()),
                    "binding": binding, "counts": counts,
                    "max_voltage_pu": float(np.max(v.vmax)), "max_trafo_loading_pct": float(np.max(v.trafo_loading))}
    # the search found k the largest that fits every year, so k+1 breaks one; a screen this
    # optimistic would be a fault of the screen, said rather than hidden
    return {"fits_all": False, "adopters": k + 1, "year": None, "hours": 0, "binding": None, "counts": {}}


def compute(summary: dict) -> tuple[dict, tuple]:
    """(the document, the map's state) for the deployment `pick(summary)` names."""
    from . import doe
    from .run import background_masks, model, pv_mask
    from .scenario import Scenario, adoption_order, customers

    sel = pick(summary)
    if sel is None:
        raise ValueError("hosting_summary.json has no adoption orders")
    cfg = settings()
    y = cfg.reference_year
    m = model()
    C = m.C
    hp, ev = background_masks(C)
    order = adoption_order(sel["order"], C)
    ms, mts = sel["static"], sel["timeseries"]
    arr = Scenario(year=y, pv=pv_mask(order, mts, C), hp=hp, ev=ev).arrays()
    res = m.solve(arr["p_net"], arr["q"])
    v = m.violations(res, arr["p_net"])
    arr_s = Scenario(year=y, pv=pv_mask(order, ms, C), hp=hp, ev=ev).arrays()
    res_s = m.solve(arr_s["p_net"], arr_s["q"])
    v_s = m.violations(res_s, arr_s["p_net"])
    cids = customers()["cid"].to_numpy()
    caps = doe.customer_caps()
    years = summary.get("years") or list(cfg.weather_years)
    doc = {
        "year": y, "order": sel["order"], "customers": C, "years": list(years),
        "static_adopters": ms, "timeseries_adopters": mts,
        "static_cids": cids[order[:ms]].tolist(), "hourly_cids": cids[order[ms:mts]].tolist(),
        "static_kw_ac": float(caps[order[:ms]].sum()), "timeseries_kw_ac": float(caps[order[:mts]].sum()),
        "verification": {
            "hours": len(year_index(y)),
            "violation_hours": int(np.asarray(v.export_side, dtype=bool).sum()),
            "counts": _counts(v),
            "max_voltage_pu": float(np.max(v.vmax)),
            "max_trafo_loading_pct": float(np.max(v.trafo_loading)),
            "max_line_loading_pct": float(np.max(v.max_line_loading)),
        },
        "static_violation_hours": int(np.asarray(v_s.export_side, dtype=bool).sum()),
        "next": next_house(m, order, mts, years, hp, ev),
    }
    return doc, ((res, v, arr), (res_s, v_s, arr_s))


def write(doc: dict, state: tuple, folder: Path | None = None) -> dict[str, Path]:
    """The document and the map's state: in results/ and ui/ of the data folder by default, or all
    three files in `folder` (the server's cache). Returns where each went."""
    from ..paths import processed
    from .run import _default, state_blob

    (res, v, arr), (res_s, v_s, arr_s) = state
    blobs = {"hourly": state_blob("hourly", res, v, arr["p_net"], arr["pv"], arr["load"], None),
             "hourly_static": state_blob("hourly_static", res_s, v_s, arr_s["p_net"], arr_s["pv"], arr_s["load"], None)}
    if folder is None:
        where = {"doc": processed("results", "hourly.json")}
        for name in blobs:
            where[f"{name}.json"] = processed("ui", f"state_{name}.json")
            where[f"{name}.bin"] = processed("ui", f"state_{name}.bin")
    else:
        folder.mkdir(parents=True, exist_ok=True)
        where = {"doc": folder / "hourly.json"}
        for name in blobs:
            where[f"{name}.json"] = folder / f"state_{name}.json"
            where[f"{name}.bin"] = folder / f"state_{name}.bin"
    for name, (header, blob) in blobs.items():
        write_bytes_atomic(where[f"{name}.bin"], blob)
        write_json_atomic(where[f"{name}.json"], header, indent=None, default=_default)
    write_json_atomic(where["doc"], doc, default=_default)
    return where
