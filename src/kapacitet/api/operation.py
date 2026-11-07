"""The DSO's settings and what the operation stage computed, over HTTP: a day's schedule as an
inverter would receive it (issued only once the day-ahead market has cleared), the market
alternatives the DSO consulted, the season replayed in the shadow, and a meter checked against its
limits."""

from __future__ import annotations

import csv
import io
import json
import re
import threading
import time
from datetime import date as Date
from datetime import datetime
from typing import Literal

import numpy as np
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import dso, market, seals
from ..config import A
from ..paths import processed_ro
from .combos import COMMAND, POLICY_PATTERN, operation_index, resolve

router = APIRouter(prefix="/api/v1", tags=["operation"])
METER_MAX_BYTES = 5_000_000
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
# An Idempotency-Key names one issue or export: sent again, it returns the seal it already has
# (seals.entry_for) instead of chaining a second line. It becomes part of a seal's id, hence the alphabet.
IDEMPOTENCY_KEY = "^[A-Za-z0-9_-]{8,64}$"
RESOLUTION_DOC = ("minutes per interval: 15 (the day-ahead market's MTU, over the delivery day on Stockholm's "
                  "clock) or 60 (the study day); by default the assumption schedule_resolution_min")
# looking up a key's seal and sealing it are one step, so two retries at once cannot both seal
_SEAL_LOCK = threading.Lock()


# What the operation stage's answers rest on (api/main.SOURCES).
_SOURCES = ("smhi_strang", "smhi_metobs", "osm", "synthetic_grid")


def _provenance(*files) -> dict:
    """main.provenance, dated by the operation stage's own files."""
    from .main import provenance

    return provenance(*_SOURCES, files=files)


def _refusal(status: int, code: str, **params) -> HTTPException:
    from ..engine.delivery import ERRORS

    message = {**ERRORS, **market.ERRORS}[code].format(**params)
    return HTTPException(status, {"code": code, "message": message, "params": params})


@router.get("/settings")
def get_settings() -> dict:
    return {"settings": dso.load().model_dump(), "policies": list(dso.POLICIES)}


@router.put("/settings")
def put_settings(change: dso.DsoSettingsChange) -> dict:
    """Change the DSO's settings; every change is sealed with what it replaced."""
    return {"settings": dso.update(change).model_dump(), "policies": list(dso.POLICIES)}


@router.put("/settings/rule-signature")
def put_rule_signature(body: dso.RuleSignatureIn) -> dict:
    """Sign the automatic rule: who signs, in what role, for which kinds of request, up to what PV
    size and until when, with the thresholds in force unless given. Sealed as `rule_signature`;
    without it, or outside it, an application waits for a person."""
    return {"settings": dso.sign_rule(body).model_dump(), "policies": list(dso.POLICIES)}


@router.get("/operation/index")
def get_index() -> dict:
    """Which combinations of rule and basis were computed, and what each verified to."""
    idx = operation_index()
    if idx is None:
        raise HTTPException(404, {"code": "not_computed", "file": "operation/index.json", "command": COMMAND,
                                  "message": "the operation stage has not been run"})
    return {**idx, "active_policy": dso.load().allocation_policy,
            "provenance": _provenance(processed_ro("results", "operation", "index.json"))}


# ---------------------------------------------------------------- the evidence pack for Ei

def _sealed_headers(entry: dict, filename: str) -> dict:
    """A sealed file's headers: the attachment's name, and its seal, so the page can keep a receipt."""
    out = {"Content-Disposition": f'attachment; filename="{filename}"', "X-Seal-Digest": entry["digest"],
           "X-Seal-Seq": str(entry["seq"]), "X-Seal-At": entry["at"]}
    if entry.get("key_id"):
        out["X-Seal-Key-Id"] = entry["key_id"]
    return out


def _pack(basis: str, policy: str | None) -> tuple[dict, str]:
    """Each of Ei's conditions beside what the prototype can show for it, and the rule it was read for."""
    from ..engine.eipack import assess
    from . import main

    combo = resolve(basis, policy)
    agreements = json.loads(processed_ro("results", "operation", "agreements.json").read_text(encoding="utf-8"))
    report_path = combo.result("compliance.json")
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
    try:
        business = main.business(None)["data"]
    except HTTPException:
        business = None                      # the reinforcement case needs results the pack can do without
    pack = assess(basis, combo.policy, dso.load(), operation_index(), agreements["agreements"], report, business,
                  market.entries())
    pack["provenance"] = _provenance(processed_ro("results", "operation", "index.json"),
                                     processed_ro("results", "operation", "agreements.json"), report_path)
    return pack, combo.policy


