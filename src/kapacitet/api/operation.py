"""The DSO's settings and what the operation stage computed, over HTTP: a day's schedule as an
inverter would receive it, the season replayed in the shadow, and a meter checked against its limits."""

from __future__ import annotations

import csv
import io
import json
import time
from datetime import date as Date

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import dso, seals
from ..config import A
from ..paths import processed_ro
from .combos import COMMAND, POLICY_PATTERN, operation_index, resolve

router = APIRouter(prefix="/api/v1", tags=["operation"])
METER_MAX_BYTES = 5_000_000


def _refusal(status: int, code: str, **params) -> HTTPException:
    from ..engine.delivery import ERRORS

    return HTTPException(status, {"code": code, "message": ERRORS[code].format(**params), "params": params})


@router.get("/settings")
def get_settings() -> dict:
    return {"settings": dso.load().model_dump(), "policies": list(dso.POLICIES)}


@router.put("/settings")
def put_settings(change: dso.DsoSettingsChange) -> dict:
    """Change the DSO's settings; every change is sealed with what it replaced."""
    return {"settings": dso.update(change).model_dump(), "policies": list(dso.POLICIES)}


@router.get("/operation/index")
def get_index() -> dict:
    """Which combinations of rule and basis were computed, and what each verified to."""
    idx = operation_index()
    if idx is None:
        raise HTTPException(404, {"code": "not_computed", "file": "operation/index.json", "command": COMMAND,
                                  "message": "the operation stage has not been run"})
    return {**idx, "active_policy": dso.load().allocation_policy}


# ---------------------------------------------------------------- a day's schedule

def _day_ahead(cid: int, date: str, policy: str | None):
    """The house's day-ahead limits and desired export for the year, and the study day of `date`."""
    from ..engine.delivery import day_of

    combo = resolve("dayahead", policy)
    cids = combo.nonfirm_cids()
    if cid not in cids:
        raise _refusal(404, "not_an_agreement", cid=cid)
    year = int(operation_index()["year"])
    try:
        day = day_of(year, Date.fromisoformat(date))
    except ValueError:
        raise _refusal(422, "outside_year", year=year) from None
    i = cids.index(cid)
    limits = np.asarray(np.load(combo.envelopes, mmap_mode="r")[i], dtype=float)
    desired_path = processed_ro("ui", "operation", "desired.npy")
    desired = np.asarray(np.load(desired_path, mmap_mode="r")[i], dtype=float) if desired_path.exists() else None
    issued = json.loads(combo.result("issued.json").read_text(encoding="utf-8"))
    dc = issued["default_control"]
    default_kw = float(dc["kw"][dc["cids"].index(cid)])
    return combo, year, day, limits, desired, default_kw, float(dc["kappa"])


@router.get("/operation/schedule")
def schedule(cid: int = Query(..., ge=0), date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
             policy: str | None = Query(None, pattern=POLICY_PATTERN),
             format: str = Query("json", pattern="^(json|csv)$")):  # noqa: A002 - the API's own word
    """A house's 24 limits for one date, issued the day before, with each hour's interval start."""
    from ..engine.delivery import schedule as rows_for

    combo, year, day, limits, desired, default_kw, kappa = _day_ahead(cid, date, policy)
    rows = rows_for(limits, desired, year, day)
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["start_utc", "study_clock_utc+1", "stockholm", "limit_kw", "desired_export_kw"])
        for r in rows:
            w.writerow([r["start_utc"], r["study_clock"], r["stockholm"], r["limit_kw"], r["desired_kw"]])
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="kapacitet-schedule-{cid}-{date}.csv"'})
    return {"cid": cid, "date": date, "year": year, "basis": "dayahead", "policy": combo.policy, "rows": rows,
            "default_kw": default_kw, "kappa": kappa}


@router.get("/operation/schedule.xml")
def schedule_xml(cid: int = Query(..., ge=0), date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
                 policy: str | None = Query(None, pattern=POLICY_PATTERN),
                 issue: bool = Query(True, description="false: a preview to read on screen, not sealed")) -> Response:
    """The day as an IEEE 2030.5 DERControlList in the CSIP-AUS profile (modelled, not certified).
    Each issue is sealed: what was sent, to whom, for which day. A preview (`issue=false`) is the
    same document, shown rather than sent, so it leaves no record."""
    from ..engine.csipaus import der_control_list
    from ..engine.delivery import schedule as rows_for

    combo, year, day, limits, desired, _, _ = _day_ahead(cid, date, policy)
    rows = rows_for(limits, desired, year, day)
    body = der_control_list(cid, rows, int(time.time()))
    if not issue:
        return Response(body, media_type="application/sep+xml")
    fp = seals.fingerprint()["digest"][:8]
    seals.seal("schedule", f"{cid}-{date}-{combo.policy}-{fp}",
               {"cid": cid, "date": date, "policy": combo.policy, "basis": "dayahead",
                "limits": [{"start_epoch": r["start_epoch"], "limit_kw": r["limit_kw"]} for r in rows]})
    return Response(body, media_type="application/sep+xml",
                    headers={"Content-Disposition": f'attachment; filename="kapacitet-dercontrols-{cid}-{date}.xml"'})


