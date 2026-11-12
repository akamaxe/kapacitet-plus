"""Imported networks over HTTP: list them, create one from an export that passed the check (its
build starts at once, in its own process), read how the build is going, delete one, and switch
the server to one. A switch restarts the server under its supervisor, so it is refused when the
server runs without one, and while an evaluation or a decision is running. Nothing ever switches
by itself. A build can be stopped; one past KAPACITET_BUILD_TIMEOUT_S is stopped when the networks
are next read, and no more than KAPACITET_MAX_BUILDS run at once.

This router and its builds are the API's second place to write: data/datasets/. (The first is
data/requests/, the third the setup page's bundle upload, api/setup.py.)
"""

from __future__ import annotations

import os

from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from .. import datasets as ds
from ..cache import memo

router = APIRouter(prefix="/api/v1", tags=["datasets"])
MAX_BYTES = 5_000_000


def refusal(err: ds.DatasetError, status: int = 409) -> HTTPException:
    return HTTPException(status, {"code": err.code, "message": str(err), "params": err.params})


class Origin(BaseModel):
    """Where a new network comes from: an export, an example network or a project file."""

    kind: Literal["csv", "template", "project_file"]
    template: str | None = Field(None, max_length=40, description="with kind template: the example network's id")
    name: str | None = Field(None, max_length=120, description="the file's or the project's own name")
    synthetic: bool | None = Field(None, description="with kind project_file: what the file says of itself")


class NewDataset(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    files: dict[str, str] = Field(..., description="the contract's four tables as CSV text, as for /check")
    quick: bool = Field(True, description="one weather year and fewer adoption orders: minutes, not an hour")
    origin: Origin | None = Field(None, description="where the network comes from; an export when left out")


@memo(maxsize=16)
def _template_files(template_id: str) -> dict[str, str]:
    """A template's tables are built by pandapower (a second or two) and never change: kept."""
    from ..importer import templates

    return templates.files(template_id)


def _jobs_running() -> bool:
    from . import main

    return main._queue_busy() or main.POOL.busy()


def _reap() -> None:
    """Stop any build past its time before saying how the builds are going."""
    try:
        ds.reap()
    except Exception:  # noqa: BLE001 - the listing still answers
        import logging

        logging.getLogger("kapacitet.datasets").exception("could not stop a build past its time")


@router.get("/datasets")
def list_datasets() -> dict:
    """The reference network and every imported one, which one is in use, and whether it can switch."""
    from ..config import settings

    _reap()
    return {"current": ds.current(), "active": ds.active(), "supervised": os.environ.get("KAPACITET_SUPERVISED") == "1",
            "reference": {"id": ds.REFERENCE, "name": settings().site.name, "summary": ds.summary()},
            "items": ds.listing()}


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
        ds.can_launch()                 # a build slot first, so a refusal leaves nothing behind
    except ds.DatasetError as err:
        raise refusal(err) from err
    try:
        rec = ds.create(body.name, body.files, quick=body.quick,
                        origin=body.origin.model_dump(exclude_none=True) if body.origin else None)
    except ds.DatasetError as err:
        raise refusal(err, 422) from err
    except Exception as err:    # a table pandas cannot read
        raise api_error(422, "unreadable_csv", table="?", detail=str(err)[:200]) from err
    try:
        ds.launch(rec["id"], workers=2)
    except ds.DatasetError as err:  # another build took the slot meanwhile: kept, to be built later
        raise refusal(err) from err
    return ds.record(rec["id"])


@router.get("/datasets/{ds_id}")
def get_dataset(ds_id: str) -> dict:
    _reap()
    try:
        rec = ds.record(ds_id)
    except ds.DatasetError as err:
        raise refusal(err, 404) from err
    return {**rec, "building": ds.building(ds_id), "log": ds.log_tail(ds_id)}


@router.get("/datasets/{ds_id}/project.zip")
def project_file(ds_id: str) -> Response:
    """A network as a project file: its tables as the contract's CSV files, what it is, and the
    contract. For the reference network, the same tables as /contract/example."""
    from ..config import settings

    if ds_id == ds.REFERENCE:
        from . import main

        name, files = settings().site.name, main._example()
        origin = {"kind": "reference", "synthetic_parts": ["the cable network and the transformer: generated "
                                                           "along the streets OpenStreetMap maps"]}
        customers = ds.summary().get("customers")
    else:
        try:
            rec = ds.record(ds_id)
            files = ds.import_files(ds_id)
        except ds.DatasetError as err:
            raise refusal(err, 404) from err
        name, origin, customers = rec["name"], rec.get("origin") or {"kind": "csv"}, rec.get("customers")
    data = ds.project_archive(name, files, origin, customers)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="kapacitet-project-{ds_id}.zip"'})


@router.get("/templates")
def list_templates() -> dict:
    """The example networks a project can start from: pandapower's LV benchmark networks."""
    from ..importer import templates

    return {"templates": templates.listing(), "synthetic_parts": list(templates.SYNTHETIC_PARTS)}


@router.get("/templates/{template_id}")
def get_template(template_id: str) -> dict:
    """One example network as the contract's four tables, as CSV text (as /contract/example)."""
    from ..importer import templates

    try:
        template = templates.get(template_id)
    except KeyError:
        raise refusal(ds.DatasetError("unknown_template", id=template_id[:40]), 404) from None
    return {"id": template.id, "name": template.name, "files": _template_files(template.id),
            "origin": templates.origin(template)}


@router.post("/datasets/{ds_id}/build")
def rebuild(ds_id: str) -> dict:
    """Build a network again, after a build that failed or to bring it up to date."""
    try:
        ds.launch(ds_id, workers=2)
    except ds.DatasetError as err:
        raise refusal(err, 404 if err.code == "unknown_dataset" else 409) from err
    return ds.record(ds_id)


@router.post("/datasets/{ds_id}/cancel")
def cancel_build(ds_id: str) -> dict:
    """Stop a network's build and the processes it started; its record says where it stopped."""
    try:
        return ds.cancel(ds_id)
    except ds.DatasetError as err:
        raise refusal(err, 404 if err.code == "unknown_dataset" else 409) from err


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
    ds.restart_soon()
    return {"switching": True, "to": ds_id}
