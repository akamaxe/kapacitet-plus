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

from ..config import A, settings
from ..paths import DATA
from . import request as rq
from .scenario import customers

_LOCK = threading.RLock()
DECISIONS = ("firm", "nonfirm", "declined")


class QueueError(ValueError):
    """A decision the queue refuses, with the reason in words for the person deciding."""


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


def in_queue(cid: int) -> bool:
    return any(it["cid"] == cid for it in approved(load()))


def _check(q: dict, record: dict, decision: str) -> str:
    """Refuse a decision on an answer that no longer describes the grid; return the queue's baseline."""
    if decision not in DECISIONS:
        raise QueueError(f"unknown decision {decision!r}")
    req, res = record["request"], record["result"]
    if any(it["cid"] == req["cid"] for it in approved(q)):
        raise QueueError("this house is already approved in the queue")
    if any(it["assessment"] == record["id"] for it in q["items"]):
        raise QueueError("this assessment has already been decided")
    asked = req["baseline"]
    if asked == "queue":
        if (res.get("queue") or {}).get("version") != q["version"]:
            raise QueueError("the queue has changed since this assessment: evaluate it again")
        return q["baseline"]
    if approved(q):
        raise QueueError("the queue already holds approved requests: evaluate against the queue")
    if q["baseline"] not in (None, asked):
        raise QueueError("the queue started from the other grid situation")
    return asked


def decide(model, hp: np.ndarray, ev: np.ndarray, record: dict, decision: str, note: str = "",
           emit=None) -> dict:
    """Record a decision on a saved assessment; an approval re-verifies it and updates the grid."""
    emit = emit or (lambda *_: None)
    with _LOCK:
        q = load()
        base = _check(q, record, decision)
        req, res = record["request"], record["result"]
        cid = int(req["cid"])
        item = {"assessment": record["id"], "cid": cid, "address": res.get("customer", {}).get("address"),
                "decision": decision, "note": note.strip()[:500],
                "decided": datetime.now(timezone.utc).isoformat(), "request": req,
                "summary": res.get("summary", {})}
        if decision == "declined":
            q["baseline"] = base
            q["items"].append(item)
            _save(q)
            return item

        static = res.get("static", {})
        if decision == "firm" and not all(v.get("approved") for v in static.values()):
            raise QueueError("the static study rejects this request, so it cannot be connected firm")
        ctx = context() or rq.Context(base)
        r = rq.Request(cid=cid, pv_kwp=float(req.get("pv_kwp", 0)), ev_kw=float(req.get("ev_kw", 0)),
                       hp=bool(req.get("hp", False)), bat_kwh=float(req.get("bat_kwh", 0)), baseline=base)
        years = ctx.years(settings().weather_years)
        emit("verify", {"years": len(years)})
        out, detail = rq.run(model, r, hp, ev, ctx=ctx, verify_years=years)
        left = out["verification"].get("all_years", {})
        if any(left.get(k, 0) for k in ("export_violations_after", "import_violations_after", "battery_violations_after")):
            raise QueueError("the full-year check still finds a violation, so it is not approved")

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
        return item


def reset() -> None:
    """Empty the queue. The assessments themselves stay saved in data/requests/."""
    with _LOCK:
        if folder().exists():
            shutil.rmtree(folder())
        _CACHE.update(version=None, states=None, overrides=None)
