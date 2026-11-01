"""A queue of connection decisions: an approved request becomes part of the grid the next one meets.

First come, first served. An approved house keeps the envelope it was given, and every later
request is evaluated against the grid with that house in it, so it sees less headroom: the queue
effect. Approving re-verifies the request on every weather year with the nonlinear power flow and
recomputes each year's grid state with the house in it. Declining records the decision and changes
nothing in the grid.

Stored in data/requests/queue/:
  queue.json           the decisions, the published baseline the queue started from, and a version
                       that changes with every approval (an assessment made against an older
                       version has to be evaluated again before it can be approved);
  <assessment>.npz     an approved house's injection (p, q) for every weather year;
  state_<year>.npz     the grid state of each weather year with every approved house.
"""

from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime, timezone

import numpy as np

from .. import seals
from ..config import A, settings
from ..paths import DATA
from . import request as rq
from .scenario import customers

_LOCK = threading.RLock()
DECISIONS = ("firm", "nonfirm", "declined")


ERRORS = {
    "unknown_decision": "unknown decision {decision}",
    "already_approved": "this house is already approved in the queue",
    "already_decided": "this assessment has already been decided",
    "queue_changed": "the queue has changed since this assessment: evaluate it again",
    "evaluate_against_queue": "the queue already holds approved requests: evaluate against the queue",
    "other_situation": "the queue started from the other grid situation",
    "static_rejects_firm": "the static study rejects this request, so it cannot be connected firm",
    "still_violates": "the full-year check still finds a violation, so it is not approved",
}


class QueueError(ValueError):
    """A decision the queue refuses: a code the interface translates (`err.<code>`), the reason in
    English for the log and the API's `message`."""

    def __init__(self, code: str, **params):
        super().__init__(ERRORS[code].format(**params))
        self.code = code
        self.params = params


def folder():
    return DATA / "requests" / "queue"


