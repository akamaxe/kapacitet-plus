"""Write the built network out in the export contract.

Two uses. It gives an operator a worked example of what we are asking them for, filled with
a network they can open and compare against their own records. And it gives the test suite
a realistic fixture, so the contract, the validator and the importer are exercised on 165
buses and 120 services rather than on a toy.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..grid.builder import load_net, load_tables
from .contract import TABLES

KIND = {"customer": "service", "cabinet": "cabinet", "junction": "cabinet",
        "lv_busbar": "substation", "mv": "substation"}


def export_grid(directory: str | Path, include_phase: bool = True) -> dict[str, pd.DataFrame]:
    """Write buses.csv, lines.csv, transformer.csv and customers.csv into `directory`."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    net = load_net()
    buses, lines, cust = load_tables()
    # The contract describes the low-voltage network only: the transformer is declared by
    # its LV busbar, so the medium-voltage bus has no row of its own.
    buses = buses[buses["kind"] != "mv"].reset_index(drop=True)

    out = {
        "buses": pd.DataFrame({
            "bus_id": buses["bus"],
            "kind": buses["kind"].map(KIND).fillna("cabinet"),
            "name": buses["name"],
            "lon": buses["lon"],
            "lat": buses["lat"],
        }),
        "lines": pd.DataFrame({
            "line_id": lines["line"],
            "from_bus": lines["from_bus"],
            "to_bus": lines["to_bus"],
            "length_m": lines["length_m"],
            "std_type": lines["std_type"],
            "parallel": lines["parallel"],
        }),
        "transformer": _transformer(net, buses),
        "customers": _customers(cust, include_phase),
    }
    for table in TABLES:
        out[table.name].to_csv(directory / f"{table.name}.csv", index=False)
    (directory / "README.md").write_text(_readme(), encoding="utf-8")
    return out


def _transformer(net, buses: pd.DataFrame) -> pd.DataFrame:
    tr = net.trafo.iloc[0]
    lv = buses.loc[buses["kind"] == "lv_busbar", "bus"]
    return pd.DataFrame([{
        "lv_bus": int(lv.iloc[0]) if len(lv) else int(tr["lv_bus"]),
        "sn_mva": float(tr["sn_mva"]),
        "vn_hv_kv": float(tr["vn_hv_kv"]),
        "vn_lv_kv": float(tr["vn_lv_kv"]),
        "vk_percent": float(tr["vk_percent"]),
        "vkr_percent": float(tr["vkr_percent"]),
        "vector_group": "Dyn",
        "tap_pos": int(tr["tap_pos"]) if pd.notna(tr["tap_pos"]) else 0,
    }])


def _customers(cust: pd.DataFrame, include_phase: bool) -> pd.DataFrame:
    from ..engine.phases import PHASE_NAMES, THREE_PHASE, phase_of
    from ..engine.netmodel3ph import inverter_kw

    out = pd.DataFrame({
        "customer_id": cust["cid"],
        "bus": cust["bus"],
        "fuse_a": cust["fuse_a"],
        "footprint_m2": cust["footprint_m2"],
    })
    if include_phase:
        phase = phase_of(cust, inverter_kw())
        out["phase"] = [PHASE_NAMES[p] if p != THREE_PHASE else None for p in phase]
    return out


def _readme() -> str:
    from .contract import as_markdown
    return (as_markdown() + "\n"
            "This directory is the contract filled with the Smedslatten reference network, "
            "exported by `python -m kapacitet export-grid`. Replace the contents with your "
            "own and run `python -m kapacitet check <directory>`.\n")