@router.get("/eipack")
def eipack(basis: str = Query("dayahead", pattern="^(foresight|dayahead)$"),
           policy: str | None = Query(None, pattern=POLICY_PATTERN)) -> dict:
    """Each of Ei's conditions for conditional agreements (Ei2025:01) beside what the prototype can
    show for it: evidence of the model, a statement of the DSO, not met here, or not applicable.
    Reading it leaves no record; `POST /eipack/export` downloads it sealed."""
    return _pack(basis, policy)[0]


class PackOut(BaseModel):
    basis: str = Field("dayahead", pattern="^(foresight|dayahead)$")
    policy: str | None = Field(None, pattern=POLICY_PATTERN)


def _sealed_pack(pack: dict, entry: dict, basis: str, policy: str) -> Response:
    """The pack with its seal: the chain line, signature included, and the public key that checks
    it, with the sentence on how (seals.HOW_TO_VERIFY). `digest` is the pack's own, without `seal`."""
    seal = {k: entry[k] for k in ("digest", "seq", "at", "fingerprint")}
    seal.update(key_id=entry.get("key_id"), sig=entry.get("sig"), line=entry,
                key=seals.public_key_info(entry["key_id"]) if entry.get("key_id") else None)
    body = json.dumps({**pack, "seal": seal}, indent=1)
    return Response(body, media_type="application/json",
                    headers=_sealed_headers(entry, f"kapacitet-ei-evidence-{basis}-{policy}.json"))


@router.post("/eipack/export")
def eipack_export(body: PackOut, idempotency_key: str | None = Header(None, pattern=IDEMPOTENCY_KEY)) -> Response:
    """Download the pack, sealed with what it showed. Each export is a seal of its own, unless it
    repeats an Idempotency-Key: then the pack sealed under that key comes back as it was."""
    pack, policy = _pack(body.basis, body.policy)
    with _SEAL_LOCK:
        if idempotency_key:
            item = f"{body.basis}-{policy}-k{idempotency_key}"
            entry = seals.entry_for("eipack", item)
            kept = seals.sealed_copy("eipack", item) if entry else None
            if kept is not None:
                return _sealed_pack(kept, entry, body.basis, policy)
        else:
            item = f"{body.basis}-{policy}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        entry = seals.seal("eipack", item, pack)
    return _sealed_pack(pack, entry, body.basis, policy)


# ---------------------------------------------------------------- a day's schedule

def _resolution(value: int | None) -> int:
    """The step a schedule is issued in: the request's, else the assumption schedule_resolution_min."""
    from ..engine.delivery import RESOLUTIONS, resolution

    try:
        return resolution(value)
    except ValueError:
        raise _refusal(422, "resolution", minutes=value, allowed=" or ".join(map(str, RESOLUTIONS))) from None


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
def schedule(cid: int = Query(..., ge=0), date: str = Query(..., pattern=DATE_PATTERN),
             policy: str | None = Query(None, pattern=POLICY_PATTERN),
             format: str = Query("json", pattern="^(json|csv)$"),  # noqa: A002 - the API's own word
             resolution: int | None = Query(None, description=RESOLUTION_DOC)):
    """A house's limits for one date, issued the day before, one per interval of the schedule's
    step, with each interval's start. A quarter-hour schedule carries each hour's verified
    envelope in its four quarters and says so (`inputs_resolution_min`); `within_envelope` is its
    check, hour by hour, against that envelope."""
    from ..engine.delivery import schedule as rows_for
    from ..engine.delivery import terms, within_envelope

    combo, year, day, limits, desired, default_kw, kappa = _day_ahead(cid, date, policy)
    step = _resolution(resolution)
    rows = rows_for(limits, desired, year, day, step)
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["start_utc", "duration_s", "study_clock_utc+1", "stockholm", "limit_kw", "desired_export_kw"])
        for r in rows:
            w.writerow([r["start_utc"], r["duration_s"], r["study_clock"], r["stockholm"], r["limit_kw"],
                        r["desired_kw"]])
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="kapacitet-schedule-{cid}-{date}{_suffix(step)}.csv"'})
    return {"cid": cid, "date": date, "year": year, "basis": "dayahead", "policy": combo.policy, "rows": rows,
            **terms(step, desired, len(limits)), "within_envelope": within_envelope(rows, limits),
            "default_kw": default_kw, "kappa": kappa, "ocpp": _has_charging_profile(cid),
            "provenance": _provenance(combo.envelopes, combo.result("issued.json"))}