def load() -> dict:
    path = folder() / "queue.json"
    if not path.exists():
        return {"version": 0, "baseline": None, "items": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save(q: dict) -> None:
    folder().mkdir(parents=True, exist_ok=True)
    tmp = folder() / "queue.json.tmp"
    tmp.write_text(json.dumps(q, indent=1), encoding="utf-8")
    tmp.replace(folder() / "queue.json")


def approved(q: dict) -> list[dict]:
    return [it for it in q["items"] if it["decision"] in ("firm", "nonfirm")]


_CACHE: dict = {"version": None, "states": None, "overrides": None}


def context() -> rq.Context | None:
    """The grid as the queue has left it, or None when nothing has been decided yet."""
    with _LOCK:
        q = load()
        if q["baseline"] is None:
            return None
        items = approved(q)
        label = {"version": q["version"], "approved": len(items), "baseline": q["baseline"]}
        if not items:
            return rq.Context(q["baseline"], label=label)
        if _CACHE["version"] != q["version"]:
            years = settings().weather_years
            states, overrides = {}, {}
            for y in years:
                path = folder() / f"state_{y}.npz"
                if path.exists():
                    d = np.load(path)
                    states[y] = {k: d[k] for k in d.files}
            for it in items:
                d = np.load(folder() / f"{it['assessment']}.npz")
                overrides[it["cid"]] = {
                    "p": {y: d[f"p_{y}"].astype(float) for y in years if f"p_{y}" in d.files},
                    "q": {y: d[f"q_{y}"].astype(float) for y in years if f"q_{y}" in d.files},
                    "pv_kw": it.get("pv_kw", 0.0), "ev_kw": it.get("ev_kw", 0.0), "hp": it.get("hp", False)}
            _CACHE.update(version=q["version"], states=states, overrides=overrides)
        return rq.Context(q["baseline"], overrides=_CACHE["overrides"], states=_CACHE["states"], label=label)


def _check(q: dict, record: dict, decision: str) -> str:
    """Refuse a decision on an answer that no longer describes the grid; return the queue's baseline."""
    if decision not in DECISIONS:
        raise QueueError("unknown_decision", decision=repr(decision))
    req, res = record["request"], record["result"]
    if any(it["cid"] == req["cid"] for it in approved(q)):
        raise QueueError("already_approved")
    if any(it["assessment"] == record["id"] for it in q["items"]):
        raise QueueError("already_decided")
    asked = req["baseline"]
    if asked == "queue":
        if (res.get("queue") or {}).get("version") != q["version"]:
            raise QueueError("queue_changed")
        return q["baseline"]
    if approved(q):
        raise QueueError("evaluate_against_queue")
    if q["baseline"] not in (None, asked):
        raise QueueError("other_situation")
    return asked


def decide(model, hp: np.ndarray, ev: np.ndarray, record: dict, decision: str, note: str = "",
           emit=None, by: str = "dso") -> dict:
    """Record a decision on a saved assessment; an approval re-verifies it and updates the grid.
    `by` says who decided: the DSO's planner, or its rule (api/inbox.py), which never declines."""
    if by == "rule" and decision == "declined":
        raise ValueError("the automatic rule never declines")
    emit = emit or (lambda *_: None)
    with _LOCK:
        q = load()
        base = _check(q, record, decision)
        req, res = record["request"], record["result"]
        cid = int(req["cid"])
        item = {"assessment": record["id"], "cid": cid, "address": res.get("customer", {}).get("address"),
                "decision": decision, "by": by, "note": note.strip()[:500],
                "decided": datetime.now(timezone.utc).isoformat(), "request": req,
                "summary": res.get("summary", {})}
        if decision == "declined":
            q["baseline"] = base
            q["items"].append(item)
            _save(q)
            seals.seal("decision", record["id"], item)
            return item

        static = res.get("static", {})
        if decision == "firm" and not all(v.get("approved") for v in static.values()):
            raise QueueError("static_rejects_firm")
        ctx = context() or rq.Context(base)
        r = rq.Request(cid=cid, pv_kwp=float(req.get("pv_kwp", 0)), ev_kw=float(req.get("ev_kw", 0)),
                       hp=bool(req.get("hp", False)), bat_kwh=float(req.get("bat_kwh", 0)), baseline=base,
                       ev_phases=int(req.get("ev_phases", 0)))
        years = ctx.years(settings().weather_years)
        emit("verify", {"years": len(years)})
        out, detail = rq.run(model, r, hp, ev, ctx=ctx, verify_years=years)
        left = out["verification"].get("all_years", {})
        if any(left.get(k, 0) for k in ("export_violations_after", "import_violations_after", "battery_violations_after")):
            raise QueueError("still_violates")

        hp_new = bool(req.get("hp", False)) and not bool(hp[cid])
        tanphi = np.tan(np.arccos(A("load_power_factor")))
        inj = {}
        for y in years:
            d = detail[y]
            if decision == "firm":       # no envelope: everything it asked for, as the static study approved
                demand = d["load"] + d["hp"] + d["ev_want"]
                inj[y] = (d["pv"] - demand, -demand * tanphi)
            else:
                inj[y] = (rq._injection(d), rq._reactive(d))

        states = {}
        for i, y in enumerate(years):
            p, qq = ctx.pq(model, y, hp, ev)
            p, qq = p.copy(), qq.copy()
            p[cid], qq[cid] = inj[y]
            states[y] = rq.build_state(model, p, qq)
            emit("state", {"year": y, "done": i + 1, "of": len(years)})

        folder().mkdir(parents=True, exist_ok=True)
        np.savez(folder() / f"{record['id']}.npz",
                 **{f"p_{y}": inj[y][0].astype(np.float32) for y in years},
                 **{f"q_{y}": inj[y][1].astype(np.float32) for y in years})
        for y, st in states.items():
            np.savez(folder() / f"state_{y}.npz", **st)
        cust = customers()
        cap = min(r.pv_kwp / A("pv_dc_ac_ratio"), float(cust.loc[cid, "fuse_kva"])) if r.pv_kwp > 0 else 0.0
        item.update(pv_kw=cap, ev_kw=r.ev_kw, hp=hp_new, verification=out["verification"],
                    summary=out["summary"])
        q["items"].append(item)
        q["baseline"] = base
        q["version"] += 1
        _save(q)
        _CACHE["version"] = None
        seals.seal("decision", record["id"], item)
        return item


def reset() -> None:
    """Empty the queue. The assessments themselves stay saved in data/requests/, and what the queue
    held is sealed first, so its decisions do not leave the record with it."""
    with _LOCK:
        q = load()
        if q["items"]:
            stamp = datetime.now(timezone.utc)
            seals.seal("queue_reset", stamp.strftime("%Y%m%dT%H%M%S%fZ"),
                       {"reset_at": stamp.isoformat(), "version": q["version"], "baseline": q["baseline"],
                        "items": q["items"]})
        if folder().exists():
            shutil.rmtree(folder())
        _CACHE.update(version=None, states=None, overrides=None)
