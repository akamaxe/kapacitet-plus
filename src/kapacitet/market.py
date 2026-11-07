"""The market comes first: when a conditional limit may be activated, and what the DSO consulted.

A conditional envelope limits a house in place of buying the same relief on a market, so two
things have to hold before a DSO uses it (Ei2025:01 §2.3; Regulation (EU) 2019/943, art. 13):

- **the day-ahead market has cleared.** A schedule for day D is issued only after the market's
  result for D is known, at `market_result_local` (Europe/Stockholm wall clock, a DSO setting) on
  D − 1. `gate` says when that is in UTC, on any day, the two DST change days included; issuing
  earlier needs a reason, which is sealed with the issue (api/operation.issue_schedule);
- **the market alternatives were studied.** Before an agreement is signed and before a period of
  activations, the DSO records which alternatives it consulted (a local flexibility market, a
  bilateral flexibility contract, a network tariff, none available, other) and with what outcome.
  Each record is one line of `data/requests/market_alternatives.jsonl` (beside the seal chain,
  `seals.folder()`; a table of the database with KAPACITET_DATABASE_URL, kapacitet.store),
  appended with `atomic.append_line` and sealed as `market_alternatives`. A
  line cut short by a crash is a torn tail: the reader leaves it out and the next append starts a
  line of its own. Issuing a schedule for a day no record covers is flagged, never refused.

Standard library and pydantic only: the API, the Ei pack and the redispatch report all read it.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from . import seals, store

STOCKHOLM = ZoneInfo("Europe/Stockholm")
SCOPES = ("agreement", "activation_period", "zone")
KINDS = ("local_flexibility_market", "bilateral_flexibility", "network_tariff", "none_available", "other")
CLOCK = r"^([01]\d|2[0-3]):[0-5]\d$"
SEAL_KIND = "market_alternatives"
# the scopes that cover a period of activations; an agreement's record is about signing it
PERIOD_SCOPES = ("activation_period", "zone")
_LOCK = threading.Lock()

# what the interface says when a record or an issue is refused (err.<code> in i18n)
ERRORS = {
    "before_market": "the schedule for {date} can be issued once the day-ahead market result is known, "
                     "after {local} Stockholm time on {day_before}; give a reason to issue it earlier",
    "market_ref": "a record for {scope} needs {needs}",
    "market_range": "the period ends ({end}) before it starts ({start})",
}


class MarketError(ValueError):
    def __init__(self, code: str, **params):
        self.code, self.params = code, params
        super().__init__(ERRORS[code].format(**params))


# ---------------------------------------------------------------- the gate

def now() -> datetime:
    """The clock the gate reads (a function, so a test can set it)."""
    return datetime.now(timezone.utc)


def result_at(day: date, local: str) -> datetime:
    """When the day-ahead result for delivery day `day` is known: `local` on the day before, on
    Stockholm's wall clock, in UTC. 13:00 is 12:00 UTC in winter and 11:00 UTC in summer."""
    if not re.fullmatch(CLOCK, local):
        raise ValueError(f"not a wall-clock time HH:MM: {local!r}")
    hh, mm = (int(x) for x in local.split(":"))
    wall = datetime.combine(day - timedelta(days=1), time(hh, mm), tzinfo=STOCKHOLM)
    return wall.astimezone(timezone.utc)


def gate(day: date, local: str, at: datetime | None = None) -> dict:
    """Whether a schedule for `day` may be issued at `at` (now by default): open from the market's
    result on the day before."""
    opens = result_at(day, local)
    at = at or now()
    return {"open": at >= opens, "result_at": opens.isoformat(), "local": local,
            "day_before": (day - timedelta(days=1)).isoformat()}


# ---------------------------------------------------------------- the log

class Alternative(BaseModel):
    """One alternative consulted: what kind, whose, when, and what came of it."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["local_flexibility_market", "bilateral_flexibility", "network_tariff", "none_available", "other"]
    name: str = Field("", max_length=200)
    consulted_at: date
    outcome: str = Field("", max_length=500)
    note: str = Field("", max_length=2000)


class Ref(BaseModel):
    """What a record is about: a house's agreement (`cid`) or a period (`start` to `end`, inclusive)."""

    model_config = ConfigDict(extra="forbid")

    cid: int | None = Field(None, ge=0)
    start: date | None = None
    end: date | None = None


