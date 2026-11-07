"""`study collect`: every station's capacity.json in one table, runs/<Q>/stations.parquet.

The upstream caps of the current inputs/upstream.csv are applied to each station first (they
apply after the computation and build nothing again, study/upstream.py), and a capacity.json
whose `upstream` or `published` they change is rewritten with them. A station that has not been
computed yet is listed with its status and no figures. The table is built with pyarrow on a fixed
schema and written by DuckDB (beside, then moved in), so a reader never sees half a file.

Beside it, runs/<Q>/inputs.json keeps what each station's figures were computed from (the input
digests its build recorded, and the upstream.csv the caps came from), so `study diff` can say why a
figure changed between two quarters. Each row also carries the station's position (its
transformer's busbar, WGS84) and the DSO's zone from inputs/stations.csv, which the disclosure of
a publication groups by, and the envelope (conditional) capacity when the method is approved and
its envelopes verified, capped by the same upstream chain. The three-phase figures (capacity.json's
`three_phase`) are carried, capped the same way, only when the check is "ok" or "limited".
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re

from . import Study, StudyError
from . import upstream as up_mod

QUARTER = re.compile(r"^\d{4}Q[1-4]$")
FIELDS = (
    ("station_id", "string"), ("name", "string"), ("status", "string"), ("hidden", "bool_"),
    ("customers", "int64"), ("method_version", "string"), ("fingerprint", "string"), ("computed_at", "string"),
    ("generation_status", "string"), ("generation_kw", "float64"), ("binding_mv_pu", "float64"),
    ("generation_limit", "string"), ("generation_order", "int64"),
    ("consumption_device", "string"), ("consumption_status", "string"), ("consumption_kw", "float64"),
    ("consumption_limit", "string"),
    ("generation_baseline_violation_hours", "int64"), ("consumption_baseline_violation_hours", "int64"),
    ("worst_generation_status", "string"), ("worst_consumption_status", "string"),
    ("worst_generation_kw", "float64"), ("worst_generation_customer", "string"),
    ("worst_consumption_kw", "float64"), ("worst_consumption_customer", "string"),
    ("published_generation_kw", "float64"), ("published_generation_limited_by", "string"),
    ("published_consumption_kw", "float64"), ("published_consumption_limited_by", "string"),
    ("upstream_complete", "bool_"), ("full_year_violations", "int64"), ("nonconverged_hours", "int64"),
    ("conditional_generation_status", "string"), ("conditional_generation_kw", "float64"),
    ("published_conditional_generation_kw", "float64"), ("approval_reference", "string"),
    ("three_phase_status", "string"), ("three_phase_known_share", "float64"),
    ("three_phase_generation_status", "string"), ("three_phase_generation_kw", "float64"),
    ("three_phase_consumption_status", "string"), ("three_phase_consumption_kw", "float64"),
    ("published_three_phase_generation_kw", "float64"), ("published_three_phase_consumption_kw", "float64"),
    ("lon", "float64"), ("lat", "float64"), ("zone", "string"),
    ("years", "string"), ("orders", "int64"), ("percentile", "float64"),
)
INPUTS_SCHEMA = "kapacitet.run-inputs/1"
# the three-phase check's statuses that publish a figure (engine/station.three_phase_check)
THREE_PHASE_FIGURES = ("ok", "limited")


def _hours(block: dict) -> int | None:
    """The hours today's grid already breaks a limit in, summed over the years (a finding's)."""
    hours = block.get("baseline_violation_hours")
    return int(sum(hours.values())) if hours else None


def quarter(today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    return f"{today.year}Q{(today.month - 1) // 3 + 1}"


def _schema():
    import pyarrow as pa

    return pa.schema([(name, getattr(pa, kind)()) for name, kind in FIELDS])


def _limit(block: dict) -> str | None:
    return ((block or {}).get("binding") or {}).get("limit")


def position(study: Study, sid: str) -> tuple[float | None, float | None]:
    """The station's transformer busbar (processed/grid/substation.json), WGS84, when it has one."""
    from ..geo import num

    path = study.station_dir(sid) / "processed" / "grid" / "substation.json"
    try:
        sub = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        sub = {}
    lon, lat = num(sub.get("lon")), num(sub.get("lat"))
    return (lon, lat) if lon is not None and lat is not None else (None, None)