@router.get("/operation/schedule.xml")
def schedule_xml(cid: int = Query(..., ge=0), date: str = Query(..., pattern=DATE_PATTERN),
                 policy: str | None = Query(None, pattern=POLICY_PATTERN),
                 resolution: int | None = Query(None, description=RESOLUTION_DOC)) -> Response:
    """The day as an IEEE 2030.5 DERControlList in the CSIP-AUS profile (modelled, not certified),
    one DERControl per interval (900 s for a quarter-hour schedule), to read on screen. A preview
    is never sealed: `POST /operation/schedule/issue` is the same document, issued."""
    from ..engine.csipaus import der_control_list
    from ..engine.delivery import schedule as rows_for

    _, year, day, limits, desired, _, _ = _day_ahead(cid, date, policy)
    rows = rows_for(limits, desired, year, day, _resolution(resolution))
    return Response(der_control_list(cid, rows, int(time.time())), media_type="application/sep+xml")



def _has_charging_profile(cid: int) -> bool:
    """Whether GET /operation/schedule.ocpp.json has a verified import envelope to send for the house."""
    path = processed_ro("results", "operation", "dayahead", "import.json")
    if not path.exists():
        return False
    meta = json.loads(path.read_text(encoding="utf-8"))
    return bool(meta.get("verified")) and not meta.get("actual_violation_hours") and cid in meta.get("cids", [])


@router.get("/operation/schedule.ocpp.json")
def schedule_ocpp(cid: int = Query(..., ge=0), date: str = Query(..., pattern=DATE_PATTERN),
                  resolution: int | None = Query(None, description=RESOLUTION_DOC)) -> Response:
    """A preview, like the XML route's GET: never sealed and never sent anywhere. A house's EV charger
    limits for one date as an OCPP 1.6J `SetChargingProfile` request payload
    (modelled on OCPP 1.6J, not certified; no charge point is connected): a ChargePointMaxProfile
    on connector 0, in watts from local midnight, one period per schedule step. The limits are the
    import envelopes issued the day before; a step without one keeps the firm import limit (the
    charger's rating). Only the day-ahead basis is ever sent, and the payload is checked against
    OCPP 1.6J's field rules before it leaves."""
    from ..engine import ocpp
    from ..engine.delivery import day_of
    from ..engine.delivery import schedule as rows_for
    from .main import not_computed

    meta_path = processed_ro("results", "operation", "dayahead", "import.json")
    env_path = processed_ro("ui", "operation", "dayahead", "import_envelopes.npy")
    if not meta_path.exists() or not env_path.exists():
        raise not_computed("operation/dayahead/import.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if not meta.get("verified") or meta.get("actual_violation_hours"):
        raise _refusal(409, "import_unverified", hours=int(meta.get("planning_violation_hours", 0)
                                                           or meta.get("actual_violation_hours", 0)))
    if cid not in meta["cids"]:
        raise _refusal(404, "no_charger", cid=cid)
    year = int(meta["year"])
    try:
        day = day_of(year, Date.fromisoformat(date))
    except ValueError:
        raise _refusal(422, "outside_year", year=year) from None
    firm = float(meta["firm_kw"])
    env = np.asarray(np.load(env_path, mmap_mode="r")[meta["cids"].index(cid)], dtype=float)
    # the envelope as issued, not the schedule's rounded figure, so no step is sent above it
    steps = [{**r, "limit_kw": None if env[r["hour"]] >= firm - 1e-6 else float(env[r["hour"]])}
             for r in rows_for(env, None, year, day, _resolution(resolution))]
    step_s = steps[1]["start_epoch"] - steps[0]["start_epoch"] if len(steps) > 1 else 3600
    payload = ocpp.charging_profile(steps, firm, step_s)
    problems = ocpp.validate(payload)
    if problems:                       # a bug here, never the caller's: refuse rather than send it
        raise RuntimeError(f"the OCPP payload failed its own check: {problems}")
    return Response(json.dumps(payload, indent=1), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="kapacitet-ocpp-{cid}-{date}.json"',
                             "X-OCPP-Action": ocpp.ACTION, "X-Format-Notice": ocpp.NOTICE})

class Issue(BaseModel):
    cid: int = Field(..., ge=0)
    date: str = Field(..., pattern=DATE_PATTERN)
    policy: str | None = Field(None, pattern=POLICY_PATTERN)
    override_reason: str | None = Field(None, max_length=500, description="why the day is issued before the "
                                        "day-ahead market's result is known; sealed with the issue")
    resolution: Literal[15, 60] | None = Field(None, description=RESOLUTION_DOC)


def _suffix(step: int) -> str:
    """A downloaded file's step, in its name when it is not hourly, so the two steps never share one."""
    return "" if step == 60 else f"-{step}min"


