"""The applicant's portal: the addresses one can apply for, the answer for one's own house, and the
state of one's own application. Nothing else of the DSO's view is served here.

No name or contact details are asked for: a household picks its address, asks, and applies; the
application is stored with the house's number in the network and its request (never the address,
which is read from the network when the application is read), and is then known by its reference
code, which is all the status page needs. The code is
80 bits and the server keeps only its hash (api/inbox.py); api/guard.py allows ten lookups a
minute from one address, so it cannot be guessed. The grid the
answer is computed against is the DSO's choice, decided here (the queue once it holds approvals,
otherwise the DSO's published situation), never the household's.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import brand, dso
from ..config import A
from ..engine import rules
from . import inbox

router = APIRouter(prefix="/api/v1/apply", tags=["apply"])
# KP-XXXX-XXXX-XXXX-XXXX in Crockford's base32, or a KP-XXXX-XXXX code from before
REFERENCE = inbox.REFERENCE


class Ask(BaseModel):
    cid: int = Field(..., ge=0, description="the house, from /apply/houses")
    pv_kwp: float = Field(0.0, ge=0, le=40)
    ev_kw: float = Field(0.0, ge=0, le=22)
    hp: bool = False
    bat_kwh: float = Field(0.0, ge=0, le=30)
    ev_phases: int = Field(0, ge=0, le=3)


class Submit(BaseModel):
    assessment: str = Field(..., pattern="^[0-9a-f]{12}$")
    key: str = Field(..., min_length=22, max_length=64, pattern="^[A-Za-z0-9_-]+$",
                     description="idempotency key: sending the same application twice returns the first")


def _baseline() -> str:
    from ..engine import queue as qu

    q = qu.load()
    return "queue" if qu.approved(q) else (q["baseline"] or dso.load().inbox_baseline)


@router.get("/brand")
def brand_info() -> dict:
    """Whose portal this is: the DSO's name (the product's when none is set), how to reach it, its
    accent and its logo's address and digest. Nothing else of the DSO's settings reaches the portal."""
    return brand.public()


@router.get("/brand/logo.png")
def brand_logo(if_none_match: str | None = Header(None)) -> Response:
    """The DSO's logo, as cleaned when it was set; its ETag is its digest."""
    logo = brand.load_logo()
    if logo is None:
        raise HTTPException(404, {"code": "logo_missing", "message": brand.ERRORS["logo_missing"], "params": {}})
    etag = f'"{logo.sha256}"'
    headers = {"ETag": etag, "X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache",
               "Content-Security-Policy": "default-src 'none'; sandbox"}
    if if_none_match and etag in [t.strip() for t in if_none_match.split(",")]:
        return Response(status_code=304, headers=headers)
    return Response(logo.png, media_type="image/png", headers=headers)


@router.get("/houses")
def houses() -> dict:
    """The addresses a household can apply for, and the limits of the form; nothing else about them."""
    from ..paths import processed_ro

    # checked before the engine is imported: on a fresh server that import takes seconds
    if not processed_ro("grid", "customers.parquet").exists():
        raise HTTPException(404, {"code": "not_computed", "file": "grid/customers.parquet",
                                  "command": "python -m kapacitet build", "message": "the network has not been built"})
    from ..engine.scenario import customers

    cust = customers()
    items = [{"cid": int(r.cid), "address": (r.address or None) if isinstance(r.address, str) else None}
             for r in cust[["cid", "address"]].itertuples()]
    items.sort(key=lambda h: (h["address"] is None, h["address"] or "", h["cid"]))
    return {"items": items,
            "form": {"pv_min_kwp": A("pv_min_kwp"), "battery_max_kwh": A("battery_max_kwh"),
                     "ev_kw": [A("ev_charger_single_phase_kw"), A("ev_charger_kw")]}}


