"""What a build assumes where an export says nothing, and how the project says it (task 1.2 of the
technical review of 14-11-2026).

In the review's walk through the workbench, twelve houses whose cables were named as a NIS names
them were built on the default cable and came out "Ready" without a word: the build counted the
substitution, and no screen read the count. The count now carries the type names it stood in for,
`datasets.summary` turns it into the project's assumptions, and /meta gives them to the frame.
"""

from __future__ import annotations

import json

import pandas as pd

from kapacitet import datasets as ds
from kapacitet.config import A
from kapacitet.importer import templates
from kapacitet.importer.build import build_from_export
from kapacitet.importer.validate import read_files

ELECTRICAL = ["r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km", "max_i_ka"]


def renamed_cables() -> dict[str, pd.DataFrame]:
    """An example network with every cable named as a NIS would name it, without r/x/c data."""
    data = read_files(templates.files("dickert-short"))
    lines = data["lines"].drop(columns=[c for c in ELECTRICAL if c in data["lines"]])
    lines["std_type"] = "N1XV 4x50 (NIS)"
    data["lines"] = lines
    return data


def test_the_build_names_the_types_it_built_as_the_default_cable():
    data = renamed_cables()
    network, applied = build_from_export(data)
    n = len(data["lines"])
    assert applied["cable type assumed"] == n
    assert network.summary["assumed_cable"] == {"type": A("service_cable_type"), "names": {"N1XV 4x50 (NIS)": n}}


def test_a_network_without_assumptions_says_none():
    network, applied = build_from_export(read_files(templates.files("dickert-short")))
    assert "cable type assumed" not in applied and "assumed_cable" not in network.summary
    assert [a for a in ds.assumptions(network.summary) if a["weighty"]] == []


def test_the_assumptions_count_against_what_they_touch_weighty_first():
    grid = {"lines": 12, "buses": 14, "customers": 12,
            "fallbacks": {"customer without a roof area": 3, "bus without coordinates": 2,
                          "cable type assumed": 12, "something new": 1, "transformer vk assumed": 0},
            "assumed_cable": {"type": "NAYY 4x50 SE", "names": {"N1XV 4x50 (NIS)": 10, "": 2}}}
    out = ds.assumptions(grid)
    assert [a["code"] for a in out] == ["cable_default", "bus_no_coordinates", "customer_no_roof", "other"]
    cable = out[0]
    assert cable == {"code": "cable_default", "count": 12, "total": 12, "weighty": True, "cable": "NAYY 4x50 SE",
                     "names": {"N1XV 4x50 (NIS)": 10, "": 2}}
    assert out[1]["total"] == 13                       # the medium-voltage bus is not on the map
    assert out[3] == {"code": "other", "count": 1, "total": None, "weighty": False, "label": "something new"}


def test_the_project_list_carries_them(tmp_path):
    grid = tmp_path / "processed" / "grid"
    grid.mkdir(parents=True)
    (grid / "summary.json").write_text(json.dumps({"customers": 12, "lines": 12, "buses": 14,
                                                   "fallbacks": {"cable type assumed": 12}}), encoding="utf-8")
    summary = ds.summary(tmp_path)
    assert summary["assumptions"] == [{"code": "cable_default", "count": 12, "total": 12, "weighty": True,
                                       "cable": "", "names": {}}]
    assert ds.summary(tmp_path / "nothing")["assumptions"] == []
