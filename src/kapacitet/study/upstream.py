"""The upstream caps of a study: what the grid above a station leaves for it, and the figure
published once they are applied.

    inputs/upstream.csv    level (feeder, substation or regional), id, parent_id, gen_headroom_kw,
                           load_headroom_kw, source, as_of: one row per node above the stations; a
                           feeder's parent is a substation, a substation's a regional grid
    inputs/stations.csv    station_id, feeder_id, hidden, zone (beside name and, for CGMES, trafo_mrid);
                           zone is the DSO's own area name, which a publication at the "zone" level
                           of [disclosure] groups by (study/disclosure.py)

A station's chain is its feeder and every parent above it. The headroom of a node is shared by every
station under it (`shared_by`), so it is not added up: the published value of a station is the
minimum of its own capacity and each level's headroom, with the level that set it (`limited_by`)
and how many stations, this one included, stand under that level (`shared_by_stations`): two
stations under a feeder with 150 kW left each show 150 kW, and the count says it is the same 150.
A blank headroom is "not entered" and never guessed: the level is listed as such, takes no part in
the minimum, and the published figure says the chain is incomplete. A station without a feeder, or
a parent that is not in the file, is a finding of the chain.

The caps apply after the computation (study.NOT_A_TRIGGER): a change of upstream.csv builds no
station again, and `study collect` applies the current file to every station's figures.
"""

from __future__ import annotations

import csv
from pathlib import Path

from . import Study

LEVELS = ("feeder", "substation", "regional")
COLUMNS = ("level", "id", "parent_id", "gen_headroom_kw", "load_headroom_kw", "source", "as_of")
SIDES = {"generation": "gen_headroom_kw", "consumption": "load_headroom_kw"}


def _rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;") if sample.strip() else csv.excel
        return [{str(k).strip().lower(): (v or "").strip() for k, v in r.items() if k is not None}
                for r in csv.DictReader(fh, dialect=dialect)]


def _kw(text: str) -> float | None:
    """A headroom in kW, or None when blank ("not entered"); an unreadable one raises."""
    if text is None or not str(text).strip():
        return None
    return float(str(text).strip().replace(" ", "").replace(",", "."))


def read(study: Study) -> dict:
    """{"nodes": {id: row}, "stations": {sid: {"feeder_id", "hidden", "zone"}}, "findings": [...]}."""
    findings: list[str] = []
    nodes: dict[str, dict] = {}
    for i, r in enumerate(_rows(study.inputs / "upstream.csv"), start=2):
        missing = [c for c in ("level", "id") if not r.get(c)]
        if missing:
            findings.append(f"upstream.csv line {i}: no {' or '.join(missing)}")
            continue
        level = r["level"].lower()
        if level not in LEVELS:
            findings.append(f"upstream.csv line {i}: level {r['level']!r} is not one of {', '.join(LEVELS)}")
            continue
        try:
            gen, load = _kw(r.get("gen_headroom_kw")), _kw(r.get("load_headroom_kw"))
        except ValueError:
            findings.append(f"upstream.csv line {i}: a headroom that is not a number")
            continue
        if r["id"] in nodes:
            findings.append(f"upstream.csv line {i}: {r['id']} twice; the first row is kept")
            continue
        nodes[r["id"]] = {"level": level, "id": r["id"], "parent_id": r.get("parent_id") or None,
                          "gen_headroom_kw": gen, "load_headroom_kw": load,
                          "source": r.get("source") or None, "as_of": r.get("as_of") or None}
    stations = {}
    for r in _rows(study.inputs / "stations.csv"):
        if r.get("station_id"):
            stations[r["station_id"]] = {"feeder_id": r.get("feeder_id") or None,
                                         "hidden": str(r.get("hidden", "")).strip().lower() in ("1", "true", "yes", "ja", "x"),
                                         "zone": r.get("zone") or None}
    return {"nodes": nodes, "stations": stations, "findings": findings}


def chain(up: dict, sid: str) -> tuple[list[dict], list[str]]:
    """The nodes above station `sid`, its feeder first, and the findings of the chain."""
    feeder = (up["stations"].get(sid) or {}).get("feeder_id")
    if not up["nodes"]:
        return [], []
    if not feeder:
        return [], [f"station {sid} has no feeder_id in inputs/stations.csv: no upstream cap applies"]
    out, seen, findings = [], set(), []
    node = feeder
    while node:
        if node in seen:
            findings.append(f"upstream.csv: {node} is its own ancestor")
            break
        seen.add(node)
        row = up["nodes"].get(node)
        if row is None:
            findings.append(f"upstream.csv has no row for {node}, above station {sid}")
            break
        out.append(row)
        node = row["parent_id"]
    return out, findings


def shared_by(up: dict, node: str) -> list[str]:
    """Every station whose chain holds `node`."""
    return sorted(sid for sid in up["stations"] if any(n["id"] == node for n in chain(up, sid)[0]))


def apply(up: dict, sid: str, generation_kw: float | None, consumption_kw: float | None) -> tuple[dict, dict]:
    """(upstream, published) for capacity.json: the chain with each node's status and who shares it,
    and the station's figures capped by it."""
    nodes, findings = chain(up, sid)
    listed = []
    for n in nodes:
        status = {side: ("entered" if n[col] is not None else "not entered") for side, col in SIDES.items()}
        listed.append({**n, "status": status, "shared_by": shared_by(up, n["id"])})
    upstream = {"entered": bool(up["nodes"]), "chain": listed, "findings": findings + list(up["findings"]),
                "hidden": bool((up["stations"].get(sid) or {}).get("hidden"))}
    published = {}
    for side, own in (("generation", generation_kw), ("consumption", consumption_kw)):
        col = SIDES[side]
        not_entered = [f"{n['level']}:{n['id']}" for n in nodes if n[col] is None]
        if own is None:
            published[side] = {"kw": None, "limited_by": "finding", "shared_by_stations": None,
                               "not_entered": not_entered}
            continue
        kw, by, shared = float(own), "station", None
        for n, item in zip(nodes, listed):
            if n[col] is not None and n[col] < kw:
                kw, by, shared = float(n[col]), f"{n['level']}:{n['id']}", len(item["shared_by"])
        # complete: a chain was entered, with every level's headroom and no finding in it
        published[side] = {"kw": kw, "limited_by": by, "shared_by_stations": shared, "not_entered": not_entered,
                           "complete": bool(nodes) and not not_entered and not findings}
    return upstream, published
