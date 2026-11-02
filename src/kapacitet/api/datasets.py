"""Imported networks over HTTP: list them, create one from an export that passed the check (its
build starts at once, in its own process), read how the build is going, delete one, and switch
the server to one. A switch restarts the server under its supervisor, so it is refused when the
server runs without one, and while an evaluation or a decision is running. Nothing ever switches
by itself.

This router and its builds are the API's second place to write: data/datasets/.
"""

from __future__ import annotations

import os
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import datasets as ds

router = APIRouter(prefix="/api/v1", tags=["datasets"])
MAX_BYTES = 5_000_000


def refusal(err: ds.DatasetError, status: int = 409) -> HTTPException:
    return HTTPException(status, {"code": err.code, "message": str(err), "params": err.params})


class NewDataset(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    files: dict[str, str] = Field(..., description="the contract's four tables as CSV text, as for /check")
    quick: bool = Field(True, description="one weather year and fewer adoption orders: minutes, not an hour")


def _jobs_running() -> bool:
    from . import main

    return main._queue_busy() or any(j["status"] == "running" for j in main._JOBS.values())


@router.get("/datasets")
def list_datasets() -> dict:
    """The reference network and every imported one, which one is in use, and whether it can switch."""
    from ..config import settings

    return {"current": ds.current(), "active": ds.active(), "supervised": os.environ.get("KAPACITET_SUPERVISED") == "1",
            "reference": {"id": ds.REFERENCE, "name": settings().site.name}, "items": ds.listing()}


@router.post("/datasets")
def create_dataset(body: NewDataset) -> dict:
    """Keep an export as a new network and start building it (two worker processes)."""
    from ..importer.contract import BY_NAME
    from .main import api_error

    unknown = set(body.files) - set(BY_NAME)
    if unknown:
        raise api_error(422, "not_in_contract", tables=", ".join(sorted(unknown)))
    big = [n for n, text in body.files.items() if len(text.encode("utf-8")) > MAX_BYTES]
    if big:
        raise api_error(413, "too_large", mb=MAX_BYTES // 1_000_000, tables=", ".join(sorted(big)))
    try:
        rec = ds.create(body.name, body.files, quick=body.quick)
    except ds.DatasetError as err:
        raise refusal(err, 422) from err
    except Exception as err:    # a table pandas cannot read
        raise api_error(422, "unreadable_csv", table="?", detail=str(err)[:200]) from err
    ds.launch(rec["id"], workers=2)
    return ds.record(rec["id"])


@router.get("/datasets/{ds_id}")
def get_dataset(ds_id: str) -> dict:
    try:
        rec = ds.record(ds_id)
    except ds.DatasetError as err:
        raise refusal(err, 404) from err
    return {**rec, "building": ds.building(ds_id), "log": ds.log_tail(ds_id)}


@router.post("/datasets/{ds_id}/build")
def rebuild(ds_id: str) -> dict:
    """Build a network again, after a build that failed or to bring it up to date."""
    try:
        ds.launch(ds_id, workers=2)
    except ds.DatasetError as err:
        raise refusal(err, 404 if err.code == "unknown_dataset" else 409) from err
    return ds.record(ds_id)


@router.delete("/datasets/{ds_id}")
def delete_dataset(ds_id: str) -> dict:
    try:
        ds.delete(ds_id)
    except ds.DatasetError as err:
        raise refusal(err, 404 if err.code == "unknown_dataset" else 409) from err
    return {"ok": True}


@router.post("/datasets/{ds_id}/activate")
def activate(ds_id: str) -> dict:
    """Switch the server to a network: it restarts on that network's folder in a few seconds."""
    if os.environ.get("KAPACITET_SUPERVISED") != "1":
        raise refusal(ds.DatasetError("not_supervised"))
    if _jobs_running():
        raise refusal(ds.DatasetError("jobs_running"))
    try:
        ds.set_active(ds_id)
    except ds.DatasetError as err:
        raise refusal(err, 404 if err.code == "unknown_dataset" else 409) from err
    # answer first, then hand back to the supervisor, which starts the server on the new folder
    threading.Timer(0.5, lambda: os._exit(ds.RESTART)).start()
    return {"switching": True, "to": ds_id}
