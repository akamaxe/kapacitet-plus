"""A queue of connection decisions: an approved request becomes part of the grid the next one meets.

First come, first served. An approved house keeps the envelope it was given, and every later
request is evaluated against the grid with that house in it, so it sees less headroom: the queue
effect. Approving re-verifies the request on every weather year with the nonlinear power flow and
recomputes each year's grid state with the house in it. Declining records the decision and changes
nothing in the grid.

Stored in data/requests/queue/:
  queue.json             the decisions, the published baseline the queue started from, and a version
                         that changes with every approval (an assessment made against an older
                         version has to be evaluated again before it can be approved);
  <assessment>.npz       an approved house's injection (p, q) for every weather year;
  state_<year>.v<n>.npz  the grid state of each weather year with every approved house, as of
                         version n (`state_<year>.npz` in a queue from before the versions);
  seal_pending.json      the decision whose seal is still owed, while it is being written.

queue.json is the commit point. An approval writes the new version's files first, each atomically,
then queue.json, atomically: a crash before that leaves the queue as it was, and the half-written
version is never read (recover() removes it). The decision is sealed after the commit; the marker,
written before it, says which seal is owed, and recover() makes it good at the server's next start.
A decision keeps the house's number, not its address (the reader takes the address from the network).

Every function takes the queue's folder (`where`), data/requests/queue by default. A study's station
keeps one queue per end of its MV band (`band_folder`), each on the model at that end.

`rebuild` (a study's `study sync`) replaces a queue with one made from the DSO's own list of approved
connections, in order: each is reserved as `decide` would (`_reserve`), but one that fails the
re-verification on every weather year is kept and flagged, since it is connected whatever the
check says. The new queue is written in full into `<folder>.next`, byte for byte the same for the
same list, and swapped in by two renames (recover() finishes a swap a crash interrupted).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .. import seals
from ..atomic import savez_atomic, write_json_atomic
from ..cache import on_clear
from ..config import A, settings
from ..paths import DATA
from . import request as rq
from .scenario import customers

_LOCK = threading.RLock()
DECISIONS = ("firm", "nonfirm", "declined")
PENDING = "seal_pending.json"
log = logging.getLogger(__name__)
_STATE = re.compile(r"^state_(\d{4})(?:\.v(\d+))?\.npz$")
_INJECTION = re.compile(r"^[0-9a-f]{12}\.npz$")


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
# what `rebuild` flags in a connection the DSO approved, instead of leaving it out
FLAGS = ("still_violates", "static_rejects_firm")


class QueueError(ValueError):
    """A decision the queue refuses: a code the interface translates (`err.<code>`), the reason in
    English for the log and the API's `message`."""

    def __init__(self, code: str, **params):
        super().__init__(ERRORS[code].format(**params))
        self.code = code
        self.params = params


def folder():
    return DATA / "requests" / "queue"


def band_folder(vm_pu: float) -> Path:
    """A study station's queue at one end of its MV band (request.band_name's suffix)."""
    return DATA / "requests" / f"queue_mv{int(round(float(vm_pu) * 100)):03d}"


def _at(where: Path | None) -> Path:
    return Path(where) if where is not None else folder()