@router.post("/evaluate")
def evaluate(body: Ask) -> dict:
    """Start the answer for one house, against the grid as the DSO sees it now."""
    from . import main

    started = main.create_request(main.RequestIn(**body.model_dump(), baseline=_baseline()), source="portal")
    return {"id": started["id"], "events": f"/api/v1/apply/evaluate/{started['id']}/events"}


@router.get("/evaluate/{job_id}/events")
async def evaluate_events(job_id: str):
    from . import main

    return await main.request_events(job_id)


@router.delete("/evaluate/{job_id}")
def cancel(job_id: str) -> dict:
    """Stop an answer that is waiting or being computed (a household that changed its mind frees
    the slot for the next one); nothing of it is kept."""
    from . import main

    return main.cancel_request(job_id, source="portal")


@router.get("/evaluate/{job_id}")
def answer(job_id: str) -> dict:
    """The answer in the terms a household needs: firm or with a limit, what the limit costs, what the
    DSO's rule would decide, and what connecting now is worth against waiting."""
    from datetime import datetime, timezone

    from ..engine.economics import owner_case
    from . import main

    try:
        rec = main._finished(job_id)
    except HTTPException as exc:
        if exc.status_code == 202:
            raise HTTPException(409, "still running") from exc
        raise
    res = rec["result"]
    static = res.get("static") or {}
    risk = res.get("risk") or {}
    summary = res.get("summary") or {}
    s = dso.load()
    years = s.deferral_years
    return {
        "id": rec["id"], "address": (res.get("customer") or {}).get("address"),
        "request": {k: rec["request"].get(k) for k in ("cid", "pv_kwp", "ev_kw", "hp", "bat_kwh", "ev_phases")},
        "baseline": rec["request"].get("baseline"),
        "firm": bool(static) and all(p.get("approved") for p in static.values()),
        "static": {k: {"approved": bool(v.get("approved")), "reason_code": v.get("reason_code"),
                       "reason_value": v.get("reason_value")} for k, v in static.items()},
        "envelope": {"curtailment_mean_pct": risk.get("mean_pct"), "curtailment_p90_pct": risk.get("p90_pct"),
                     "hours_limited_ref": summary.get("pv_hours_limited_ref"),
                     "ev_unmet_pct": summary.get("ev_unmet_pct"), "hp_unmet_pct": summary.get("hp_unmet_pct")},
        "fuse": res.get("fuse"), "phases_ok": (res.get("phases") or {}).get("ok", True),
        # "auto": the rule would decide it on its own, switched on and signed for this request
        "rule": {**rules.verdict(res, dso.thresholds(s)), "auto": _decides_alone(s, rec["request"])},
        "owner": owner_case(res, years, A("household_discount_rate_pct"), datetime.now(timezone.utc).year),
        "provenance": rec.get("provenance"),
    }


def _decides_alone(s, request: dict) -> bool:
    """Whether the DSO's rule would decide this request without a person (dso.RuleSignature)."""
    if not s.auto_decisions:
        return False
    sig = s.rule_signature.model_dump(mode="json") if s.rule_signature else None
    return rules.scope(sig, request, dso.thresholds(s), datetime.now(timezone.utc).date()) is None


def _status(app: dict, reference: str | None) -> dict:
    keep = ("status", "received", "decided", "by", "address", "request")
    return {"reference": reference, **{k: app.get(k) for k in keep},
            "reasons": [{"code": r.get("code"), "params": r.get("params", {})} for r in app.get("reasons", [])]}


@router.post("/submit")
def submit(body: Submit) -> dict:
    """Apply for the connection the answer describes. The reference code is the household's key:
    this answer is the one place it is shown (a retry with the same key shows it again)."""
    app = inbox.receive(body.assessment, "portal", body.key)
    return _status(app, app.get("reference"))


@router.get("/status/{reference}")
def status(reference: str) -> dict:
    """One application's state, read with its reference code and nothing else."""
    ref = inbox.normalize_reference(reference)
    app = inbox.find(ref) if REFERENCE.match(ref) else None
    if app is None:
        raise inbox.refusal(404, "unknown_application")
    return _status(app, ref)
