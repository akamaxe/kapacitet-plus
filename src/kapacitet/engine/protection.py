"""Protection: would a fault at the far end of each feeder, and at each house, blow its fuse in time?

The check every LV planner makes and the README listed as missing. For each feeder:

1. the head fuse is the largest standard gG rating the main cable allows: I_n <= I_z, and the
   conventional fusing current 1.6·I_n within 1.45·I_z (IEC 60364-4-43, 433.1), where I_z is the
   cable's rating times its parallel runs;
2. the minimum single-phase fault current along the feeder (pandapower `calc_sc`, IEC 60909, case
   "min", conductors at their end-of-fault temperature) must reach the current at which that fuse
   is certain to melt within the permitted disconnection time, 5 s for a distribution circuit in a
   TN system (IEC 60364-4-41, 411.3.2.3): the "Imax at 5 s" gate of IEC 60269 for gG fuse-links.

Each house's main fuse is held to the same rule at its service point. The gate table starts at
25 A; a 16 or 20 A fuse melts sooner at any current than a 25 A one, so the 25 A gate is a bound
that proves them too (and a house below it reads "not proven", never "fails").

Not modelled: the cabinet fuses of the service cables, and what inverters add to a fault (they are
current-limited and switch off). Run with `python -m kapacitet compute --stage extra --only
protection`; it writes results/protection.json in well under a second.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ..config import A

log = logging.getLogger(__name__)


def prepare(net) -> None:
    """The minimum-case data calc_sc needs, from the model inputs (the zero sequence is already set
    by grid.builder.add_zero_sequence). Behind the Dyn transformer the MV zero sequence does not
    reach an LV fault, so the source's zero-sequence ratios only satisfy the solver."""
    net.line["endtemp_degree"] = float(A("line_sc_end_temperature_c"))
    net.ext_grid["s_sc_min_mva"] = float(A("mv_short_circuit_mva")) * float(A("mv_short_circuit_min_factor"))
    net.ext_grid["rx_min"] = net.ext_grid.get("rx_max", 0.1)
    net.ext_grid["x0x_min"] = net.ext_grid.get("x0x_max", 1.0)
    net.ext_grid["r0x0_min"] = net.ext_grid.get("r0x0_max", 0.1)


def fault_currents(net) -> pd.Series:
    """Minimum single-phase fault current at every bus, amperes (pandapower bus index)."""
    import pandapower.shortcircuit as sc

    prepare(net)
    sc.calc_sc(net, fault="1ph", case="min", ip=False, ith=False, branch_results=False)
    return net.res_bus_sc["ikss_ka"] * 1000.0


def feeder_fuse(i_z_a: float, ratings: list[float], conventional: float, overload: float) -> float | None:
    """The largest standard rating with I_n <= I_z and conventional·I_n <= overload·I_z, or None."""
    fits = [r for r in sorted(ratings) if r <= i_z_a + 1e-9 and conventional * r <= overload * i_z_a + 1e-9]
    return float(fits[-1]) if fits else None


def gate_5s(rating_a: float, ratings: list[float], imax_5s: list[float]) -> tuple[float, bool]:
    """The current at which a gG fuse of this rating melts within 5 s, and whether that is a bound:
    a rating the table does not list takes the next larger row, which it melts before (16 and
    20 A take 25 A; 35 A takes 40 A). Above the table there is nothing to bound it with."""
    table = dict(zip([float(r) for r in ratings], [float(i) for i in imax_5s]))
    rating_a = float(rating_a)
    if rating_a in table:
        return table[rating_a], False
    larger = [r for r in sorted(table) if r > rating_a]
    if larger:
        return table[larger[0]], True
    raise ValueError(f"no gG gate for a {rating_a:g} A fuse")


def assess(net, lines: pd.DataFrame, cust: pd.DataFrame, ik: pd.Series | None = None) -> dict:
    """Every feeder's head fuse against its weakest point, and every house's main fuse."""
    ik = fault_currents(net) if ik is None else ik
    ratings, imax = list(A("fuse_gg_ratings_a")), list(A("fuse_gg_imax_5s_a"))
    conv, over = float(A("fuse_conventional_factor")), float(A("cable_overload_factor"))
    rating_ka = net.line["max_i_ka"] * net.line["parallel"]

    feeders = []
    for f, group in lines[lines["kind"] == "main"].groupby("feeder"):
        i_z = float(rating_ka.loc[group["line"]].min()) * 1000.0
        fuse = feeder_fuse(i_z, ratings, conv, over)
        buses = pd.unique(group[["from_bus", "to_bus"]].to_numpy().ravel())
        currents = ik.reindex(buses).dropna()
        weakest_bus = int(currents.idxmin())
        ik_min = float(currents.min())
        gate = gate_5s(fuse, ratings, imax)[0] if fuse is not None else None
        types = sorted(set(net.line.loc[group["line"], "std_type"].astype(str)))
        feeders.append({
            "feeder": int(f), "cable": ", ".join(types), "i_z_a": round(i_z, 1), "fuse_a": fuse,
            "weakest_bus": weakest_bus, "ik1_min_a": round(ik_min, 1), "gate_5s_a": gate,
            "ok": bool(gate is not None and ik_min >= gate), "margin": round(ik_min / gate, 3) if gate else None,
        })

    houses = []
    for row in cust.itertuples():
        ikh = float(ik.get(int(row.bus), np.nan))
        gate, bound = gate_5s(float(row.fuse_a), ratings, imax)
        houses.append({"cid": int(row.cid), "bus": int(row.bus), "main_fuse_a": int(row.fuse_a),
                       "ik1_min_a": round(ikh, 1), "gate_5s_a": gate, "bound": bool(bound),
                       "ok": bool(ikh >= gate)})

    return {
        "criterion": {"disconnect_s": float(A("protection_max_disconnect_s")), "fault": "single-phase to earth",
                      "case": "minimum (IEC 60909)", "end_temperature_c": float(A("line_sc_end_temperature_c")),
                      "conventional_factor": conv, "overload_factor": over},
        "feeders": feeders,
        "houses": houses,
        "summary": {"feeders": len(feeders), "feeders_ok": sum(f["ok"] for f in feeders),
                    "houses": len(houses), "houses_ok": sum(h["ok"] for h in houses),
                    "ik1_min_a": round(float(min(h["ik1_min_a"] for h in houses)), 1) if houses else None,
                    "ik1_median_a": round(float(np.median([h["ik1_min_a"] for h in houses])), 1) if houses else None},
    }


def write() -> dict:
    """Compute and save results/protection.json for the built network."""
    import json

    from ..grid.builder import load_net, load_tables
    from ..paths import processed

    net = load_net()
    _, lines, cust = load_tables()
    out = assess(net, lines, cust)
    processed("results", "protection.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    s = out["summary"]
    log.info("protection: %d of %d feeders and %d of %d houses clear a fault within %.0f s",
             s["feeders_ok"], s["feeders"], s["houses_ok"], s["houses"], out["criterion"]["disconnect_s"])
    return out
