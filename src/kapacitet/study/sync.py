"""`kapacitet study sync <dir> --approved <csv> [--pending <csv>]`: each station's queue made the DSO's.

The DSO's own list of approved connections is the truth about the grid: a study's station queues
are rebuilt from it, not decided one by one. The approved file has the columns of a case
(study/cases.py) and `decision` (firm or nonfirm) and `decided_at`; the pending file, the cases
received and not yet decided. Every ready station is rebuilt, also one with no approved row, so a
connection taken off the list leaves the grid the next case meets.

Each station's process (`kapacitet station sync <sid> --study <dir> --batch <name>`) rebuilds its
queue at each end of its MV band (engine/queue.rebuild into requests/queue_mv<end>, on the model at
that end, from the "existing" baseline there), in the order the connections were approved
(decided_at, then received_at, then case_id). Each is re-verified on every weather year as an
approval would be; one that fails is kept and flagged in sync_report.csv instead of being left
out, since it is connected whatever the check says. The rebuild is deterministic: the same list
gives the same bytes. Each station's new queues are sealed as `sync` in its own chain, and the
report in the study's evidence/.

    cases/<batch>/sync_report.csv (+ .machine.csv)   one row per case: approved or pending, the
                                                      order, whether it verified at each end, its
                                                      flags, or the error that kept it out
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..atomic import write_json_atomic
from . import Study, StudyError
from .cases import (CaseError, Runner, batch_dir, group, read_cases, row_error, run_stations,
                    station_context, station_output)
from .csvio import write as write_csv

EXTRA = ("decision", "decided_at")
REPORT_COLUMNS = ["case_id", "station", "connection_point_id", "status", "order", "decision", "decided_at",
                  "verified", "flags", "pv_kw", "assessment_id", "error", "error_text"]


def _approved(path: Path) -> tuple[list[dict], list[dict]]:
    from .cases import _when

    cases, errors = read_cases(path, extra=EXTRA)
    ok = []
    for c in cases:
        decision = c["decision"].lower()
        if decision not in ("firm", "nonfirm"):
            errors.append(row_error(c["case_id"], "bad_decision", value=c["decision"]))
            continue
        try:
            decided = _when("decided_at", c["decided_at"]) if c["decided_at"] else c["received_at"]
        except CaseError as err:
            errors.append(row_error(c["case_id"], err.code, **err.params))
            continue
        ok.append({**c, "decision": decision, "decided_at": decided})
    return ok, errors


def sync_id(case: dict) -> str:
    """An approved connection's id in the queue: the same case and request, the same id."""
    key = {"case_id": case["case_id"], "request": case["request"], "cid": case["cid"], "decision": case["decision"]}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def _order(c: dict) -> tuple:
    return (c.get("decided_at") or c["received_at"], c["received_at"], c["case_id"])


# ---------------------------------------------------------------- inside a station's process