def _market_terms(body: Issue, step: int, rows: list[dict]) -> dict:
    """What the issue owes to the market: when its result was known, whether the issue waited for
    it (or why not), and which records of market alternatives cover the day. Refuses an issue
    before the result unless it gives a reason; a day no record covers is flagged, never refused."""
    from ..engine.delivery import span

    day = Date.fromisoformat(body.date)
    local = dso.load().market_result_local
    g = market.gate(day, local)
    reason = (body.override_reason or "").strip()
    if not g["open"] and not reason:
        raise _refusal(409, "before_market", date=body.date, day_before=g["day_before"], local=local,
                       result_at=g["result_at"])
    covered = market.covering(day, cid=body.cid)
    flags = ([] if g["open"] else ["before_market_override"]) + ([] if covered else ["no_market_alternatives"])
    return {"market_result_at": g["result_at"], "market_result_local": local,
            "before_market_result": not g["open"], "override_reason": reason if not g["open"] else None,
            "market_alternatives": covered, "flags": flags,
            # the span the issued limits cover: the study day at 60 minutes, the delivery day at 15
            "delivery": span(rows, step)}


@router.post("/operation/schedule/issue")
def issue_schedule(body: Issue, idempotency_key: str | None = Header(None, pattern=IDEMPOTENCY_KEY)) -> Response:
    """Issue the day to the house's inverter: the DERControlList, sealed with what was sent, to
    whom and for which day. An issue that repeats an Idempotency-Key gets the document of the
    first one back, with its seal, and nothing new is sealed."""
    from ..engine.csipaus import der_control_list
    from ..engine.delivery import schedule as rows_for

    combo, year, day, limits, desired, _, _ = _day_ahead(body.cid, body.date, body.policy)
    step = _resolution(body.resolution)
    rows = rows_for(limits, desired, year, day, step)
    # an hourly issue keeps the id it always had; a quarter-hour one names its step before the
    # fingerprint, so neither id is a prefix of the other
    stepped = "" if step == 60 else f"{step}min-"
    base, fp = f"{body.cid}-{body.date}-{combo.policy}-", seals.fingerprint()["digest"][:8]
    item = f"{base}{stepped}{fp}"
    with _SEAL_LOCK:
        kept = None
        if idempotency_key:
            # the key alone names the issue, whatever its market terms were
            # and its step: the same key sent again with another resolution gets the first issue
            keyed = [e for e in seals.sealed_of_kind("schedule")
                     if e["entry"]["id"].startswith(base) and e["entry"]["id"].endswith(f"-k{idempotency_key}")
                     and re.match(rf"(\d+min-)?{fp}", e["entry"]["id"].removeprefix(base))]
            if keyed:
                entry, kept = keyed[-1]["entry"], keyed[-1]["record"]
        if kept is not None:
            # the limits sent the first time, issued when they were sealed, in the step they had
            step = int(kept.get("resolution_min", 60))
            duration = 60 * step
            rows = [{"duration_s": duration, **r} for r in kept["limits"]]
            terms = kept.get("market") or {}
        else:
            terms = _market_terms(body, step, rows)
            # the same day issued under other market terms (a reason, a record added since) is
            # another record, so it never collides with the first one's seal
            if terms["flags"] or terms["market_alternatives"]:
                item += f"-m{seals.digest(terms)[:8]}"
            if idempotency_key:
                item += f"-k{idempotency_key}"
            entry = seals.seal("schedule", item, {
                "cid": body.cid, "date": body.date, "policy": combo.policy, "basis": "dayahead",
                "resolution_min": step, "inputs_resolution_min": 60,
                "limits": [{"start_epoch": r["start_epoch"], "limit_kw": r["limit_kw"]} for r in rows],
                "market": terms})
    issued = int(datetime.fromisoformat(entry["at"]).timestamp())
    headers = _sealed_headers(entry, f"kapacitet-dercontrols-{body.cid}-{body.date}{_suffix(step)}.xml")
    headers["X-Issue-Flags"] = ",".join(terms.get("flags", []))
    return Response(der_control_list(body.cid, rows, issued), media_type="application/sep+xml", headers=headers)


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


# ---------------------------------------------------------------- the market alternatives

@router.get("/operation/market-alternatives")
def market_alternatives() -> dict:
    """Every record of the market alternatives the DSO consulted, newest first, each with its seal."""
    return {"entries": market.with_seals(), "scopes": list(market.SCOPES), "kinds": list(market.KINDS),
            "market_result_local": dso.load().market_result_local}


