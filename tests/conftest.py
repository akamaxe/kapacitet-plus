"""Shared fixtures.

Unit tests run anywhere. Tests that need the pipeline output (`python -m kapacitet all`)
are skipped when `data/processed` is empty, so a fresh clone still gets a green run.
"""

from __future__ import annotations

import json

import pytest

from kapacitet.paths import processed

HAS_GRID = processed("grid", "net.json").exists()
HAS_RESULTS = processed("results", "showcase.json").exists()

needs_grid = pytest.mark.skipif(not HAS_GRID, reason="no built network (run: python -m kapacitet build)")
needs_results = pytest.mark.skipif(not HAS_RESULTS, reason="no results (run: python -m kapacitet all)")


@pytest.fixture(scope="session")
def model():
    from kapacitet.engine.netmodel import get_model

    return get_model()


@pytest.fixture(scope="session")
def results():
    def load(name: str) -> dict:
        return json.loads(processed("results", f"{name}.json").read_text(encoding="utf-8"))

    return load
