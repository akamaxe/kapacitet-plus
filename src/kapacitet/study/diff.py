"""`study diff --from Q1 --to Q2`: what changed between two collected quarters, and why.

    runs/<Q2>/changes.csv (and its machine copy): one row per station and figure that changed,
    with the cause tags and a sentence in Swedish

The causes come from runs/<Q>/inputs.json, which `study collect` writes: a figure that changed
while the station's network digest changed is tagged `network`, and so on for every input a
station's build reads (study.Study.digests), the upstream caps (`upstream`, applied at collect),
inputs/stations.csv (`stations`: a feeder, a zone or a hidden flag), the method version (`method`)
and the code (`code`). A station that is new or gone is tagged `added` or `removed`. A change with
no input that differs is tagged `unexplained`: the figures were computed again from the same
inputs, which should give the same figures, so it is something to look at.
"""

from __future__ import annotations

import json

from . import Study, StudyError
from . import csvio
from .publish import sv
from .review import load_rows, run_dir

FIELDS = {
    "published_generation_kw": "publicerad kapacitet för ny produktion",
    "published_consumption_kw": "publicerad kapacitet för ny förbrukning",
    "published_conditional_generation_kw": "villkorad kapacitet för ny produktion",
    "generation_status": "status för produktion",
    "consumption_status": "status för förbrukning",
    "published_generation_limited_by": "begränsning för produktion",
    "published_consumption_limited_by": "begränsning för förbrukning",
    "hidden": "dold",
}
CAUSE_SV = {
    "network": "nätdata", "mv": "uppmätt mellanspänning", "equipment": "registrerad utrustning",
    "meters": "mätvärden", "upstream": "gränser längre upp i nätet", "stations": "stationsförteckningen",
    "study_toml": "studiens inställningar", "overrides": "ändrade antaganden", "weather": "väder",
    "households": "hushållsprofiler", "method": "metodversion", "code": "programversion",
    "quick": "beräkningsläge", "added": "ny station", "removed": "borttagen station",
    "unexplained": "ingen ändrad indata",
}
COLUMNS = ["station_id", "name", "field", "from_quarter", "to_quarter", "from_value", "to_value", "delta_kw",
           "causes", "sentence_sv"]
KW = ("published_generation_kw", "published_consumption_kw", "published_conditional_generation_kw")


def _inputs(study: Study, q: str) -> dict:
    path = run_dir(study, q) / "inputs.json"
    return json.loads(path.read_text(encoding="utf-8")).get("stations", {}) if path.is_file() else {}


def causes(a: dict | None, b: dict | None) -> list[str]:
    """The tags of what differs between a station's inputs in two runs."""
    if a is None:
        return ["added"]
    if b is None:
        return ["removed"]
    out = [k for k in sorted(set(a.get("inputs") or {}) | set(b.get("inputs") or {}))
           if (a.get("inputs") or {}).get(k) != (b.get("inputs") or {}).get(k)]
    for key, tag in (("upstream", "upstream"), ("stations_csv", "stations"), ("method_version", "method"),
                     ("code", "code"), ("quick", "quick")):
        if a.get(key) != b.get(key):
            out.append(tag)
    return out or ["unexplained"]


def _value(field: str, v) -> str:
    if v is None:
        return "inget värde"
    if field in KW:
        return f"{sv(v, 1)} kW"
    if isinstance(v, bool):
        return "ja" if v else "nej"
    return str(v)


def sentence(sid: str, field: str, old, new, tags: list[str]) -> str:
    why = ", ".join(CAUSE_SV.get(t, t) for t in tags)
    if "added" in tags:
        return f"Station {sid} är ny: {FIELDS[field]} är {_value(field, new)}."
    if "removed" in tags:
        return f"Station {sid} finns inte längre: {FIELDS[field]} var {_value(field, old)}."
    delta = (f" ({'+' if new - old >= 0 else '−'}{sv(abs(new - old), 1)} kW)"
             if field in KW and old is not None and new is not None else "")
    return (f"Station {sid}: {FIELDS[field]} ändrades från {_value(field, old)} till {_value(field, new)}{delta}. "
            f"Orsak: {why}.")


def diff(study: Study, q_from: str, q_to: str) -> dict:
    """runs/<q_to>/changes.csv; returns {"paths", "rows"}."""
    if q_from == q_to:
        raise StudyError("config", message="--from and --to are the same quarter")
    old = {r["station_id"]: r for r in load_rows(study, q_from)}
    new = {r["station_id"]: r for r in load_rows(study, q_to)}
    ins_old, ins_new = _inputs(study, q_from), _inputs(study, q_to)
    rows = []
    for sid in sorted(set(old) | set(new)):
        a, b = old.get(sid), new.get(sid)
        tags = causes(ins_old.get(sid, {}) if a else None, ins_new.get(sid, {}) if b else None)
        for field in FIELDS:
            va, vb = (a or {}).get(field), (b or {}).get(field)
            if a is not None and b is not None and va == vb:
                continue
            if (a is None or b is None) and field not in KW[:2]:
                continue
            delta = (float(vb) - float(va)) if field in KW and va is not None and vb is not None else None
            rows.append({"station_id": sid, "name": (b or a).get("name"), "field": field, "from_quarter": q_from,
                         "to_quarter": q_to, "from_value": va, "to_value": vb, "delta_kw": delta,
                         "causes": ";".join(tags), "sentence_sv": sentence(sid, field, va, vb, tags)})
    paths = csvio.write(run_dir(study, q_to), "changes", rows, COLUMNS, study.config.output.csv_dialect)
    return {"paths": paths, "rows": rows}


__all__ = ["CAUSE_SV", "COLUMNS", "FIELDS", "causes", "diff", "sentence"]