def row(study: Study, sid: str, up: dict) -> tuple[dict, dict | None]:
    """The station's row of the table, and its capacity.json with the current caps (None when the
    station has none)."""
    rec = study.record(sid)
    path = study.station_dir(sid) / "processed" / "results" / "capacity.json"
    base = {name: None for name, _ in FIELDS}
    base.update(station_id=sid, name=rec.get("name") or sid, status=rec.get("status"),
                hidden=sid in set(study.config.disclosure.hidden) or bool((up["stations"].get(sid) or {}).get("hidden")),
                customers=(rec.get("summary") or {}).get("customers"),
                zone=(up["stations"].get(sid) or {}).get("zone"))
    base["lon"], base["lat"] = position(study, sid)
    if not path.is_file():
        return base, None
    cap = json.loads(path.read_text(encoding="utf-8"))
    gen, cons, worst = cap.get("generation") or {}, cap.get("consumption") or {}, cap.get("worst_point") or {}
    upstream, published = up_mod.apply(up, sid, gen.get("kw"), cons.get("kw"))
    cap["upstream"], cap["published"] = upstream, published
    pg, pc = published["generation"], published["consumption"]
    base.update(
        method_version=cap.get("method_version"), fingerprint=cap.get("fingerprint"), computed_at=cap.get("computed_at"),
        generation_status=gen.get("status"), generation_kw=gen.get("kw"), binding_mv_pu=gen.get("binding_mv_pu"),
        generation_limit=_limit(gen), generation_order=gen.get("order"),
        consumption_device=cons.get("consumption_device"), consumption_status=cons.get("status"),
        consumption_kw=cons.get("kw"), consumption_limit=_limit(cons),
        generation_baseline_violation_hours=_hours(gen), consumption_baseline_violation_hours=_hours(cons),
        worst_generation_status=(worst.get("generation") or {}).get("status"),
        worst_consumption_status=(worst.get("consumption") or {}).get("status"),
        worst_generation_kw=(worst.get("generation") or {}).get("kw"),
        worst_generation_customer=(worst.get("generation") or {}).get("customer_id"),
        worst_consumption_kw=(worst.get("consumption") or {}).get("kw"),
        worst_consumption_customer=(worst.get("consumption") or {}).get("customer_id"),
        published_generation_kw=pg["kw"], published_generation_limited_by=pg["limited_by"],
        published_consumption_kw=pc["kw"], published_consumption_limited_by=pc["limited_by"],
        upstream_complete=bool(pg.get("complete")) and bool(pc.get("complete")),
        full_year_violations=(cap.get("verification") or {}).get("full_year_violations"),
        nonconverged_hours=(cap.get("verification") or {}).get("nonconverged_hours"),
        years=",".join(str(y) for y in cap.get("years") or []) or None, orders=cap.get("orders"),
        percentile=cap.get("percentile"))
    doe = gen.get("doe") or {}
    if doe:
        base.update(conditional_generation_status=doe.get("status"), approval_reference=doe.get("approval_reference"))
    if doe.get("status") == "ok" and doe.get("kw") is not None and not doe.get("full_year_violations"):
        # the envelope capacity, capped by the same chain as the firm one
        _, capped = up_mod.apply(up, sid, doe["kw"], None)
        base.update(conditional_generation_kw=float(doe["kw"]),
                    published_conditional_generation_kw=capped["generation"]["kw"])
    three = cap.get("three_phase") or {}
    if three:
        base.update(three_phase_status=three.get("status"), three_phase_known_share=three.get("known_share"))
    if three.get("status") in THREE_PHASE_FIGURES:
        # the three-phase figures, capped by the same chain as the balanced ones
        g, c = (three.get(side) or {} for side in ("generation", "consumption"))
        _, capped = up_mod.apply(up, sid, g.get("kw"), c.get("kw"))
        base.update(three_phase_generation_status=g.get("status"), three_phase_generation_kw=g.get("kw"),
                    three_phase_consumption_status=c.get("status"), three_phase_consumption_kw=c.get("kw"),
                    published_three_phase_generation_kw=capped["generation"]["kw"],
                    published_three_phase_consumption_kw=capped["consumption"]["kw"])
    return base, cap


def collect(study: Study, q: str | None = None) -> dict:
    """runs/<q>/stations.parquet (q by default this quarter, as 2026Q4); returns {"path", "rows",
    "rewritten": stations whose capacity.json took new caps}."""
    import duckdb
    import pyarrow as pa

    from ..atomic import write_json_atomic

    q = q or quarter()
    if not QUARTER.match(q):
        raise StudyError("config", message=f"{q!r} is not a quarter such as 2026Q4")
    up = up_mod.read(study)
    rows, rewritten = [], []
    for sid in study.station_ids():
        r, cap = row(study, sid, up)
        rows.append(r)
        if cap is not None:
            path = study.station_dir(sid) / "processed" / "results" / "capacity.json"
            before = json.loads(path.read_text(encoding="utf-8"))
            if (before.get("upstream"), before.get("published")) != (cap["upstream"], cap["published"]):
                write_json_atomic(path, cap)
                rewritten.append(sid)
    table = pa.Table.from_pylist(rows, schema=_schema())
    folder = study.runs / q
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / "stations.parquet"
    tmp = folder / "stations.parquet.tmp"
    con = duckdb.connect()
    try:
        con.register("stations", table)
        target = tmp.as_posix().replace("'", "''")
        con.execute(f"COPY stations TO '{target}' (FORMAT PARQUET)")
    finally:
        con.close()
    os.replace(tmp, out)
    write_json_atomic(folder / "inputs.json", run_inputs(study, up))
    return {"path": out, "rows": len(rows), "rewritten": rewritten, "quarter": q}


def run_inputs(study: Study, up: dict | None = None) -> dict:
    """What the run's figures were computed from, per station: the digests its build recorded,
    the upstream.csv applied now, and the method and code of its capacity.json."""
    from . import code_version, file_digest

    upstream_now = file_digest(study.inputs / "upstream.csv")
    stations = {}
    for sid in study.station_ids():
        rec = study.record(sid)
        path = study.station_dir(sid) / "processed" / "results" / "capacity.json"
        cap = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        built = rec.get("kapacitet") or {}
        stations[sid] = {"inputs": {k: v for k, v in (rec.get("inputs") or {}).items() if k != "upstream"},
                         "upstream": upstream_now, "stations_csv": file_digest(study.inputs / "stations.csv"),
                         "method_version": cap.get("method_version") or rec.get("method_version"),
                         "code": built.get("commit") or built.get("version"), "quick": bool(rec.get("quick"))}
    return {"schema": INPUTS_SCHEMA, "study": study.id, "collected_at": _now(), "stations": stations,
            "code": code_version()}


def _now() -> str:
    from .. import procs

    return procs.now()
