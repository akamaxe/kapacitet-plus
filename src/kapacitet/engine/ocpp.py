"""A day's charging limits as an OCPP 1.6J SetChargingProfile request, and a check of its shape.

OCPP 1.6 (Open Charge Point Protocol, Open Charge Alliance; the JSON flavour is "1.6J") is how a
central system sets the most a charge point may draw. Kapacitet+ runs no central system: it
writes the request payload a DSO or its partner would send, so they can pass it on. The payload
is a `ChargePointMaxProfile` on connector 0 (the whole charge point), `Absolute` from local
midnight of the study day, in watts, with one `chargingSchedulePeriod` per step of the day's
schedule: the issued import envelope where there is one, the firm import limit where there is
none.

`validate` checks the payload against the rules of the OCPP 1.6 JSON schema
(SetChargingProfile.json) for the fields used here: types, required fields, enumerations, the
rate unit, a limit with at most one decimal, and periods that start at 0 and ascend. It is a
format modelled on OCPP 1.6J, written with the standard library: it is not certified, and no
charge point is connected to Kapacitet+.
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import floor

NOTICE = ("Kapacitet+: format modelled on OCPP 1.6J (SetChargingProfile); not certified; no charge point "
          "is connected. Simulated limits; not for operational use.")
ACTION = "SetChargingProfile"
PURPOSES = ("ChargePointMaxProfile", "TxDefaultProfile", "TxProfile")
KINDS = ("Absolute", "Recurring", "Relative")
UNITS = ("A", "W")
PROFILE_ID = 1           # one profile per charge point here; a later day replaces it at the same stack level
STACK_LEVEL = 0


def _utc(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _watts(kw: float) -> float:
    """kW to W with at most one decimal (OCPP 1.6: a multiple of 0.1), rounded down so the limit sent
    is never above the one issued."""
    return floor(max(0.0, float(kw)) * 10000.0 + 1e-6) / 10.0


def charging_profile(steps: list[dict], firm_kw: float, step_s: int) -> dict:
    """The SetChargingProfile.req payload for one day.

    `steps` are the day's schedule steps in order, each with `start_epoch` (UTC seconds) and
    `limit_kw` (None where the house has no envelope); `firm_kw` is the default for those.
    """
    if not steps:
        raise ValueError("a charging profile needs at least one step")
    start = int(steps[0]["start_epoch"])
    periods = [{"startPeriod": int(s["start_epoch"]) - start,
                "limit": _watts(firm_kw if s["limit_kw"] is None else min(float(s["limit_kw"]), firm_kw))}
               for s in steps]
    duration = int(steps[-1]["start_epoch"]) - start + int(step_s)
    return {
        "connectorId": 0,
        "csChargingProfiles": {
            "chargingProfileId": PROFILE_ID,
            "stackLevel": STACK_LEVEL,
            "chargingProfilePurpose": "ChargePointMaxProfile",
            "chargingProfileKind": "Absolute",
            "validFrom": _utc(start),
            "validTo": _utc(start + duration),
            "chargingSchedule": {
                "duration": duration,
                "startSchedule": _utc(start),
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": periods,
            },
        },
    }


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _datetime(v) -> bool:
    if not isinstance(v, str):
        return False
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def validate(payload) -> list[str]:
    """What is wrong with a SetChargingProfile.req payload, as OCPP 1.6J's JSON schema has it, for
    the fields this module writes; an empty list when nothing is. Rules beyond the schema that a
    charge point would enforce on this profile are checked too: connector 0 for a
    ChargePointMaxProfile, a start for an Absolute one, periods starting at 0 and ascending."""
    errs: list[str] = []
    if not isinstance(payload, dict):
        return ["the payload is not an object"]
    if not _int(payload.get("connectorId")) or payload["connectorId"] < 0:
        errs.append("connectorId: a non-negative integer is required")
    prof = payload.get("csChargingProfiles")
    if not isinstance(prof, dict):
        return errs + ["csChargingProfiles: an object is required"]
    for key in ("chargingProfileId", "stackLevel"):
        if not _int(prof.get(key)):
            errs.append(f"csChargingProfiles.{key}: an integer is required")
    if _int(prof.get("stackLevel")) and prof["stackLevel"] < 0:
        errs.append("csChargingProfiles.stackLevel: must not be negative")
    if prof.get("chargingProfilePurpose") not in PURPOSES:
        errs.append(f"csChargingProfiles.chargingProfilePurpose: one of {', '.join(PURPOSES)}")
    if prof.get("chargingProfileKind") not in KINDS:
        errs.append(f"csChargingProfiles.chargingProfileKind: one of {', '.join(KINDS)}")
    if prof.get("chargingProfilePurpose") == "ChargePointMaxProfile" and payload.get("connectorId") != 0:
        errs.append("connectorId: a ChargePointMaxProfile applies to connector 0")
    if "transactionId" in prof and prof.get("chargingProfilePurpose") != "TxProfile":
        errs.append("csChargingProfiles.transactionId: only a TxProfile names a transaction")
    for key in ("validFrom", "validTo"):
        if key in prof and not _datetime(prof[key]):
            errs.append(f"csChargingProfiles.{key}: a date-time with its offset is required")
    sch = prof.get("chargingSchedule")
    if not isinstance(sch, dict):
        return errs + ["csChargingProfiles.chargingSchedule: an object is required"]
    at = "csChargingProfiles.chargingSchedule"
    if "duration" in sch and (not _int(sch["duration"]) or sch["duration"] <= 0):
        errs.append(f"{at}.duration: a positive integer (seconds) is required")
    if "startSchedule" in sch and not _datetime(sch["startSchedule"]):
        errs.append(f"{at}.startSchedule: a date-time with its offset is required")
    if prof.get("chargingProfileKind") == "Absolute" and "startSchedule" not in sch:
        errs.append(f"{at}.startSchedule: an Absolute profile needs its start")
    if sch.get("chargingRateUnit") not in UNITS:
        errs.append(f"{at}.chargingRateUnit: one of {', '.join(UNITS)}")
    periods = sch.get("chargingSchedulePeriod")
    if not isinstance(periods, list) or not periods:
        return errs + [f"{at}.chargingSchedulePeriod: a non-empty array is required"]
    last = None
    for i, per in enumerate(periods):
        here = f"{at}.chargingSchedulePeriod[{i}]"
        if not isinstance(per, dict):
            errs.append(f"{here}: an object is required")
            continue
        start = per.get("startPeriod")
        if not _int(start) or start < 0:
            errs.append(f"{here}.startPeriod: a non-negative integer (seconds) is required")
        elif last is None and start != 0:
            errs.append(f"{here}.startPeriod: the first period starts at 0")
        elif last is not None and start <= last:
            errs.append(f"{here}.startPeriod: periods must ascend")
        if _int(start):
            last = start
        limit = per.get("limit")
        if not _num(limit) or limit < 0:
            errs.append(f"{here}.limit: a non-negative number is required")
        elif abs(round(limit * 10) - limit * 10) > 1e-6:
            errs.append(f"{here}.limit: at most one decimal")
        if "numberPhases" in per and (not _int(per["numberPhases"]) or not 1 <= per["numberPhases"] <= 3):
            errs.append(f"{here}.numberPhases: an integer from 1 to 3")
        if _int(start) and _int(sch.get("duration")) and start >= sch["duration"]:
            errs.append(f"{here}.startPeriod: starts after the schedule ends")
    return errs