class RecordIn(BaseModel):
    """POST /operation/market-alternatives: a record of the alternatives studied, before signing an
    agreement or before a period of activations, for one house, one period or the whole zone."""

    model_config = ConfigDict(extra="forbid")

    scope: Literal["agreement", "activation_period", "zone"]
    ref: Ref
    alternatives: list[Alternative] = Field(..., min_length=1, max_length=20)
    decided_by: str = Field("", max_length=120, description="who recorded it, as free text (a name, a role)")



def check(body: RecordIn) -> None:
    """Refuse a record whose `ref` does not fit its scope: an agreement names a house, a period or
    the zone names its first and last day."""
    r = body.ref
    if body.scope == "agreement":
        if r.cid is None or r.start is not None or r.end is not None:
            raise MarketError("market_ref", scope=body.scope, needs="cid")
        return
    if r.start is None or r.end is None or r.cid is not None:
        raise MarketError("market_ref", scope=body.scope, needs="start, end")
    if r.end < r.start:
        raise MarketError("market_range", start=r.start.isoformat(), end=r.end.isoformat())


def log_path() -> Path:
    return seals.folder() / "market_alternatives.jsonl"


def _log():
    """market_alternatives.jsonl, or its table in the database (kapacitet.store)."""
    return store.market(log_path())


def entries() -> list[dict]:
    """Every record, oldest first; a line that does not parse (a torn tail) is left out."""
    return [e for e in _log().entries() if isinstance(e, dict) and e.get("id")]


def _entry(body: RecordIn, item_id: str, at: datetime) -> dict:
    ref = ({"cid": body.ref.cid} if body.scope == "agreement"
           else {"start": body.ref.start.isoformat(), "end": body.ref.end.isoformat()})
    return {"id": item_id, "recorded_at": at.isoformat(timespec="seconds"), "scope": body.scope, "ref": ref,
            "alternatives": [{**a.model_dump(mode="json"), "name": a.name.strip(), "outcome": a.outcome.strip(),
                              "note": a.note.strip()} for a in body.alternatives],
            "decided_by": body.decided_by.strip()}


def record(body: RecordIn, idempotency_key: str | None = None) -> tuple[dict, dict]:
    """Seal a record and append it to the log; returns the record and its chain entry. Sent again
    under the same Idempotency-Key, it returns the record sealed the first time and adds nothing
    (and mends a log line a crash kept from being written after its seal)."""
    check(body)
    with _LOCK, _log().locked():
        item = f"k{idempotency_key}" if idempotency_key else f"ma-{now():%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"
        seal = seals.entry_for(SEAL_KIND, item) if idempotency_key else None
        kept = seals.sealed_copy(SEAL_KIND, item) if seal else None
        if kept is None:
            kept = _entry(body, item, now())
            seal = seals.seal(SEAL_KIND, item, kept)
        if all(e["id"] != item for e in entries()):
            _log().append(kept, json.dumps(kept, sort_keys=True, ensure_ascii=False))
    return kept, seal


def with_seals() -> list[dict]:
    """Every record with its seal (digest, entry in the chain), newest first."""
    sealed = {s["entry"]["id"]: s["entry"] for s in seals.sealed_of_kind(SEAL_KIND)}
    out = []
    for e in entries():
        s = sealed.get(e["id"])
        out.append({**e, "seal": {k: s[k] for k in ("digest", "seq", "at")} if s else None})
    return out[::-1]


def _period(e: dict) -> tuple[date, date] | None:
    ref = e.get("ref") or {}
    try:
        return date.fromisoformat(ref["start"]), date.fromisoformat(ref["end"])
    except (KeyError, TypeError, ValueError):
        return None


def covering(day: date, records: list[dict] | None = None, cid: int | None = None) -> list[str]:
    """The records whose period covers `day`: the alternatives studied before that day's activations.
    With `cid`, the house's own agreement record counts too (studied before it was signed)."""
    out = []
    for e in entries() if records is None else records:
        p = _period(e) if e.get("scope") in PERIOD_SCOPES else None
        if p and p[0] <= day <= p[1]:
            out.append(e["id"])
        elif cid is not None and e.get("scope") == "agreement" and (e.get("ref") or {}).get("cid") == cid:
            out.append(e["id"])
    return out


def in_year(year: int, records: list[dict] | None = None) -> list[dict]:
    """The records about a year: a period that overlaps it, and every agreement's (an agreement's
    record is about signing it, so it holds for each year the agreement is in force)."""
    first, last = date(year, 1, 1), date(year, 12, 31)
    out = []
    for e in entries() if records is None else records:
        p = _period(e)
        if p is not None:
            if p[0] <= last and p[1] >= first:
                out.append(e)
        elif e.get("scope") == "agreement":
            out.append(e)
    return out
