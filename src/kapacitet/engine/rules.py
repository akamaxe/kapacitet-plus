"""The rule an application is decided by when nobody has to look at it.

    firm     every part of the request passes the static study, and the house's own main fuse and
             phases take what it asks for;
    nonfirm  the static study rejects a part, but the verified envelope keeps the house's curtailment
             within the DSO's thresholds on average and in one year in ten, leaves no more of its EV
             charging unserved than the DSO accepts, leaves no heat unserved, and the year it was
             checked on has no hour above a grid limit left;
    review   anything else.

The rule never declines: a refusal is a person's decision. It reads only the saved assessment and
the thresholds, so the same answer always gets the same verdict, and a test can hold it to that.
An approval it gives is still re-verified on every weather year by `queue.decide`.

It decides on its own only what a person has signed for (`scope`, dso.RuleSignature): a request of
the kinds signed, within the PV size signed, under the thresholds signed and before the signature
runs out. Anything else goes to a person as `unsigned_rule` or `out_of_scope`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

OUTCOMES = ("firm", "nonfirm", "review")

# Why an application went to a person. The interface says it with `inbox.reason.<code>` in each
# language (tests/test_messages.py holds both dictionaries to these sentences).
REASONS = {
    "fuse": "the house's main fuse is too small for what it asks for",
    "phases": "the charger would unbalance its phase beyond the limit",
    "curtailment_mean": "curtailment of {value:.1f} % on average, above the threshold of {limit:.1f} %",
    "curtailment_p90": "curtailment of {value:.1f} % in one year in ten, above the threshold of {limit:.1f} %",
    "ev_unserved": "{value:.1f} % of the charging it asks for would be unserved, above {limit:.1f} %",
    "hp_unserved": "{value:.1f} % of the heat pump's energy would be unserved",
    "violations": "the checked year still has {value:.0f} hours above a grid limit",
    "auto_off": "advisor mode: the rule only proposes",
    # `detail` is the queue's own refusal code (engine/queue.py ERRORS), which the interface translates
    "queue": "the queue refused the decision: {detail}",
    "engine_error": "the evaluation failed: {detail}",
    "assessment_missing": "the assessment behind it is no longer kept",
    "unsigned_rule": "no person has signed the rule in force, so a person decides",
    "out_of_scope": "the request is outside what the signed rule covers, so a person decides",
}


@dataclass(frozen=True)
class Thresholds:
    curtailment_mean_pct: float
    curtailment_p90_pct: float
    ev_unserved_pct: float


def request_kinds(request: dict) -> set[str]:
    """What a request asks for, in the signature's words."""
    kinds = set()
    if float(request.get("pv_kwp", 0) or 0) > 0:
        kinds.add("pv")
    if float(request.get("ev_kw", 0) or 0) > 0:
        kinds.add("ev")
    if request.get("hp"):
        kinds.add("hp")
    if float(request.get("bat_kwh", 0) or 0) > 0:
        kinds.add("battery")
    return kinds


def scope(signature: dict | None, request: dict, th: Thresholds, today: date) -> str | None:
    """None when the signed rule covers this request under these thresholds today; otherwise why a
    person decides: `unsigned_rule` (no signature, another rule's thresholds, or run out) or
    `out_of_scope` (a kind or a size the signature does not cover)."""
    if not signature:
        return "unsigned_rule"
    signed = signature.get("thresholds") or {}
    for key in ("curtailment_mean_pct", "curtailment_p90_pct", "ev_unserved_pct"):
        if abs(float(signed.get(key, -1.0)) - float(getattr(th, key))) > 1e-9:
            return "unsigned_rule"
    until = signature.get("valid_until")
    if until is None or date.fromisoformat(str(until)) < today:
        return "unsigned_rule"
    if not request_kinds(request) <= set(signature.get("request_kinds") or ()):
        return "out_of_scope"
    if float(request.get("pv_kwp", 0) or 0) > float(signature.get("max_pv_kwp", 0)) + 1e-9:
        return "out_of_scope"
    return None


def _reason(code: str, **params) -> dict:
    return {"code": code, "params": params, "text": REASONS[code].format(**params)}


def _violations_left(verification: dict) -> int:
    """Hours above a limit still left after the envelope, on the year the answer was checked on."""
    v = verification or {}
    return int(v.get("violations_after", 0) or 0) + sum(
        int((v.get(k) or {}).get("violations_after", 0) or 0) for k in ("import", "battery"))


def verdict(result: dict, th: Thresholds) -> dict:
    """`{"outcome": firm | nonfirm | review, "reasons": [...]}` for one saved answer."""
    reasons: list[dict] = []
    if "fuse" in result and not result["fuse"].get("ok", True):
        reasons.append(_reason("fuse"))
    if "phases" in result and not result["phases"].get("ok", True):
        reasons.append(_reason("phases"))
    if reasons:
        return {"outcome": "review", "reasons": reasons}

    static = result.get("static") or {}
    if static and all(part.get("approved") for part in static.values()):
        return {"outcome": "firm", "reasons": []}

    summary = result.get("summary") or {}
    risk = result.get("risk") or {}
    if "pv" in static:
        mean, p90 = risk.get("mean_pct"), risk.get("p90_pct")
        if mean is None or mean > th.curtailment_mean_pct + 1e-9:
            reasons.append(_reason("curtailment_mean", value=float(mean or 0.0), limit=th.curtailment_mean_pct))
        if p90 is None or p90 > th.curtailment_p90_pct + 1e-9:
            reasons.append(_reason("curtailment_p90", value=float(p90 or 0.0), limit=th.curtailment_p90_pct))
    if "ev" in static:
        ev = float(summary.get("ev_unmet_pct") or 0.0)
        if ev > th.ev_unserved_pct + 1e-9:
            reasons.append(_reason("ev_unserved", value=ev, limit=th.ev_unserved_pct))
    if "hp" in static:
        hp = float(summary.get("hp_unmet_pct") or 0.0)
        if hp > 1e-9:
            reasons.append(_reason("hp_unserved", value=hp))
    left = _violations_left(result.get("verification") or {})
    if left:
        reasons.append(_reason("violations", value=float(left)))
    return {"outcome": "review" if reasons else "nonfirm", "reasons": reasons}