def station_sync(study: Study, sid: str, batch: str) -> dict:
    """`kapacitet station sync`: this station's queues rebuilt from the batch, at each end of its band."""
    import warnings

    from .. import seals
    from ..engine import queue as qu
    from ..engine import request as rq

    warnings.filterwarnings("ignore")
    work = json.loads((batch_dir(study, batch) / "work" / f"{sid}.json").read_text(encoding="utf-8"))
    sctx = station_context()
    out: dict = {"station": sid, "batch": batch, "fingerprint": sctx["fingerprint"],
                 "mv_band_pu": list(sctx["band"]), "approved": [], "pending": [], "errors": []}
    if not sctx["ready"]:
        out["errors"] = [row_error(c["case_id"], "station_stale", station=sid)
                         for c in work["approved"] + work["pending"]]
        write_json_atomic(station_output(batch, "sync"), out)
        return out
    hp = sctx["hp"]
    items, seen = [], {}
    for c in sorted(work["approved"], key=_order):
        if c["cid"] in seen:
            out["errors"].append(row_error(c["case_id"], "house_twice", station=sid, other=seen[c["cid"]]))
            continue
        if c["request"]["hp"] and bool(hp[c["cid"]]):
            out["errors"].append(row_error(c["case_id"], "has_heat_pump", station=sid))
            continue
        seen[c["cid"]] = c["case_id"]
        items.append({"assessment": sync_id(c), "cid": c["cid"], "decision": c["decision"],
                      "decided": c["decided_at"], "by": "sync", "case_id": c["case_id"],
                      "connection_point_id": c["connection_point_id"], "received_at": c["received_at"],
                      "request": {**c["request"], "cid": c["cid"]}})
    pending = [{"case_id": c["case_id"], "cid": c["cid"], "connection_point_id": c["connection_point_id"],
                "received_at": c["received_at"], "request": {**c["request"], "cid": c["cid"]}}
               for c in sorted(work["pending"], key=lambda c: (c["received_at"], c["case_id"]))]
    by_end = {}
    for e in sctx["ends"]:
        where = qu.band_folder(e)
        # the check runs the band's high end generation-only (request.check), so its queue holds the
        # generation side of each approved item; the low end holds all of it
        mine = items if e == min(sctx["ends"]) else [
            {**it, "request": {**it["request"], "ev_kw": 0.0, "hp": False, "ev_phases": 0}} for it in items]
        q = qu.rebuild(rq.model_at(e), hp, sctx["ev"], rq.band_name("existing", e), mine, pending, where=where)
        digest = seals.digest(q)
        seals.seal("sync", f"{batch}.{where.name}.{digest[:12]}", q)
        by_end[f"{e:.2f}"] = {it["assessment"]: it for it in q["items"]}
    for order, it in enumerate(items, start=1):
        ends = {e: got[it["assessment"]] for e, got in by_end.items()}
        flags = [f"{e}:{f}" for e, got in ends.items() for f in got["flags"]]
        out["approved"].append({"case_id": it["case_id"], "station": sid, "connection_point_id": it["connection_point_id"],
                                "order": order, "decision": it["decision"], "decided_at": it["decided"],
                                "verified": all(g["verified"] for g in ends.values()), "flags": flags,
                                "pv_kw": max(g["pv_kw"] for g in ends.values()), "assessment_id": it["assessment"]})
    out["pending"] = [{"case_id": p["case_id"], "station": sid, "connection_point_id": p["connection_point_id"]}
                      for p in pending]
    write_json_atomic(station_output(batch, "sync"), out)
    return out


# ---------------------------------------------------------------- the batch

def run(study: Study, approved_csv: str | Path, pending_csv: str | Path | None = None, batch: str | None = None,
        jobs: int | None = None, runner: Runner | None = None, allow_git: bool = False) -> dict:
    """Rebuild every ready station's queues from the DSO's lists; returns the report's rows."""
    from .. import seals

    approved_csv = Path(approved_csv)
    batch = batch or f"sync-{approved_csv.stem}"
    folder = batch_dir(study, batch)
    approved, errors = _approved(approved_csv)
    pending, perrors = read_cases(Path(pending_csv)) if pending_csv else ([], [])
    errors += perrors
    by_a, unplaced = group(study, approved)
    by_p, unplaced_p = group(study, pending)
    errors += unplaced + unplaced_p
    ready = [sid for sid in study.station_ids() if study.record(sid).get("status") == "ready"]
    if not ready:
        raise StudyError("no_ready_station")
    work = {sid: {"batch": batch, "approved": by_a.get(sid, []), "pending": by_p.get(sid, [])} for sid in ready}
    got = run_stations(study, "sync", batch, work, jobs=jobs, runner=runner, allow_git=allow_git)
    rows = []
    for sid, doc in got.items():
        if "failed" in doc:
            errors += [row_error(c["case_id"], "station_failed", station=sid,
                                 detail=f"the station's process ended with {doc['failed']}")
                       for c in work[sid]["approved"] + work[sid]["pending"]]
            continue
        rows += [{**r, "status": "approved", "flags": ";".join(r["flags"])} for r in doc["approved"]]
        rows += [{**r, "status": "pending"} for r in doc["pending"]]
        errors += doc["errors"]
    rows += [{"case_id": e["case_id"], "station": e.get("station") or None, "error": e["error"],
              "error_text": e["error_text"]} for e in errors]
    rows.sort(key=lambda r: (r.get("station") or "~", r.get("status") or "~", r.get("order") or 0, r["case_id"]))
    written = write_csv(folder, "sync_report", rows, REPORT_COLUMNS, study.config.output.csv_dialect)
    sealed = {"batch": batch, "rows": rows}
    with seals.chain_at(study.evidence):
        entry = seals.seal("sync", f"{batch}.{seals.digest(sealed)[:12]}", sealed)
    return {"batch": batch, "folder": folder, "rows": rows, "errors": errors, "paths": written, "seal": entry,
            "stations": ready}


__all__ = ["EXTRA", "REPORT_COLUMNS", "run", "station_sync", "sync_id"]