def load(where: Path | None = None) -> dict:
    path = _at(where) / "queue.json"
    if not path.exists():
        return {"version": 0, "baseline": None, "items": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save(q: dict, where: Path | None = None) -> None:
    """Write queue.json atomically: the commit point of every decision."""
    write_json_atomic(_at(where) / "queue.json", q)


def approved(q: dict) -> list[dict]:
    return [it for it in q["items"] if it["decision"] in ("firm", "nonfirm")]


def _state_path(year: int, version: int, where: Path | None = None) -> Path:
    return _at(where) / f"state_{year}.v{version}.npz"


def _state_file(year: int, version: int, where: Path | None = None) -> Path | None:
    """The state file of a year as of `version`, or of a queue from before the versions."""
    for path in (_state_path(year, version, where), _at(where) / f"state_{year}.npz"):
        if path.exists():
            return path
    return None


def state_files(where: Path | None = None) -> list[Path]:
    """The grid-state files the queue's current version reads (provenance of an assessment)."""
    root = _at(where)
    if not root.exists():
        return []
    version = load(root).get("version", 0)
    files = sorted(root.glob(f"state_*.v{version}.npz"))
    return files or sorted(p for p in root.glob("state_*.npz") if (m := _STATE.match(p.name)) and m[2] is None)


def _load_npz(path: Path) -> dict[str, np.ndarray]:
    with np.load(path) as d:          # closed at once: Windows will not replace or remove an open file
        return {k: d[k] for k in d.files}


# per queue folder: the version its states and injections were read for
_CACHE: dict[str, dict] = {}
on_clear(_CACHE.clear)


def _forget(where: Path | None = None) -> None:
    _CACHE.pop(str(_at(where)), None)


def _override(it: dict, d: dict[str, np.ndarray], years) -> dict:
    """An approved house as the next request meets it: its injection per year and what the static
    study counts for it."""
    return {"p": {y: d[f"p_{y}"].astype(float) for y in years if f"p_{y}" in d},
            "q": {y: d[f"q_{y}"].astype(float) for y in years if f"q_{y}" in d},
            "pv_kw": it.get("pv_kw", 0.0), "ev_kw": it.get("ev_kw", 0.0), "hp": it.get("hp", False)}


def context(where: Path | None = None) -> rq.Context | None:
    """The grid as the queue has left it, or None when nothing has been decided yet."""
    root = _at(where)
    with _LOCK:
        q = load(root)
        if q["baseline"] is None:
            return None
        items = approved(q)
        label = {"version": q["version"], "approved": len(items), "baseline": q["baseline"]}
        if not items:
            return rq.Context(q["baseline"], label=label)
        held = _CACHE.get(str(root))
        if held is None or held["version"] != q["version"]:
            years = settings().weather_years
            states, overrides = {}, {}
            for y in years:
                path = _state_file(y, q["version"], root)
                if path is not None:
                    states[y] = _load_npz(path)
            for it in items:
                overrides[it["cid"]] = _override(it, _load_npz(root / f"{it['assessment']}.npz"), years)
            held = _CACHE[str(root)] = {"version": q["version"], "states": states, "overrides": overrides}
        return rq.Context(q["baseline"], overrides=held["overrides"], states=held["states"], label=label)


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


def engine_request(req: dict, baseline: str) -> rq.Request:
    """The engine's request from a saved one (a RequestIn's fields, or a study's case)."""
    return rq.Request(cid=int(req["cid"]), pv_kwp=float(req.get("pv_kwp", 0) or 0),
                      ev_kw=float(req.get("ev_kw", 0) or 0), hp=bool(req.get("hp", False)),
                      bat_kwh=float(req.get("bat_kwh", 0) or 0), baseline=baseline,
                      ev_phases=int(req.get("ev_phases", 0) or 0), pv_kw_ac=float(req.get("pv_kw_ac", 0) or 0),
                      pv_phases=int(req.get("pv_phases", 0) or 0))


def _reserve(model, hp: np.ndarray, ev: np.ndarray, ctx: rq.Context, r: rq.Request, decision: str,
             emit=None) -> dict:
    """What an approval adds to the grid: the request run on every weather year of `ctx`, the house's
    injection in each (all it asked for when firm, held to its envelope when not) and each year's
    grid state with it. `verified` says whether every year passed; the caller decides what a
    failure means (`decide` refuses it, `rebuild` flags it). The injections are rounded as they are
    stored, so the states are those a reader of the stored files would rebuild."""
    emit = emit or (lambda *_: None)
    cid = int(r.cid)
    years = ctx.years(settings().weather_years)
    emit("verify", {"years": len(years)})
    out, detail = rq.run(model, r, hp, ev, ctx=ctx, verify_years=years)
    left = out["verification"].get("all_years", {})
    verified = not any(left.get(k, 0) for k in ("export_violations_after", "import_violations_after",
                                                "battery_violations_after"))
    tanphi = np.tan(np.arccos(A("load_power_factor")))
    inj = {}
    for y in years:
        d = detail[y]
        if decision == "firm":       # no envelope: everything it asked for, as the static study approved
            demand = d["load"] + d["hp"] + d["ev_want"]
            p, q = d["pv"] - demand, -demand * tanphi
        else:
            p, q = rq._injection(d), rq._reactive(d)
        inj[y] = (np.asarray(p, dtype=np.float32), np.asarray(q, dtype=np.float32))
    states = {}
    for i, y in enumerate(years):
        p, qq = ctx.pq(model, y, hp, ev)
        p, qq = p.copy(), qq.copy()
        p[cid], qq[cid] = inj[y][0].astype(float), inj[y][1].astype(float)
        states[y] = rq.build_state(model, p, qq)
        emit("state", {"year": y, "done": i + 1, "of": len(years)})
    cust = customers()
    return {"out": out, "years": years, "inj": inj, "states": states, "verified": verified,
            "pv_kw": rq.pv_cap(r, float(cust.loc[cid, "fuse_kva"])), "ev_kw": float(r.ev_kw),
            "hp": bool(r.hp) and not bool(hp[cid])}


def _injection_arrays(inj: dict) -> dict[str, np.ndarray]:
    return {**{f"p_{y}": p for y, (p, _) in inj.items()}, **{f"q_{y}": q for y, (_, q) in inj.items()}}


def decide(model, hp: np.ndarray, ev: np.ndarray, record: dict, decision: str, note: str = "",
           emit=None, by: str = "dso", decided_by: str = "", signature: str | None = None,
           where: Path | None = None) -> dict:
    """Record a decision on a saved assessment; an approval re-verifies it and updates the grid.
    `by` says who decided: the DSO's planner, or its rule (api/inbox.py), which never declines and
    decides only under a signed rule, whose digest (`signature`) the decision keeps. A planner's
    decision keeps who made it (`decided_by`, free text) when given."""
    if by == "rule" and decision == "declined":
        raise ValueError("the automatic rule never declines")
    if by == "rule" and not signature:
        raise ValueError("an automatic decision needs the signed rule it was made under")
    root = _at(where)
    with _LOCK:
        q = load(root)
        base = _check(q, record, decision)
        req, res = record["request"], record["result"]
        cid = int(req["cid"])
        item = {"assessment": record["id"], "cid": cid,
                "decision": decision, "by": by, "note": note.strip()[:500],
                "decided": datetime.now(timezone.utc).isoformat(), "request": req,
                "summary": res.get("summary", {})}
        if decided_by.strip():
            item["decided_by"] = decided_by.strip()[:120]
        if signature:
            item["rule_signature"] = signature
        if decision == "declined":
            q["baseline"] = base
            q["items"].append(item)
            _commit(q, record["id"], root)
            return item

        static = res.get("static", {})
        if decision == "firm" and not all(v.get("approved") for v in static.values()):
            raise QueueError("static_rejects_firm")
        ctx = context(root) or rq.Context(base)
        r = engine_request(req, base)
        got = _reserve(model, hp, ev, ctx, r, decision, emit=emit)
        if not got["verified"]:
            raise QueueError("still_violates")

        # the next version's files first; none of them is read until queue.json names that version
        version = q["version"] + 1
        savez_atomic(root / f"{record['id']}.npz", **_injection_arrays(got["inj"]))
        for y, st in got["states"].items():
            savez_atomic(_state_path(y, version, root), **st)
        item.update(pv_kw=got["pv_kw"], ev_kw=got["ev_kw"], hp=got["hp"], verification=got["out"]["verification"],
                    summary=got["out"]["summary"])
        q["items"].append(item)
        q["baseline"] = base
        q["version"] = version
        _commit(q, record["id"], root)
        _forget(root)
        _collect(q, root)
        return item


def rebuild(model, hp: np.ndarray, ev: np.ndarray, baseline: str, approved_items: list[dict],
            pending: list[dict] | None = None, where: Path | None = None, emit=None) -> dict:
    """Replace the queue in `where` with the DSO's own list of approved connections, in the order
    given: each {"assessment", "cid", "decision" (firm|nonfirm), "decided", "request", and anything
    else to keep}. Each is reserved as `decide` would, against the grid with the ones before it;
    one that fails the re-verification, or that the static study rejects though approved firm, is
    kept with its `flags`. `pending` is kept as it is, in order, for whoever reads the queue.

    The new queue is written whole into `<where>.next` and swapped in; the same list gives the same
    bytes (queue.json, every injection and every state). Returns the queue as written."""
    root = _at(where)
    nxt, old = root.with_name(root.name + ".next"), root.with_name(root.name + ".old")
    with _LOCK:
        if nxt.exists():
            shutil.rmtree(nxt)
        nxt.mkdir(parents=True)
        years = settings().weather_years
        overrides: dict[int, dict] = {}
        states: dict[int, dict] | None = None
        items = []
        for it in approved_items:
            if it["decision"] not in ("firm", "nonfirm"):
                raise QueueError("unknown_decision", decision=repr(it["decision"]))
            ctx = rq.Context(baseline, overrides=dict(overrides), states=states)
            r = engine_request(it["request"], baseline)
            got = _reserve(model, hp, ev, ctx, r, it["decision"], emit=emit)
            flags = [] if got["verified"] else ["still_violates"]
            static = got["out"].get("static", {})
            if it["decision"] == "firm" and not all(v.get("approved") for v in static.values()):
                flags.append("static_rejects_firm")
            savez_atomic(nxt / f"{it['assessment']}.npz", stable=True, **_injection_arrays(got["inj"]))
            item = {**it, "cid": int(r.cid), "by": it.get("by", "sync"), "pv_kw": got["pv_kw"], "ev_kw": got["ev_kw"],
                    "hp": got["hp"], "verification": got["out"]["verification"], "summary": got["out"]["summary"],
                    "verified": got["verified"], "flags": flags}
            items.append(item)
            overrides[int(r.cid)] = _override(item, _injection_arrays(got["inj"]), years)
            states = got["states"]
        version = len(items)
        for y, st in (states or {}).items():
            savez_atomic(_state_path(y, version, nxt), stable=True, **st)
        q = {"version": version, "baseline": baseline, "items": items, "pending": list(pending or [])}
        write_json_atomic(nxt / "queue.json", json.loads(json.dumps(q, default=_plain)))
        # the swap: the old queue aside, the new one in, the old one removed
        if old.exists():
            shutil.rmtree(old)
        if root.exists():
            os.replace(root, old)
        os.replace(nxt, root)
        shutil.rmtree(old, ignore_errors=True)
        _forget(root)
        return load(root)


def _plain(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


def _commit(q: dict, assessment: str, where: Path | None = None) -> None:
    """Commit a decision: say which seal is owed, write queue.json, seal the decision, clear the marker.
    A crash at any point leaves either the old queue or the new one with its seal owed (recover())."""
    _seal_owed(q, where)              # an earlier decision whose seal failed in-process: seal it first, loudly
    write_json_atomic(_at(where) / PENDING, {"kind": "decision", "id": assessment})
    _save(q, where)
    _seal_owed(q, where)


def _seal_owed(q: dict, where: Path | None = None) -> str | None:
    """Seal the decision the marker names when queue.json holds it (once: a seal already in the chain
    with the same digest is not repeated), then remove the marker. Returns the id it sealed."""
    marker = _at(where) / PENDING
    if not marker.exists():
        return None
    try:
        owed = json.loads(marker.read_text(encoding="utf-8"))
    except ValueError:
        owed = None
    done = None
    item = next((it for it in q.get("items", []) if isinstance(owed, dict) and it.get("assessment") == owed.get("id")),
                None)
    if item is not None:              # committed: the seal is owed; otherwise the decision never happened
        entry = seals.entry_for("decision", item["assessment"])
        if entry is None or entry["digest"] != seals.digest(item):
            seals.seal("decision", item["assessment"], item)
        done = item["assessment"]
    marker.unlink(missing_ok=True)
    return done


def _collect(q: dict, where: Path | None = None) -> None:
    """Remove what no committed version reads: other versions' states (a crashed approval's among
    them), states from before the versions once the current version has its own, and injections of
    houses the queue does not hold."""
    root = _at(where)
    if not root.exists():
        return
    version = q.get("version", 0)
    held = {f"{it['assessment']}.npz" for it in approved(q)} if q.get("items") else set()
    for path in root.iterdir():
        m = _STATE.match(path.name)
        if m:
            stale = (m[2] is not None and int(m[2]) != version) or (
                m[2] is None and _state_path(int(m[1]), version, root).exists())
        else:
            stale = bool(_INJECTION.match(path.name)) and path.name not in held
        if stale:
            try:
                path.unlink()
            except OSError:           # held open for a moment: the next approval or start removes it
                pass


def recover(where: Path | None = None) -> dict:
    """At the server's start: finish a rebuild's swap a crash interrupted, seal the decision a crash
    left unsealed, and remove the files of a version that never committed."""
    root = _at(where)
    with _LOCK:
        old, nxt = root.with_name(root.name + ".old"), root.with_name(root.name + ".next")
        if not root.exists() and old.exists():      # stopped between the swap's two renames
            os.replace(old, root)
        if old.exists() and root.exists():
            shutil.rmtree(old, ignore_errors=True)
        if nxt.exists():                            # a rebuild that never swapped
            shutil.rmtree(nxt, ignore_errors=True)
        if not root.exists():
            return {"sealed": None}
        q = load(root)
        sealed = _seal_owed(q, root)
        if sealed:
            log.info("sealed decision %s, owed since the server last stopped", sealed)
        _collect(q, root)
        return {"sealed": sealed}


def reset(where: Path | None = None) -> None:
    """Empty the queue. The assessments themselves stay saved in data/requests/, and what the queue
    held is sealed first, so its decisions do not leave the record with it."""
    root = _at(where)
    with _LOCK:
        q = load(root)
        if q["items"]:
            stamp = datetime.now(timezone.utc)
            seals.seal("queue_reset", stamp.strftime("%Y%m%dT%H%M%S%fZ"),
                       {"reset_at": stamp.isoformat(), "version": q["version"], "baseline": q["baseline"],
                        "items": q["items"]})
        if root.exists():
            shutil.rmtree(root)
        _forget(root)
