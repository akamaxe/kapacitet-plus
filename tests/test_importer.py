"""The export contract and the data-quality report an operator gets back.

Most of this runs on a four-bus export written here, so the checks are exercised without
the pipeline. The round trip on the real network is marked `needs_grid`.
"""

from __future__ import annotations

import pandas as pd
import pytest
from conftest import needs_grid

from kapacitet.importer import as_markdown, validate
from kapacitet.importer.validate import ERROR, WARNING, check_directory


def good() -> dict[str, pd.DataFrame]:
    """Substation, one cabinet, two service points, two customers."""
    return {
        "buses": pd.DataFrame({
            "bus_id": ["SS1", "K1", "S1", "S2"],
            "kind": ["substation", "cabinet", "service", "service"],
            "name": ["Nätstation", "Kabelskåp 1", "Villa 1", "Villa 2"],
            "lon": [18.0, 18.001, 18.002, 18.003],
            "lat": [59.0, 59.001, 59.002, 59.003],
        }),
        "lines": pd.DataFrame({
            "line_id": ["L1", "L2", "L3"],
            "from_bus": ["SS1", "K1", "K1"],
            "to_bus": ["K1", "S1", "S2"],
            "length_m": [120.0, 30.0, 35.0],
            "std_type": ["NAYY 4x150 SE", "NAYY 4x50 SE", "NAYY 4x50 SE"],
            "parallel": [1, 1, 1],
        }),
        "transformer": pd.DataFrame([{
            "lv_bus": "SS1", "sn_mva": 0.4, "vn_hv_kv": 10.0, "vn_lv_kv": 0.4,
            "vk_percent": 4.0, "vkr_percent": 1.325, "vector_group": "Dyn", "tap_pos": 0,
        }]),
        "customers": pd.DataFrame({
            "customer_id": ["C1", "C2"],
            "bus": ["S1", "S2"],
            "fuse_a": [25, 20],
            "phase": [None, "a"],
            "footprint_m2": [120.0, 95.0],
            "annual_kwh": [15000.0, 12000.0],
        }),
    }


def findings(report, severity=None, check=None):
    return [f for f in report.findings
            if (severity is None or f.severity == severity)
            and (check is None or f.check == check)]


def test_a_complete_export_passes_clean():
    report = validate(good())
    assert report.usable and report.score == 100
    assert not findings(report, ERROR) and not findings(report, WARNING)
    # notes are allowed: here, that one of the two services is three-phase
    assert all(f.severity == "note" for f in report.findings)


def test_a_missing_file_is_an_error():
    data = good()
    del data["transformer"]
    report = validate(data)
    assert not report.usable
    assert findings(report, ERROR, "missing file")


def test_a_missing_required_column_is_an_error():
    data = good()
    data["lines"] = data["lines"].drop(columns=["length_m"])
    assert findings(validate(data), ERROR, "missing column")


def test_duplicate_identifiers_are_caught():
    data = good()
    data["buses"].loc[3, "bus_id"] = "S1"
    assert findings(validate(data), ERROR, "duplicate identifier")


def test_a_cable_to_nowhere_is_caught():
    data = good()
    data["lines"].loc[2, "to_bus"] = "S99"
    assert findings(validate(data), ERROR, "dangling cable")


def test_an_unreachable_bus_is_caught():
    data = good()
    data["lines"] = data["lines"].iloc[:2]          # S2 loses its cable
    assert findings(validate(data), ERROR, "island")


def test_a_ring_is_reported_as_meshed():
    data = good()
    data["lines"].loc[3] = ["L4", "S1", "S2", 20.0, "NAYY 4x50 SE", 1]
    assert findings(validate(data), WARNING, "meshed network")


def test_an_orphan_customer_is_caught():
    data = good()
    data["customers"].loc[1, "bus"] = "S99"
    assert findings(validate(data), ERROR, "orphan customer")


def test_an_unknown_phase_label_is_caught():
    data = good()
    data["customers"].loc[1, "phase"] = "red"
    assert findings(validate(data), ERROR, "phase value")


@pytest.mark.parametrize("length,check", [(0.0, ERROR), (-5.0, ERROR)])
def test_impossible_cable_lengths_are_errors(length, check):
    data = good()
    data["lines"].loc[0, "length_m"] = length
    assert findings(validate(data), check, "cable length")


def test_a_kilometre_long_low_voltage_section_is_suspicious():
    data = good()
    data["lines"].loc[0, "length_m"] = 4000.0
    assert findings(validate(data), WARNING, "cable length")