@router.get("/operation/default.xml")
def default_xml(cid: int = Query(..., ge=0), policy: str | None = Query(None, pattern=POLICY_PATTERN)) -> Response:
    """The limit the inverter keeps when it holds no schedule: DefaultDERControl."""
    from ..engine.csipaus import default_der_control

    combo = resolve("dayahead", policy)
    if cid not in combo.nonfirm_cids():
        raise _refusal(404, "not_an_agreement", cid=cid)
    issued = json.loads(combo.result("issued.json").read_text(encoding="utf-8"))
    dc = issued["default_control"]
    body = default_der_control(cid, float(dc["kw"][dc["cids"].index(cid)]))
    return Response(body, media_type="application/sep+xml",
                    headers={"Content-Disposition": f'attachment; filename="kapacitet-default-{cid}.xml"'})


# ---------------------------------------------------------------- the season in the shadow

@router.get("/operation/shadow")
def shadow(policy: str | None = Query(None, pattern=POLICY_PATTERN)) -> dict:
    """The season replayed day by day: what the day-ahead envelope did, beside perfect foresight,
    and the pilot's two criteria (no hour above a limit; every agreement within its level)."""
    from ..engine.delivery import go_no_go, season_days

    settings = dso.load()
    out: dict = {"policy": None, "agreed_level_pct": settings.agreed_level_pct, "bases": {}}
    year = None
    for basis in ("dayahead", "foresight"):
        combo = resolve(basis, policy)
        out["policy"] = combo.policy
        days = json.loads(combo.result("days.json").read_text(encoding="utf-8"))
        year = int(days["year"])
        npz = np.load(combo.result("days.npz"))
        season = season_days(year, tuple(A("shadow_season_months")))
        verdict = go_no_go(days, {k: npz[k] for k in ("curtailed", "pv", "cids")}, season, settings.agreed_level_pct)
        out["bases"][basis] = {
            **verdict,
            "days": {k: [days[k][i] for i in season] for k in
                     ("curtailed_kwh", "desired_kwh", "hours_limited", "hours_necessary_limited",
                      "hours_unnecessary_limited", "violation_hours")},
        }
    out["year"] = year
    out["season_months"] = list(A("shadow_season_months"))
    out["first_day"] = int(season_days(year, tuple(A("shadow_season_months")))[0])
    fc = processed_ro("results", "forecast.json")
    if fc.exists():
        rows = json.loads(fc.read_text(encoding="utf-8")).get("rows", [])
        out["persistence"] = next((r for r in rows if r.get("scheme") == "persistence"), None)
    return out


# ---------------------------------------------------------------- a meter

class MeterIn(BaseModel):
    cid: int = Field(..., ge=0)
    csv: str = Field(..., description="timestamp (ISO 8601 with offset), export_kw or export_w")
    policy: str | None = Field(None, pattern=POLICY_PATTERN)


@router.post("/operation/meter-check")
def meter_check(body: MeterIn) -> dict:
    """A house's measured export against the limits it was issued the day before."""
    from ..engine.delivery import MeterError
    from ..engine.delivery import meter_check as check

    if len(body.csv.encode("utf-8")) > METER_MAX_BYTES:
        raise HTTPException(413, {"code": "too_large", "message": f"larger than {METER_MAX_BYTES // 1_000_000} MB",
                                  "params": {"mb": METER_MAX_BYTES // 1_000_000, "tables": "meter"}})
    combo = resolve("dayahead", body.policy)
    cids = combo.nonfirm_cids()
    if body.cid not in cids:
        raise _refusal(404, "not_an_agreement", cid=body.cid)
    limits = np.asarray(np.load(combo.envelopes, mmap_mode="r")[cids.index(body.cid)], dtype=float)
    try:
        out = check(body.csv, limits, int(operation_index()["year"]), float(A("meter_breach_tolerance_w")))
    except MeterError as err:
        raise HTTPException(422, {"code": err.code, "message": str(err), "params": err.params}) from err
    return {"cid": body.cid, "policy": combo.policy, **out}


@router.get("/operation/meter-example")
def meter_example(cid: int = Query(..., ge=0), date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
                  policy: str | None = Query(None, pattern=POLICY_PATTERN)) -> Response:
    """A synthetic day of 5-minute readings for a house, with one deliberate breach (labelled)."""
    from ..engine.delivery import meter_example as example

    _, year, day, limits, desired, _, _ = _day_ahead(cid, date, policy)
    if desired is None:
        raise HTTPException(404, {"code": "not_computed", "file": "ui/operation/desired.npy", "command": COMMAND})
    body = example(limits, desired, year, day)
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="kapacitet-meter-example-{cid}-{date}.csv"'})
