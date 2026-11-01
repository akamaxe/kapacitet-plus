"""Applications: formal requests to connect, decided by a rule when nobody has to look at them.

An assessment answers "what if"; an application asks for the connection. It rests on a saved
assessment, and one worker takes the applications in the order they arrived: if the queue has moved
since the assessment, it evaluates the request again against the grid as it now stands; then
`engine/rules.py` gives its verdict. Firm and envelope verdicts go through `queue.decide`, which
re-verifies every weather year, under the queue's own write lock, so no manual decision runs at the
same time. Anything else waits for a person, who decides it with the queue's usual decision (the
rule never declines).

Every step is an event in data/requests/applications.jsonl, appended and never rewritten; an
application's state is the fold of its events, and the indicators (time to a decision, the share
the rule decided, kW connected firm and with an envelope) are computed from the same events. Each
application is sealed when it arrives; each decision is sealed by the queue.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import dso, seals
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

_LOG_LOCK = threading.Lock()
# receiving checks the log and then writes to it: two at once could both pass the checks
_RECEIVE_LOCK = threading.Lock()
_WAKE = threading.Event()
_STATE: dict = {"thread": None, "working": None}


def refusal(status: int, code: str, **params) -> HTTPException:
    return HTTPException(status, {"code": code, "message": ERRORS[code].format(**params), "params": params})


def log_path():
    return DATA / "requests" / "applications.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- the event log (pure reading)

def events() -> list[dict]:
    path = log_path()
    if not path.exists():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


def append(event: dict) -> dict:
    """Add one event; the sequence number and the time are the log's, not the caller's."""
    with _LOG_LOCK:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        seq = sum(1 for _ in path.open(encoding="utf-8")) + 1 if path.exists() else 1
        event = {"seq": seq, "at": _now(), **event}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
    return event


def fold(evs: list[dict]) -> dict[str, dict]:
    """Each application's state, in the order the applications arrived."""
    apps: dict[str, dict] = {}
    for e in evs:
        a = e.get("app")
        if e.get("event") == "received":
            apps[a] = {"id": a, "reference": e.get("reference"), "received": e["at"], "cid": e["cid"],
                       "address": e.get("address"), "source": e.get("source", "dso"), "request": e.get("request", {}),
                       "assessment": e["assessment"], "assessments": [e["assessment"]], "status": "pending",
                       "reasons": [], "decided": None,
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
            app.update(status="review", reasons=e.get("reasons", []))
        elif kind == "requeued":
            app.update(status="pending", reasons=[])
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
    return fold(events())


# ---------------------------------------------------------------- receiving

def receive(assessment: str, source: str = "dso", key: str | None = None) -> dict:
    """Register an application on a saved assessment and wake the worker. Idempotent on `key`."""
    from ..engine import queue as qu

    rec = records.load(assessment)
    if rec is None:
        raise refusal(404, "unknown_assessment")
    with _RECEIVE_LOCK:
        evs = events()
        apps = fold(evs)
        if key:
            for e in evs:
                if e.get("event") == "received" and e.get("key") == key and e["app"] in apps:
                    return apps[e["app"]]
        # an assessment backs one application, whether it was the first answer or a later one
        used = {e["assessment"]: e["app"] for e in evs if e.get("assessment") and e.get("app") in apps}
        if assessment in used:
            raise refusal(409, "already_applied", app=apps[used[assessment]]["reference"])
        cid = int(rec["request"]["cid"])
        for app in apps.values():
            if app["cid"] == cid and app["status"] in ("pending", "review"):
                raise refusal(409, "application_open", app=app["reference"])
        if any(it["cid"] == cid for it in qu.approved(qu.load())):
            raise HTTPException(409, {"code": "already_approved", "message": qu.ERRORS["already_approved"], "params": {}})
        app_id = uuid.uuid4().hex[:12]
        reference = "KP-" + secrets.token_hex(2).upper() + "-" + secrets.token_hex(2).upper()
        request = {k: v for k, v in rec["request"].items() if k != "baseline"}
        event = append({"app": app_id, "event": "received", "reference": reference, "assessment": assessment,
                        "cid": cid, "address": rec["result"].get("customer", {}).get("address"), "source": source,
                        "request": request, **({"key": key} if key else {})})
    seals.seal("application", app_id, {k: event[k] for k in ("app", "reference", "assessment", "cid", "source", "request", "at")})
    start()
    return fold(events())[app_id]


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
        if not settings.auto_decisions:
            return append({"app": app["id"], "event": "review", "assessment": rec["id"],
                           "reasons": [rules._reason("auto_off")]})
        v = rules.verdict(rec["result"], dso.thresholds(settings))
        if v["outcome"] == "review":
            return append({"app": app["id"], "event": "review", "assessment": rec["id"], "reasons": v["reasons"]})
        eng = engine()
        try:
            item = qu.decide(eng["model"], eng["hp"], eng["ev"], rec, v["outcome"], by="rule")
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
    key: str | None = Field(None, max_length=64, pattern="^[A-Za-z0-9_-]+$",
                            description="idempotency key: the same key returns the same application")


def _public(app: dict) -> dict:
    return {k: v for k, v in app.items() if k != "history"} | {"history": app["history"][-12:]}


@router.post("/applications")
def post_application(body: ApplicationIn) -> dict:
    """Apply for the connection an assessment describes. The DSO's rule decides it, or a person does."""
    return _public(receive(body.assessment, "dso", body.key))


@router.get("/inbox")
def get_inbox(limit: int = Query(100, ge=1, le=500)) -> dict:
    """Every application, newest first, with its state; the worker's status and the DSO's thresholds."""
    apps = list(applications().values())[::-1][:limit]
    s = dso.load()
    th = dso.thresholds(s)
    return {"items": [_public(a) for a in apps], "working": _STATE["working"], "auto_decisions": s.auto_decisions,
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
