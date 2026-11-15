"""The DSO's static method as a profile, over HTTP (method_profile.py, task 2.5 of the technical review).

    GET /method-profile     the profile of the network in use, the default one, the values the
                            computation reads now, and the fields' limits (from the model)
    PUT /method-profile     a new profile, written to data/requests/method_profile.json

A profile changes the static figures at the next computation of the capacity (and in this server
once it starts again), so the published figures and the live checks always come from one profile;
the answer says what to run. It writes only in data/requests/, the API's first place to write.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import method_profile as mp

router = APIRouter(prefix="/api/v1", tags=["method"])
KEYS = ("static_pv_factor", "static_load_rule", "static_min_load_factor", "static_fixed_load_kw",
        "static_v_rise_limit_pct", "static_max_loading_pct")


@router.get("/method-profile")
def get_profile() -> dict:
    from ..config import A

    try:
        profile = mp.load()
    except (OSError, ValueError):
        profile = None
    return {"profile": None if profile is None else profile.model_dump(mode="json"),
            "default": mp.MethodProfile().model_dump(mode="json"),
            "in_use": {k: A(k) for k in KEYS},
            "schema": mp.MethodProfile.model_json_schema()}


@router.put("/method-profile")
def put_profile(body: dict) -> dict:
    """The profile, checked by its model (a fixed load needs its figure, a change its source)."""
    try:
        profile = mp.MethodProfile.model_validate(body)
    except ValueError as err:
        detail = str(err).splitlines()[-1][:300] if str(err) else "invalid"
        raise HTTPException(422, {"code": "method_profile", "message": f"the method profile cannot be used: {detail}",
                                  "params": {"detail": detail}}) from err
    mp.save(profile)
    return {"profile": profile.model_dump(mode="json"), "changes": profile.overrides(),
            "command": "python -m kapacitet compute --part capacity"}