@router.post("/operation/market-alternatives")
def record_market_alternatives(body: market.RecordIn,
                               idempotency_key: str | None = Header(None, pattern=IDEMPOTENCY_KEY)) -> dict:
    """Record the alternatives studied before signing an agreement or before a period of
    activations: appended to market_alternatives.jsonl and sealed. Sent again under the same
    Idempotency-Key, it returns the first record and adds nothing."""
    try:
        entry, seal = market.record(body, idempotency_key)
    except market.MarketError as err:
        raise _refusal(422, err.code, **err.params) from err
    return {"entry": entry, "seal": {k: seal[k] for k in ("digest", "seq", "at")}}


# ---------------------------------------------------------------- the season in the shadow

@router.get("/operation/shadow")
def shadow(policy: str | None = Query(None, pattern=POLICY_PATTERN)) -> dict:
    """The season replayed day by day: what the day-ahead envelope did, beside perfect foresight,
    and the pilot's two criteria (no hour above a limit; every agreement within its level)."""
    from ..engine.delivery import go_no_go, season_days

    settings = dso.load()
    out: dict = {"policy": None, "agreed_level_pct": settings.agreed_level_pct, "bases": {}}
    year = None
    files = []
    for basis in ("dayahead", "foresight"):
        combo = resolve(basis, policy)
        files += [combo.result("days.json"), combo.result("days.npz")]
        out["policy"] = combo.policy
        days = json.loads(combo.result("days.json").read_text(encoding="utf-8"))
        year = int(days["year"])
        npz = np.load(combo.result("days.npz"))
        season = season_days(year, tuple(A("shadow_season_months")))
        verdict = go_no_go(days, {k: npz[k] for k in ("curtailed", "pv", "cids")}, season, settings.agreed_level_pct)
        # on the day-ahead basis, where the real year beat the plan it was issued against: the
        # year's audit (issued.json) and, day by day, the season's share of it (days.json)
        issued_path = combo.result("issued.json")
        issued = json.loads(issued_path.read_text(encoding="utf-8")) if issued_path.exists() else {}
        if issued:
            files.append(issued_path)
        exceed_keys = [k for k in ("exceedance_pv_customer_hours", "exceedance_load_customer_hours",
                                   "exceedance_net_hours") if k in days]
        out["bases"][basis] = {
            **verdict,
            "days": {k: [days[k][i] for i in season] for k in
                     ("curtailed_kwh", "desired_kwh", "hours_limited", "hours_necessary_limited",
                      "hours_unnecessary_limited", "violation_hours", *exceed_keys)},
            **({"exceedance": issued["exceedance"]} if issued.get("exceedance") is not None else {}),
            **({k: issued[k] for k in ("critical_hours", "curtailed_share_of_export_pct") if k in issued}),
        }
    out["year"] = year
    out["season_months"] = list(A("shadow_season_months"))
    out["first_day"] = int(season_days(year, tuple(A("shadow_season_months")))[0])
    fc = processed_ro("results", "forecast.json")
    if fc.exists():
        rows = json.loads(fc.read_text(encoding="utf-8")).get("rows", [])
        out["persistence"] = next((r for r in rows if r.get("scheme") == "persistence"), None)
        files.append(fc)
    out["provenance"] = _provenance(*files)
    return out


# ---------------------------------------------------------------- a meter

class MeterIn(BaseModel):
    cid: int = Field(..., ge=0)
    csv: str = Field(..., description="timestamp (ISO 8601 with offset), export_kw or export_w, or export_kwh "
                                      "(the energy of the quarter-hour the timestamp starts)")
    policy: str | None = Field(None, pattern=POLICY_PATTERN)
    resolution: Literal[15, 60] | None = Field(None, description=RESOLUTION_DOC)


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
        out = check(body.csv, limits, int(operation_index()["year"]), float(A("meter_breach_tolerance_w")),
                    _resolution(body.resolution))
    except MeterError as err:
        raise HTTPException(422, {"code": err.code, "message": str(err), "params": err.params}) from err
    return {"cid": body.cid, "policy": combo.policy, **out}


@router.get("/operation/meter-example")
def meter_example(cid: int = Query(..., ge=0), date: str = Query(..., pattern=DATE_PATTERN),
                  policy: str | None = Query(None, pattern=POLICY_PATTERN)) -> Response:
    """A synthetic day of 5-minute readings for a house, with one deliberate breach (labelled)."""
    from ..engine.delivery import meter_example as example

    _, year, day, limits, desired, _, _ = _day_ahead(cid, date, policy)
    if desired is None:
        raise HTTPException(404, {"code": "not_computed", "file": "ui/operation/desired.npy", "command": COMMAND})
    body = example(limits, desired, year, day)
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="kapacitet-meter-example-{cid}-{date}.csv"'})
