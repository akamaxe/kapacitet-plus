"""The evidence trail over HTTP: verify a sealed record, read the chain's head, download the log of
every queue decision ever sealed (including those an emptied queue no longer holds). A record
erased after its retention period verifies as `erased`: the chain still holds its digest."""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from .. import seals
from . import records

router = APIRouter(prefix="/api/v1", tags=["seals"])

_ID = r"^[A-Za-z0-9_.:-]{1,80}$"


@router.get("/seals")
def chain(limit: int = Query(20, ge=1, le=200)) -> dict:
    """Whether the chain holds, its head (the value to write down elsewhere) and the latest lines."""
    head = seals.verify_chain()
    lines = seals._lines()
    return {**head, "last": lines[-limit:][::-1]}


@router.get("/seals/verify")
def verify(kind: str = Query(..., pattern="^(" + "|".join(seals.KINDS) + ")$"),
           id: str = Query(..., pattern=_ID)) -> dict:  # noqa: A002 - the API's own word
    """Re-hash a sealed record's kept copy and, where it still exists, its working copy."""
    current = None
    if kind == "assessment":
        current = records.load(id, resolve=False)     # as kept: without the address, as it was sealed
    elif kind == "decision":
        from ..engine import queue as qu   # the engine loads lazily, as in main.py

        current = next((it for it in qu.load()["items"] if it.get("assessment") == id), None)
    out = seals.verify(kind, id, current)
    if not out.get("sealed"):
        raise HTTPException(404, {"code": "not_sealed", "message": f"{kind} {id} has no seal"})
    return out


_LOG_COLUMNS = ("decided", "assessment", "house", "address", "decision", "note", "pv_kw", "ev_kw", "heat_pump",
                "digest", "sealed_at", "seq")


@router.get("/queue/log.csv")
def decision_log() -> Response:
    """Every decision ever sealed, oldest first, with its digest: the queue's own memory."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_LOG_COLUMNS)
    for item in seals.sealed_of_kind("decision"):
        rec, entry = item["record"], item["entry"]
        # a decision keeps the house's number; its address is the network's, read now
        address = rec.get("address") or records.address_of(rec.get("cid")) or ""
        w.writerow([rec.get("decided", ""), rec.get("assessment", ""), rec.get("cid", ""), address,
                    rec.get("decision", ""), rec.get("note", ""), rec.get("pv_kw", ""), rec.get("ev_kw", ""),
                    rec.get("hp", ""), entry["digest"], entry["at"], entry["seq"]])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="kapacitet-decisions.csv"'})
