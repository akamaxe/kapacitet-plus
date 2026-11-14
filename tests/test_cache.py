"""The cache registry against work that runs in another thread, and the prices kept per folder.

The full run with data of 13-11-2026 failed in tests/test_mv_band.py depending on the order of the
modules: a test server's warm-up, started by the module before, was still loading the reference
network when the tiny grid's module emptied the caches, and stored 120 customers in them afterwards.
"""

from __future__ import annotations

import pandas as pd
import pytest

from kapacitet import cache, paths


def test_every_clear_starts_a_new_generation():
    before = cache.generation()
    cache.clear_all()
    assert cache.generation() == before + 1


def test_the_warm_up_is_off_in_the_tests_and_on_by_default(monkeypatch):
    from kapacitet.api import main

    assert not main.warm_up_enabled()           # tests/conftest.py
    monkeypatch.delenv("KAPACITET_WARM")
    assert main.warm_up_enabled()
    monkeypatch.setenv("KAPACITET_WARM", "0")
    assert not main.warm_up_enabled()


def test_the_warm_up_stops_once_the_caches_are_emptied_under_it(monkeypatch, tmp_path):
    """The warm-up loads the model, then the unbalanced model, the baselines and the profiles: a clear
    between two steps (a switch of folder) ends it before it caches anything of the old folder."""
    from kapacitet.api import main
    from kapacitet.engine import netmodel3ph

    monkeypatch.setenv("KAPACITET_WARM", "1")
    (tmp_path / "results").mkdir()
    # the results the screens need: the capacity part of a computation (api/main.py results_ready)
    for name in ("meta.json", "hosting_summary.json", "nodal.json"):
        (tmp_path / "results" / name).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(main, "processed", lambda *parts: tmp_path.joinpath(*parts))
    steps: list[str] = []

    def engine():
        steps.append("engine")
        cache.clear_all()                       # the folder changes while the model loads
        return {"model": None, "hp": None, "ev": None}

    monkeypatch.setattr(main, "_engine", engine)
    monkeypatch.setattr(netmodel3ph, "get_model3ph", lambda: steps.append("model3ph"))
    main._warm()
    assert steps == ["engine"]


def test_the_prices_are_kept_per_downloads_folder(monkeypatch, tmp_path):
    """data/raw does not move with a clear, so the folder is part of the key: two folders never share
    an answer, with or without a clear in between."""
    from kapacitet.engine import economics
    from kapacitet.ingest import prices
    from kapacitet.timeindex import year_index

    idx = year_index(2023)
    answers = {}
    for name, value in (("a", 1.0), ("b", 2.0)):
        folder = tmp_path / name
        (folder / "prices").mkdir(parents=True)
        (folder / "prices" / "elpris_SE3_2023.jsonl").write_text("", encoding="utf-8")
        answers[str(folder)] = pd.DataFrame({"sek_kwh": value, "eur_kwh": value / 10, "filled": False}, index=idx)
    monkeypatch.setattr(prices, "read_elpris", lambda zone, year: pd.DataFrame())
    monkeypatch.setattr(prices, "hourly_prices", lambda df, index: answers[str(paths.RAW)])
    economics.hourly.cache_clear()
    try:
        monkeypatch.setattr(paths, "RAW", tmp_path / "a")
        assert economics.hourly(2023, "SE3")["sek_kwh"].iloc[0] == pytest.approx(1.0)
        monkeypatch.setattr(paths, "RAW", tmp_path / "b")
        assert economics.hourly(2023, "SE3")["sek_kwh"].iloc[0] == pytest.approx(2.0)
    finally:
        economics.hourly.cache_clear()
