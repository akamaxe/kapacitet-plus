"""The DSO's settings and what the operation stage computed, over HTTP."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import dso
from .combos import COMMAND, operation_index

router = APIRouter(prefix="/api/v1", tags=["operation"])


@router.get("/settings")
def get_settings() -> dict:
    return {"settings": dso.load().model_dump(), "policies": list(dso.POLICIES)}


@router.put("/settings")
def put_settings(change: dso.DsoSettingsChange) -> dict:
    """Change the DSO's settings; every change is sealed with what it replaced."""
    return {"settings": dso.update(change).model_dump(), "policies": list(dso.POLICIES)}


@router.get("/operation/index")
def get_index() -> dict:
    """Which combinations of rule and basis were computed, and what each verified to."""
    idx = operation_index()
    if idx is None:
        raise HTTPException(404, {"code": "not_computed", "file": "operation/index.json", "command": COMMAND,
                                  "message": "the operation stage has not been run"})
    return {**idx, "active_policy": dso.load().allocation_policy}
