"""The DSO's own settings: the choices an operator makes, kept per data folder and sealed on change.

They live in data/requests/settings.json, the one folder the API writes (or in the database, with
KAPACITET_DATABASE_URL: kapacitet.store). A change is validated, written atomically and sealed
with what it was before and after, so the evidence trail says which rule was in force when.

Advisor mode is the default: `auto_decisions` is off, and even on, the rule decides only what a
person at the DSO has signed for (`RuleSignature`: who, in what role, which kinds of request, up to
what PV size, with which thresholds and until when). The signature is sealed as `rule_signature`
when it is set (`sign_rule`), and every automatic decision keeps its digest (engine/queue.decide).
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import actor, seals, store
from .paths import DATA

POLICIES = ("maxmin", "prorata", "propfair", "maxtotal")
REQUEST_KINDS = ("pv", "ev", "hp", "battery")
_LOCK = threading.Lock()


class SignedThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    curtailment_mean_pct: float = Field(..., ge=0, le=100)
    curtailment_p90_pct: float = Field(..., ge=0, le=100)
    ev_unserved_pct: float = Field(..., ge=0, le=100)


class RuleSignature(BaseModel):
    """A person's signature on the automatic rule: what it may decide without them."""

    model_config = ConfigDict(extra="forbid")

    signed_by: str = Field(..., min_length=1, max_length=120)
    role: str = Field(..., min_length=1, max_length=120)
    signed_at: str = Field(..., min_length=10, max_length=40)
    request_kinds: list[Literal["pv", "ev", "hp", "battery"]] = Field(..., min_length=1)
    max_pv_kwp: float = Field(..., ge=0, le=1000)
    thresholds: SignedThresholds
    valid_until: date
    # with sign-in on, the signed-in person ({sub, name, role}, kapacitet/actor.py): `signed_by` is
    # their name, and the digest every automatic decision keeps covers it; `role` stays the job title
    actor: dict[str, str] | None = None


class RuleSignatureIn(BaseModel):
    """PUT /settings/rule-signature: the signature, dated by the server; the thresholds are those in
    force unless given."""

    model_config = ConfigDict(extra="forbid")

    signed_by: str = Field(..., min_length=1, max_length=120)
    role: str = Field(..., min_length=1, max_length=120)
    request_kinds: list[Literal["pv", "ev", "hp", "battery"]] = Field(..., min_length=1)
    max_pv_kwp: float = Field(..., ge=0, le=1000)
    thresholds: SignedThresholds | None = None
    valid_until: date


class DsoSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # how curtailment is shared between the houses on conditional agreements (engine/doe.py)
    allocation_policy: Literal["maxmin", "prorata", "propfair", "maxtotal"] = "maxmin"
    # the most curtailment an agreement promises, as a share of the house's solar energy in a year;
    # by default the budget the capacity search itself is held to (z_star_pct)
    agreed_level_pct: float = Field(default_factory=lambda: float(_assumption("z_star_pct")), ge=0, le=100)
    # applications are decided by engine/rules.py when this is on and a signed rule covers them
    # (advisor mode, the default, leaves every decision to a person); the rule never declines
    auto_decisions: bool = False
    # the rule's thresholds: a house's curtailment on average and in one year in ten (none: the agreed
    # level), and the EV charging an envelope may leave unserved
    auto_curtailment_mean_pct: float | None = Field(None, ge=0, le=100)
    auto_curtailment_p90_pct: float | None = Field(None, ge=0, le=100)
    auto_ev_unserved_pct: float = Field(default_factory=lambda: float(_assumption("ev_unserved_limit_pct")), ge=0, le=100)
    # the published grid an application is evaluated against while the queue is empty
    inbox_baseline: Literal["static", "doe"] = "static"
    # how many years the envelopes defer the reinforcement, for the business case (by default one
    # regulatory period)
    deferral_years: int = Field(default_factory=lambda: int(_assumption("regulatory_period_years")), ge=1, le=40)
    # what only the DSO can state for Ei (engine/eipack.py): empty means not stated, never met
    market_alternative_statement: str = Field("", max_length=2000)
    method_approval_reference: str = Field("", max_length=200)
    review_statement: str = Field("", max_length=2000)
    # when the day-ahead market's result is known, on Stockholm's wall clock on the day before
    # delivery: a schedule is issued only after it (kapacitet/market.py)
    market_result_local: str = Field(default_factory=lambda: str(_assumption("dayahead_market_result_local")),
                                     pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    # what the DSO paid in the report year for the curtailment its conditional agreements imposed,
    # for the redispatch report (engine/redispatch.py); zero unless the DSO enters it
    redispatch_compensation_sek: float = Field(0.0, ge=0, le=1e12)
    # what the rule may decide on its own (`sign_rule`); none: nothing
    rule_signature: RuleSignature | None = None


def _assumption(key: str) -> float:
    from .config import A

    return A(key)


class DsoSettingsChange(BaseModel):
    """A partial change: only the fields given are changed."""

    model_config = ConfigDict(extra="forbid")

    allocation_policy: Literal["maxmin", "prorata", "propfair", "maxtotal"] | None = None
    agreed_level_pct: float | None = Field(None, ge=0, le=100)
    auto_decisions: bool | None = None
    auto_curtailment_mean_pct: float | None = Field(None, ge=0, le=100)
    auto_curtailment_p90_pct: float | None = Field(None, ge=0, le=100)
    auto_ev_unserved_pct: float | None = Field(None, ge=0, le=100)
    inbox_baseline: Literal["static", "doe"] | None = None
    deferral_years: int | None = Field(None, ge=1, le=40)
    market_alternative_statement: str | None = Field(None, max_length=2000)
    method_approval_reference: str | None = Field(None, max_length=200)
    review_statement: str | None = Field(None, max_length=2000)
    market_result_local: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    redispatch_compensation_sek: float | None = Field(None, ge=0, le=1e12)


def path():
    return DATA / "requests" / "settings.json"


def _doc():
    """settings.json, or its row in the database (kapacitet.store)."""
    return store.settings(path())


def load() -> DsoSettings:
    """The settings in force; the defaults when none were ever saved or the file is unreadable."""
    try:
        got = _doc().get()
        return DsoSettings() if got is None else DsoSettings.model_validate(got)
    except (ValueError, ValidationError):
        return DsoSettings()


def _write(before: DsoSettings, after: DsoSettings) -> DsoSettings:
    _doc().put(after.model_dump(mode="json"))
    stamp = datetime.now(timezone.utc)
    seals.seal("settings", stamp.strftime("%Y%m%dT%H%M%S%fZ"),
               {"changed_at": stamp.isoformat(), "before": before.model_dump(mode="json"),
                "after": after.model_dump(mode="json")})
    return after


def update(change: DsoSettingsChange) -> DsoSettings:
    """Apply a change, write it atomically and seal before and after."""
    with _LOCK, _doc().locked():
        before = load()
        after = DsoSettings.model_validate({**before.model_dump(), **change.model_dump(exclude_none=True)})
        if after == before:
            return before
        return _write(before, after)


def signature_of(body: RuleSignatureIn, now: datetime | None = None, s: DsoSettings | None = None) -> RuleSignature:
    """The signature a request to sign stands for: dated now, the thresholds in force unless given."""
    th = thresholds(s)
    given = body.thresholds or SignedThresholds(curtailment_mean_pct=th.curtailment_mean_pct,
                                                curtailment_p90_pct=th.curtailment_p90_pct,
                                                ev_unserved_pct=th.ev_unserved_pct)
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    who = actor.current()     # signed in: the session says who signs, never the request's free text
    by = {k: str(who.get(k, ""))[:120] for k in ("sub", "name", "role")} if who else None
    return RuleSignature(signed_by=(by["name"] if by and by["name"] else body.signed_by.strip()),
                         role=body.role.strip(), signed_at=stamp,
                         request_kinds=sorted(set(body.request_kinds), key=REQUEST_KINDS.index),
                         max_pv_kwp=body.max_pv_kwp, thresholds=given, valid_until=body.valid_until, actor=by)


def signature_doc(sig: RuleSignature | dict) -> dict:
    """The signature as sealed and digested: without `actor` when there is none, so a signature made
    with sign-in off keeps the digest it had before the field existed."""
    data = sig.model_dump(mode="json") if isinstance(sig, RuleSignature) else dict(sig)
    if data.get("actor") is None:
        data.pop("actor", None)
    return data


def signature_digest(sig: RuleSignature | dict) -> str:
    """The digest an automatic decision keeps of the signature it was made under."""
    return seals.digest(signature_doc(sig))


def sign_rule(body: RuleSignatureIn) -> DsoSettings:
    """Set the rule's signature: sealed as `rule_signature`, and as a change of the settings."""
    with _LOCK, _doc().locked():
        before = load()
        sig = signature_of(body, s=before)
        after = DsoSettings.model_validate({**before.model_dump(), "rule_signature": sig.model_dump()})
        seals.seal("rule_signature", signature_digest(sig)[:16], signature_doc(sig))
        return _write(before, after)


def thresholds(s: DsoSettings | None = None):
    """The automatic rule's thresholds as the settings resolve them."""
    from .engine.rules import Thresholds

    s = s or load()
    return Thresholds(
        curtailment_mean_pct=s.auto_curtailment_mean_pct if s.auto_curtailment_mean_pct is not None else s.agreed_level_pct,
        curtailment_p90_pct=s.auto_curtailment_p90_pct if s.auto_curtailment_p90_pct is not None else s.agreed_level_pct,
        ev_unserved_pct=s.auto_ev_unserved_pct,
    )
