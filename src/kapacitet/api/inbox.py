"""Applications: formal requests to connect, decided by a rule when nobody has to look at them.

An assessment answers "what if"; an application asks for the connection. It rests on a saved
assessment, and one worker takes the applications in the order they arrived: if the queue has moved
since the assessment, it evaluates the request again against the grid as it now stands; then
`engine/rules.py` gives its verdict, but only when automatic decisions are on and a person has
signed the rule for that kind of request (dso.RuleSignature; otherwise `auto_off`, `unsigned_rule`
or `out_of_scope` send it to a person, with the rule's verdict kept as its `proposal`: in advisor
mode, the default, the rule only proposes). Firm and envelope verdicts go through `queue.decide`, which
re-verifies every weather year, under the queue's own write lock, so no manual decision runs at the
same time. Anything else waits for a person, who decides it with the queue's usual decision (the
rule never declines).

Every step is an event in data/requests/applications.jsonl (a table of the database with
KAPACITET_DATABASE_URL, kapacitet.store), appended and never rewritten; an
application's state is the fold of its events, and the indicators (time to a decision, the share
the rule decided, kW connected firm and with an envelope) are computed from the same events. Each
application is sealed when it arrives; each decision is sealed by the queue. The log keeps the
house's number and never its address, which is read from the network when an application is read.

An application's reference code is the household's key to its state, so it is shown once, to
whoever sent the application, and never kept: the log holds its SHA-256 and a hint of its first
four characters for the DSO's table. Codes from before that (`KP-XXXX-XXXX`, kept in plain) still
resolve.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import dso, seals, store
from ..engine import rules
from ..paths import DATA
from . import records

router = APIRouter(prefix="/api/v1", tags=["inbox"])
log = logging.getLogger("kapacitet.inbox")

ERRORS = {
    "unknown_assessment": "unknown assessment",
    "already_applied": "this assessment already backs application {app}",
    "application_open": "this house already has an application waiting: {app}",
    "unknown_application": "unknown application",
    "not_in_review": "this application is not waiting for a decision",
}
STATUSES = ("pending", "review", "firm", "nonfirm", "declined")
DECIDED = ("firm", "nonfirm", "declined")

# Crockford's base32: no I, L, O or U, so a code read aloud or typed from paper has one spelling.
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_C = f"[{CROCKFORD}]"
# 80 bits in four groups (KP-XXXX-XXXX-XXXX-XXXX), and the 32-bit hex codes issued before them
REFERENCE = re.compile(rf"^KP-{_C}{{4}}-{_C}{{4}}-{_C}{{4}}-{_C}{{4}}$|^KP-[0-9A-F]{{4}}-[0-9A-F]{{4}}$")

# receiving checks the log and then writes to it: two at once could both pass the checks
_RECEIVE_LOCK = threading.Lock()
_WAKE = threading.Event()
_STATE: dict = {"thread": None, "working": None}


def refusal(status: int, code: str, **params) -> HTTPException:
    return HTTPException(status, {"code": code, "message": ERRORS[code].format(**params), "params": params})


# ---------------------------------------------------------------- reference codes

def new_reference(key: str | None = None) -> str:
    """A fresh reference code: 80 bits in Crockford's base32, KP-XXXX-XXXX-XXXX-XXXX.

    The bits are random. An application sent with an idempotency key takes them from the key
    instead (HMAC-SHA256), so a retry whose first answer was lost gets the same code back; the key
    is then as secret as the code, and the log keeps only its hash too."""
    raw = (hmac.new(key.encode("utf-8"), b"kapacitet-reference", hashlib.sha256).digest()[:10] if key
           else secrets.token_bytes(10))
    n = int.from_bytes(raw, "big")
    chars = "".join(CROCKFORD[(n >> 5 * i) & 31] for i in reversed(range(16)))
    return "KP-" + "-".join(chars[i:i + 4] for i in range(0, 16, 4))


def normalize_reference(text: str) -> str:
    """A code as typed: upper case, and the letters base32 reads as digits (O as 0, I and L as 1)."""
    return text.strip().upper().replace("O", "0").replace("I", "1").replace("L", "1")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hint_label(hint: str | None) -> str:
    """How a refusal names an application without its code."""
    return f"KP-{hint}-…" if hint else "KP-…"


def find(reference: str) -> dict | None:
    """The application a code belongs to, compared in constant time; None when no code matches."""
    ref = normalize_reference(reference)
    if not REFERENCE.match(ref):
        return None
    digest = _sha256(ref)
    evs = events()
    found = None
    for e in evs:
        if e.get("event") != "received":
            continue
        kept = e.get("reference_sha256")
        legacy = e.get("reference")
        if (kept and hmac.compare_digest(kept, digest)) or (legacy and hmac.compare_digest(legacy.upper(), ref)):
            found = e["app"]
    app = fold(evs).get(found) if found else None
    return _addressed(app) if app else None


def log_path():
    return DATA / "requests" / "applications.jsonl"


def _log():
    """The log: applications.jsonl, or its table in the database (kapacitet.store)."""
    return store.applications(log_path())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- the event log (pure reading)

def events() -> list[dict]:
    return _log().entries()


def append(event: dict) -> dict:
    """Add one event; the sequence number and the time are the log's, not the caller's (numbered
    under the log's lock: a lock in this process with files, a transaction with the database)."""
    book = _log()
    with book.locked():
        event = {"seq": book.next_seq(), "at": _now(), **event}
        book.append(event, json.dumps(event, sort_keys=True))
    return event


def _addressed(app: dict) -> dict:
    """An application with its house's address, read from the network (older events kept it)."""
    return app if app.get("address") else {**app, "address": records.address_of(app.get("cid"))}


def fold(evs: list[dict]) -> dict[str, dict]:
    """Each application's state, in the order the applications arrived."""
    apps: dict[str, dict] = {}
    for e in evs:
        a = e.get("app")
        if e.get("event") == "received":
            legacy = e.get("reference") or ""
            apps[a] = {"id": a, "reference_hint": e.get("reference_hint") or legacy[3:7] or None,
                       "received": e["at"], "cid": e["cid"],
                       "address": e.get("address"), "source": e.get("source", "dso"), "request": e.get("request", {}),
                       "assessment": e["assessment"], "assessments": [e["assessment"]], "status": "pending",
                       "reasons": [], "proposal": None, "proposal_reasons": [], "decided": None,
                       "by": None, "pv_kw": 0.0, "evaluations": 1, "history": []}
        app = apps.get(a)
        if app is None:
            continue
        kind = e.get("event")
        if kind == "reevaluated":
            app["assessment"] = e["assessment"]
            app["assessments"].append(e["assessment"])
            app["evaluations"] += 1
        elif kind == "review":
            app.update(status="review", reasons=e.get("reasons", []), proposal=e.get("proposal"),
                       proposal_reasons=e.get("proposal_reasons", []))
        elif kind == "requeued":
            app.update(status="pending", reasons=[], proposal=None, proposal_reasons=[])
        elif kind == "decided":
            app.update(status=e["outcome"], decided=e["at"], by=e.get("by"), reasons=e.get("reasons", []),
                       pv_kw=float(e.get("pv_kw") or 0.0), assessment=e.get("assessment", app["assessment"]))
        app["history"].append({"at": e["at"], "event": kind, **({"outcome": e["outcome"]} if kind == "decided" else {})})
    return apps


def _seconds(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def kpis(apps: dict[str, dict]) -> dict:
    """What the inbox has done, from its own events."""
    decided = [a for a in apps.values() if a["status"] in DECIDED]
    waits = np.array([_seconds(a["decided"]) - _seconds(a["received"]) for a in decided], dtype=float)
    by_rule = sum(1 for a in decided if a["by"] == "rule")
    return {
        "received": len(apps),
        "pending": sum(1 for a in apps.values() if a["status"] == "pending"),
        "review": sum(1 for a in apps.values() if a["status"] == "review"),
        "decided": len(decided),
        "decided_by_rule": by_rule,
        "share_by_rule_pct": 100.0 * by_rule / len(decided) if decided else None,
        "median_s": float(np.median(waits)) if len(waits) else None,
        "p90_s": float(np.percentile(waits, 90)) if len(waits) else None,
        "firm": sum(1 for a in decided if a["status"] == "firm"),
        "nonfirm": sum(1 for a in decided if a["status"] == "nonfirm"),
        "declined": sum(1 for a in decided if a["status"] == "declined"),
        "firm_kw": round(sum(a["pv_kw"] for a in decided if a["status"] == "firm"), 3),
        "nonfirm_kw": round(sum(a["pv_kw"] for a in decided if a["status"] == "nonfirm"), 3),
    }


def applications() -> dict[str, dict]:
    return {k: _addressed(a) for k, a in fold(events()).items()}


# ---------------------------------------------------------------- receiving

def _replay(evs: list[dict], apps: dict, key: str) -> dict | None:
    """The application an idempotency key already sent, with its code when the key made it."""
    digest = _sha256(key)
    for e in evs:
        if e.get("event") != "received" or e.get("app") not in apps:
            continue
        if e.get("key_sha256") and hmac.compare_digest(e["key_sha256"], digest):
            return {**_addressed(apps[e["app"]]), "reference": new_reference(key)}
        if e.get("key") and hmac.compare_digest(e["key"], key):          # an application from before the hashes
            return {**_addressed(apps[e["app"]]), "reference": e.get("reference")}
    return None


def receive(assessment: str, source: str = "dso", key: str | None = None) -> dict:
    """Register an application on a saved assessment and wake the worker. Idempotent on `key`.

    The answer is the application's state with its `reference` code, the one time it is shown."""
    from ..engine import queue as qu

    rec = records.load(assessment)
    if rec is None:
        raise refusal(404, "unknown_assessment")
    with _RECEIVE_LOCK, _log().locked():
        evs = events()
        apps = fold(evs)
        if key:
            again = _replay(evs, apps, key)
            if again is not None:
                return again
        # an assessment backs one application, whether it was the first answer or a later one
        used = {e["assessment"]: e["app"] for e in evs if e.get("assessment") and e.get("app") in apps}
        if assessment in used:
            raise refusal(409, "already_applied", app=hint_label(apps[used[assessment]]["reference_hint"]))
        cid = int(rec["request"]["cid"])
        for app in apps.values():
            if app["cid"] == cid and app["status"] in ("pending", "review"):
                raise refusal(409, "application_open", app=hint_label(app["reference_hint"]))
        if any(it["cid"] == cid for it in qu.approved(qu.load())):
            raise HTTPException(409, {"code": "already_approved", "message": qu.ERRORS["already_approved"], "params": {}})
        app_id = uuid.uuid4().hex[:12]
        reference = new_reference(key)
        request = {k: v for k, v in rec["request"].items() if k != "baseline"}
        event = append({"app": app_id, "event": "received", "reference_sha256": _sha256(reference),
                        "reference_hint": reference[3:7], "assessment": assessment, "cid": cid, "source": source,
                        "request": request, **({"key_sha256": _sha256(key)} if key else {})})
    seals.seal("application", app_id, {k: event[k] for k in ("app", "reference_sha256", "reference_hint", "assessment",
                                                             "cid", "source", "request", "at")})
    start()
    return {**applications()[app_id], "reference": reference}


# ---------------------------------------------------------------- the worker

def busy() -> bool:
    """An application is being evaluated or decided: the queue is being written."""
    return _STATE["working"] is not None


def enabled() -> bool:
    """The worker runs unless KAPACITET_INBOX_WORKER=0 (the tests drive `process` themselves)."""
    return os.environ.get("KAPACITET_INBOX_WORKER", "1") != "0"


def start() -> None:
    """Start the worker if it is not running, and wake it either way."""
    if not enabled():
        return
    t = _STATE["thread"]
    if t is None or not t.is_alive():
        t = threading.Thread(target=_loop, name="kapacitet-inbox", daemon=True)
        _STATE["thread"] = t
        t.start()
    _WAKE.set()


def _loop() -> None:
    while True:
        _WAKE.wait()
        _WAKE.clear()
        while True:
            app = next((a for a in applications().values() if a["status"] == "pending"), None)
            if app is None:
                break
            _STATE["working"] = app["id"]
            try:
                process(app)
            except Exception as exc:  # the application waits for a person; the worker carries on
                log.exception("application %s failed", app["id"])
                append({"app": app["id"], "event": "review",
                        "reasons": [rules._reason("engine_error", detail=str(exc)[:200])]})
            finally:
                _STATE["working"] = None


def _stale(q: dict, rec: dict) -> str | None:
    """Why an assessment no longer describes the queue's grid, or None when it still does."""
    from ..engine import queue as qu

    try:
        qu._check(q, rec, "nonfirm")
    except qu.QueueError as exc:
        return exc.code
    return None


def process(app: dict, engine=None, make_record=None, evaluate=None) -> dict:
    """Decide one pending application. The engine and the record builder come from the API module,
    and can be given by a test."""
    from ..engine import queue as qu

    if engine is None or make_record is None or evaluate is None:
        from . import main
        engine, make_record, evaluate = main._engine, main.make_record, main.evaluate_record
    settings = dso.load()
    with qu._LOCK:                          # the queue's write lock: no manual decision meanwhile
        rec = records.load(app["assessment"])
        if rec is None:
            return append({"app": app["id"], "event": "review", "reasons": [rules._reason("assessment_missing")]})
        q = qu.load()
        why = _stale(q, rec)
        if why == "already_decided":        # decided directly on its assessment, by a person
            item = next(it for it in q["items"] if it["assessment"] == rec["id"])
            return append({"app": app["id"], "event": "decided", "outcome": item["decision"], "by": "dso",
                           "assessment": rec["id"], "pv_kw": item.get("pv_kw", 0.0)})
        if why == "already_approved":
            return append({"app": app["id"], "event": "review",
                           "reasons": [rules._reason("queue", detail="already_approved")]})
        if why is not None:                 # the queue moved, or started elsewhere: evaluate again
            baseline = "queue" if qu.approved(q) else (q["baseline"] or settings.inbox_baseline)
            request = {**rec["request"], "baseline": baseline}
            rec = make_record(uuid.uuid4().hex[:12], time.time(), request, evaluate(request))
            records.save(rec)
            append({"app": app["id"], "event": "reevaluated", "assessment": rec["id"], "because": why,
                    "baseline": baseline})
        th = dso.thresholds(settings)
        v = rules.verdict(rec["result"], th)
        sig = settings.rule_signature.model_dump(mode="json") if settings.rule_signature else None
        outside = "auto_off" if not settings.auto_decisions else rules.scope(
            sig, rec["request"], th, datetime.now(timezone.utc).date())
        if outside is not None:              # advisor mode: the rule only proposes, and a person decides
            return append({"app": app["id"], "event": "review", "assessment": rec["id"],
                           "reasons": [rules._reason(outside)], "proposal": v["outcome"],
                           "proposal_reasons": v["reasons"]})
        if v["outcome"] == "review":
            return append({"app": app["id"], "event": "review", "assessment": rec["id"], "reasons": v["reasons"]})
        eng = engine()
        try:
            item = qu.decide(eng["model"], eng["hp"], eng["ev"], rec, v["outcome"], by="rule",
                             signature=dso.signature_digest(sig))
        except qu.QueueError as exc:
            return append({"app": app["id"], "event": "review", "assessment": rec["id"],
                           "reasons": [rules._reason("queue", detail=exc.code)]})
        return append({"app": app["id"], "event": "decided", "outcome": v["outcome"], "by": "rule",
                       "assessment": rec["id"], "pv_kw": item.get("pv_kw", 0.0)})


def link_decision(assessment: str, decision: str, pv_kw: float, application: str | None = None) -> dict | None:
    """A person decided with the queue's own decision: the application it answers, named or found by
    its assessment, is decided with it."""
    apps = applications()
    app = apps.get(application) if application else next(
        (a for a in apps.values() if a["assessment"] == assessment and a["status"] in ("pending", "review")), None)
    if app is None or app["status"] not in ("pending", "review"):
        return None
    return append({"app": app["id"], "event": "decided", "outcome": decision, "by": "dso", "assessment": assessment,
                   "pv_kw": pv_kw})


# ---------------------------------------------------------------- HTTP

class ApplicationIn(BaseModel):
    assessment: str = Field(..., pattern="^[0-9a-f]{12}$", description="the saved assessment the application rests on")
    key: str | None = Field(None, min_length=22, max_length=64, pattern="^[A-Za-z0-9_-]+$",
                            description="idempotency key: the same key returns the same application")


def _public(app: dict) -> dict:
    """An application as the DSO's table shows it: the hint of its code, never the code."""
    return {k: v for k, v in app.items() if k not in ("history", "reference")} | {"history": app["history"][-12:]}


@router.post("/applications")
def post_application(body: ApplicationIn) -> dict:
    """Apply for the connection an assessment describes. The DSO's rule decides it, or a person does.
    The answer carries the reference code for the household, once."""
    app = receive(body.assessment, "dso", body.key)
    return {**_public(app), "reference": app.get("reference")}


def signature_state(s: dso.DsoSettings, today=None) -> str:
    """Whether the signed rule can decide anything today: `none` (nobody signed), `expired`,
    `thresholds_changed` (the thresholds in force are not the ones signed) or `in_force`."""
    sig = s.rule_signature
    if sig is None:
        return "none"
    if sig.valid_until < (today or datetime.now(timezone.utc).date()):
        return "expired"
    th = dso.thresholds(s)
    signed = sig.thresholds
    if any(abs(getattr(signed, k) - getattr(th, k)) > 1e-9
           for k in ("curtailment_mean_pct", "curtailment_p90_pct", "ev_unserved_pct")):
        return "thresholds_changed"
    return "in_force"


@router.get("/inbox")
def get_inbox(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)) -> dict:
    """The applications, newest first, a page at a time, with their state; the worker's status and
    the DSO's thresholds."""
    apps = list(applications().values())[::-1]
    s = dso.load()
    th = dso.thresholds(s)
    return {"items": [_public(a) for a in apps[offset:offset + limit]], "total": len(apps), "offset": offset,
            "limit": limit, "working": _STATE["working"], "auto_decisions": s.auto_decisions,
            "rule_signature": s.rule_signature.model_dump(mode="json") if s.rule_signature else None,
            "signature_state": signature_state(s),
            "thresholds": {"curtailment_mean_pct": th.curtailment_mean_pct, "curtailment_p90_pct": th.curtailment_p90_pct,
                           "ev_unserved_pct": th.ev_unserved_pct}}


@router.get("/inbox/kpis")
def get_kpis() -> dict:
    return kpis(applications())


@router.post("/applications/{app_id}/requeue")
def requeue(app_id: str) -> dict:
    """Send an application in review back to the worker: evaluated again if the queue has moved."""
    app = applications().get(app_id)
    if app is None:
        raise refusal(404, "unknown_application")
    if app["status"] != "review":
        raise refusal(409, "not_in_review")
    append({"app": app_id, "event": "requeued"})
    start()
    return _public(applications()[app_id])