def test_missing_phases_are_a_warning_with_the_cost_spelled_out():
    data = good()
    data["customers"] = data["customers"].drop(columns=["phase"])
    found = findings(validate(data), WARNING, "phase not recorded")
    assert found and "hosting capacity" in found[0].message
    assert found[0].count == 2


def test_missing_coordinates_cost_the_map_but_not_the_answer():
    data = good()
    data["buses"]["lon"] = None
    report = validate(data)
    assert report.usable
    assert findings(report, WARNING, "no coordinates")


def test_a_non_distribution_transformer_is_rejected():
    data = good()
    data["transformer"].loc[0, "sn_mva"] = 40.0
    assert findings(validate(data), ERROR, "transformer rating")


def test_the_contract_is_documented():
    doc = as_markdown()
    for table in ("buses", "lines", "transformer", "customers"):
        assert f"`{table}.csv`" in doc
    assert "if missing" in doc


def test_an_empty_directory_says_so(tmp_path):
    report = check_directory(tmp_path)
    assert not report.usable and findings(report, ERROR, "empty export")


@needs_grid
def test_the_reference_network_round_trips_through_the_contract(tmp_path):
    """Our own grid, written out in the contract, has to come back clean."""
    from kapacitet.importer import export_grid

    tables = export_grid(tmp_path)
    assert len(tables["buses"]) == 164 and len(tables["customers"]) == 120
    assert "mv" not in set(tables["buses"]["kind"])

    report = check_directory(tmp_path)
    assert report.usable, report.text()
    assert not findings(report, ERROR)
    # only the metered-consumption column is genuinely absent from a synthetic network
    assert {f.check for f in findings(report, WARNING)} == {"annual_kwh missing"}
    assert (tmp_path / "README.md").exists()


@needs_grid
def test_the_contract_round_trips_electrically(tmp_path):
    """Export, import, and the network has to be the same one, not merely a similar one.

    If the imported network is electrically identical then every stage after it, the
    profiles, the capacity, the envelopes, gives the same answer by construction, which is
    the only way to claim the engine runs on someone else's records.
    """
    import numpy as np

    from kapacitet.engine.powerflow import PowerFlowModel
    from kapacitet.grid.builder import load_net, load_tables
    from kapacitet.importer import export_grid
    from kapacitet.importer.build import build_from_export
    from kapacitet.importer.validate import read_export

    export_grid(tmp_path)
    imported, fallbacks = build_from_export(read_export(tmp_path))
    assert fallbacks == {}, "a complete export must need no fallback"

    original_cust = load_tables()[2]
    a = PowerFlowModel(load_net(), original_cust["bus"].to_numpy())
    b = PowerFlowModel(imported.net, imported.customers["bus"].to_numpy())
    rng = np.random.default_rng(12)
    p = rng.uniform(-6.0, 9.0, size=(len(original_cust), 6))
    q = -0.05 * p
    ra, rb = a.solve(p, q, 1.0), b.solve(p, q, 1.0)

    assert np.abs(np.abs(ra.v[a.customer_bus]) - np.abs(rb.v[b.customer_bus])).max() < 1e-9
    assert np.abs(ra.trafo_loading - rb.trafo_loading).max() < 1e-9
    assert np.abs(np.sort(ra.line_loading, 0) - np.sort(rb.line_loading, 0)).max() < 1e-6


@needs_grid
def test_fallbacks_are_counted_not_hidden(tmp_path):
    from kapacitet.importer import export_grid
    from kapacitet.importer.build import build_from_export
    from kapacitet.importer.validate import read_export

    export_grid(tmp_path)
    lines = pd.read_csv(tmp_path / "lines.csv")
    lines.loc[:4, "std_type"] = None
    lines.to_csv(tmp_path / "lines.csv", index=False)

    _, fallbacks = build_from_export(read_export(tmp_path))
    assert fallbacks.get("cable type assumed") == 5


def test_an_unusable_export_refuses_to_import(tmp_path):
    from kapacitet.importer.build import ImportError_, import_directory

    data = good()
    data["customers"].loc[1, "bus"] = "S99"
    for name, df in data.items():
        df.to_csv(tmp_path / f"{name}.csv", index=False)
    with pytest.raises(ImportError_, match="orphan customer"):
        import_directory(tmp_path, save_to_disk=False)


def test_the_published_contract_document_matches_the_code():
    """docs/network-export-contract.md is what we send an operator. It has to be current."""
    from pathlib import Path

    doc = Path(__file__).resolve().parents[1] / "docs" / "network-export-contract.md"
    assert doc.exists(), "the contract document is missing"
    body = doc.read_text(encoding="utf-8")
    assert body.endswith(as_markdown()), (
        "docs/network-export-contract.md is out of date; regenerate it from "
        "kapacitet.importer.as_markdown()"
    )
