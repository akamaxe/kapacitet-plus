"""Which computed combination of sharing rule and basis a reader asks for, and where its files are.

`foresight` is the published planning basis (each hour's envelope knows that hour); `dayahead` is
what can be sent the day before. The rule defaults to the DSO's setting (kapacitet/dso.py). A
combination is served only when the operation stage verified it with zero violation hours over the
whole year; before that stage has run, the published files stand in for foresight with maxmin.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

from .. import dso
from ..paths import processed_ro

BASES = ("foresight", "dayahead")
BASIS_PATTERN = "^(foresight|dayahead)$"
POLICY_PATTERN = "^(" + "|".join(dso.POLICIES) + ")$"
COMMAND = "python -m kapacitet compute --stage extra --only operation"


@dataclass(frozen=True)
class Combo:
    basis: str
    policy: str
    results: Path        # compliance.json, compliance.txt, audit_trail.csv
    ui: Path             # envelopes, map state
    legacy: bool         # the published files, before the operation stage ran

    def result(self, name: str) -> Path:
        return self.results / name

    @property
    def envelopes(self) -> Path:
        return self.ui / ("envelopes_doe.npy" if self.legacy else "envelopes.npy")

    def nonfirm_cids(self) -> list[int]:
        if self.legacy:
            return json.loads(processed_ro("results", "envelopes_index.json").read_text(encoding="utf-8"))["cids"]
        return [int(c) for c in operation_index()["nonfirm_cids"]]

    def state(self, suffix: str) -> Path:
        return self.ui / f"state_doe.{suffix}"


@lru_cache(maxsize=2)
def _index(stamp: float) -> dict:
    return json.loads(processed_ro("results", "operation", "index.json").read_text(encoding="utf-8"))


def operation_index() -> dict | None:
    path = processed_ro("results", "operation", "index.json")
    return _index(path.stat().st_mtime) if path.exists() else None


def resolve(basis: str | None, policy: str | None) -> Combo:
    basis = basis or "foresight"
    policy = policy or dso.load().allocation_policy
    if basis not in BASES or policy not in dso.POLICIES:
        raise HTTPException(422, {"code": "bad_combo", "message": f"unknown basis or rule: {basis}, {policy}"})
    folder = processed_ro("results", "operation", basis, policy)
    if (folder / "issued.json").exists():
        info = ((operation_index() or {}).get("combos", {}).get(basis, {}) or {}).get(policy, {})
        if not info.get("verified", False):
            raise HTTPException(409, {"code": "unverified", "message": f"{basis}/{policy} did not verify over the year",
                                      "params": {"basis": basis, "policy": policy}})
        return Combo(basis, policy, folder, processed_ro("ui", "operation", basis, policy), legacy=False)
    if basis == "foresight" and policy == "maxmin" and processed_ro("results", "compliance.json").exists():
        return Combo(basis, policy, processed_ro("results"), processed_ro("ui"), legacy=True)
    raise HTTPException(404, {"code": "not_computed", "file": f"operation/{basis}/{policy}", "command": COMMAND,
                              "message": f"{basis}/{policy} has not been computed yet"})
