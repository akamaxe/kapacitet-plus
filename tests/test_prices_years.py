"""Spot prices over the eleven weather years (ingest/prices.py, engine/economics.py, engine/summaries.py).

elprisetjustnu has the years from 2023, ENTSO-E the earlier ones when a token is set; the business
case's central figure is the mean of 2023-2025 and its range spreads over every year with prices.
Nothing here fetches anything: the ENTSO-E documents are written by the test.
"""

from __future__ import annotations

import argparse
import json
import logging
import zipfile

import numpy as np
import pandas as pd
import pytest

NS = "urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3"


def _period(start: str, end: str, res: str, prices: dict[int, float]) -> str:
    pts = "".join(f"<Point><position>{p}</position><price.amount>{v}</price.amount></Point>"
                  for p, v in sorted(prices.items()))
    return (f"<Period><timeInterval><start>{start}</start><end>{end}</end></timeInterval>"
            f"<resolution>{res}</resolution>{pts}</Period>")


def _doc(*periods: str) -> bytes:
    series = "".join(f"<TimeSeries><mRID>{i + 1}</mRID>{p}</TimeSeries>" for i, p in enumerate(periods))
    return f'<?xml version="1.0" encoding="UTF-8"?><Publication_MarketDocument xmlns="{NS}">{series}</Publication_MarketDocument>'.encode()


def test_an_hourly_document_with_a_dst_day_and_a_repeated_price_parses():
    from kapacitet.ingest.prices import parse_entsoe_xml

    # 31 March 2019 in Sweden is 23 hours long: 30 Mar 23:00Z to 31 Mar 22:00Z
    s = parse_entsoe_xml(_doc(_period("2019-03-30T23:00Z", "2019-03-31T22:00Z", "PT60M",
                                      {p: 10.0 * p for p in range(1, 24) if p != 5})))
    assert len(s) == 23 and str(s.index.tz) == "UTC"
    assert s.index[0] == pd.Timestamp("2019-03-30T23:00Z")
    assert s.iloc[4] == s.iloc[3] == 40.0         # A03: a missing position repeats the previous price


def test_fifteen_minute_values_are_averaged_and_win_over_hourly_ones():
    from kapacitet.ingest.prices import hourly_prices, parse_entsoe_xml

    fine = _period("2019-06-01T00:00Z", "2019-06-01T02:00Z", "PT15M", {p: float(p) for p in range(1, 9)})
    coarse = _period("2019-06-01T00:00Z", "2019-06-01T03:00Z", "PT60M", {1: 100.0, 2: 100.0, 3: 30.0})
    s = parse_entsoe_xml(_doc(coarse, fine))
    df = pd.DataFrame({"start": s.index, "sek_kwh": s.to_numpy(), "eur_kwh": s.to_numpy()})
    idx = pd.date_range("2019-06-01T00:00Z", periods=3, freq="h")
    h = hourly_prices(df, idx)
    assert h["sek_kwh"].tolist() == pytest.approx([2.5, 6.5, 30.0])   # means of 1-4 and 5-8, then the hourly
    assert not h["filled"].any()


def test_a_gap_is_filled_up_to_six_hours_and_flagged():
    from kapacitet.ingest import prices

    doc = _doc(_period("2018-12-31T23:00Z", "2019-01-01T01:00Z", "PT60M", {1: 20.0, 2: 20.0}),
               _period("2019-01-01T04:00Z", "2019-01-01T06:00Z", "PT60M", {1: 50.0, 2: 50.0}))
    s = prices.parse_entsoe_xml(doc)
    rates = pd.Series([11.0, 11.5], index=pd.to_datetime(["2018-12-28", "2019-01-02"]))
    frame = prices.entsoe_frame(s, rates)
    # each interval takes its delivery day's rate (Swedish date), or the last one published before it
    assert frame["sek_kwh"].tolist() == pytest.approx([0.020 * 11.0] * 2 + [0.050 * 11.0] * 2)
    assert frame["eur_kwh"].iloc[0] == pytest.approx(0.020)
    idx = pd.date_range("2018-12-31T23:00Z", periods=7, freq="h")
    h = prices.hourly_prices(frame, idx)
    assert h["filled"].tolist() == [False, False, True, True, True, False, False]
    assert not h["sek_kwh"].isna().any()
    assert h["eur_kwh"].iloc[2] == pytest.approx(0.020 + (0.050 - 0.020) / 4)


def test_an_entsoe_year_reaches_the_study_index_through_the_ecb_rate(tmp_path, monkeypatch):
    from kapacitet import paths
    from kapacitet.engine import economics
    from kapacitet.ingest import prices
    from kapacitet.timeindex import year_index

    monkeypatch.setattr(paths, "RAW", tmp_path)
    idx = year_index(2019)
    start = idx[0].strftime("%Y-%m-%dT%H:%MZ")
    end = (idx[-1] + pd.Timedelta(hours=1)).strftime("%Y-%m-%dT%H:%MZ")
    (tmp_path / "prices").mkdir()
    prices.entsoe_path("SE3", 2019).write_bytes(_doc(_period(start, end, "PT60M", {1: 40.0})))
    assert prices.entsoe_hourly("SE3", 2019, idx) is None        # no ECB rates yet: no SEK prices
    pd.DataFrame({"date": ["2018-12-31"], "sek_per_eur": [10.0]}).to_csv(prices.ecb_path(2015, 2022), index=False)
    economics.hourly.cache_clear()
    try:
        h = economics.hourly(2019, "SE3")
        assert len(h) == len(idx) and h.index.equals(idx)
        assert h["sek_kwh"].to_numpy() == pytest.approx(np.full(len(idx), 0.4))
        assert prices.source_for_year(2019) == "entsoe" and prices.source_for_year(2023) == "elprisetjustnu"
    finally:
        economics.hourly.cache_clear()


def _fetch(monkeypatch, token):
    from kapacitet import pipeline
    from kapacitet.ingest import prices

    calls = {"elpris": [], "entsoe": [], "ecb": []}
    monkeypatch.setattr(prices, "entsoe_token", lambda: token)
    monkeypatch.setattr(prices, "fetch_elpris_year", lambda z, y, **k: calls["elpris"].append((z, y)))
    monkeypatch.setattr(prices, "fetch_entsoe_day_ahead", lambda z, y, **k: calls["entsoe"].append((z, y)))
    monkeypatch.setattr(prices, "fetch_ecb_eursek", lambda a, b, **k: calls["ecb"].append((a, b)))
    pipeline.cmd_fetch(argparse.Namespace(only="prices", refresh=False))
    return calls


def test_without_a_token_the_early_years_are_skipped_with_a_log_line(monkeypatch, caplog):
    from kapacitet.config import settings

    with caplog.at_level(logging.WARNING):
        calls = _fetch(monkeypatch, None)
    zone = settings().site.bidding_zone
    assert calls["entsoe"] == [] and calls["ecb"] == []
    assert {y for z, y in calls["elpris"] if z == zone} >= {2023, 2024, 2025}
    assert all(y >= 2023 for _, y in calls["elpris"])
    assert any("ENTSOE_API_TOKEN not set" in r.getMessage() and "2015–2022" in r.getMessage() for r in caplog.records)


def test_with_a_token_the_early_weather_years_come_from_entsoe(monkeypatch, caplog):
    from kapacitet.config import settings

    secret = "a1b2c3d4-0000-4fff-8eee-123456789abc"
    with caplog.at_level(logging.DEBUG):
        calls = _fetch(monkeypatch, secret)
    zone = settings().site.bidding_zone
    assert calls["entsoe"] == [(zone, y) for y in range(2015, 2023)]
    assert calls["ecb"] == [(2015, 2022)]
    assert not any(y < 2023 for _, y in calls["elpris"])
    assert all(secret not in r.getMessage() for r in caplog.records)


def test_central_is_the_mean_of_its_years_and_range_spreads_over_all():
    from kapacitet.engine.summaries import price_basis, spread

    vals = {y: float(y - 2014) for y in range(2015, 2026)}          # 1 ... 11
    s = spread(vals, [2023, 2024, 2025])
    assert s["central"] == pytest.approx(10.0)
    assert s["range"]["min"] == 1.0 and s["range"]["max"] == 11.0
    assert s["range"]["p10"] == pytest.approx(2.0) and s["range"]["p90"] == pytest.approx(10.0)
    assert s["years_central"] == [2023, 2024, 2025] and s["years_range"] == list(range(2015, 2026))
    assert not s["range_is_central"]
    # only the central years have prices: the range is those years, and says so
    only = spread({y: vals[y] for y in (2023, 2024, 2025)}, [2023, 2024, 2025])
    assert only["range_is_central"] and only["range"]["min"] == 9.0 and only["range"]["max"] == 11.0
    b = price_basis(range(2015, 2026), [2023, 2024, 2025])
    assert b["source_keys"] == ["elprisetjustnu", "entsoe"] and not b["range_is_central"]
    assert price_basis([2023, 2024, 2025], [2023, 2024, 2025])["source_keys"] == ["elprisetjustnu"]
    assert spread({}, [2023]) is None


def _over(years):
    """An over-installation result with three points, the budget at the second."""
    def per_year(scale):
        return [{"year": y, "potential_kwh": 100.0 * scale, "curtailed_kwh": 5.0 * scale,
                 "delivered_value": {"net_sek": 50.0 * scale * (y - 2014)},
                 "curtailed_value": {"net_sek": 2.0 * scale * (y - 2014)}} for y in years]
    pts = [{"adopters": a, "kw_ac": 100.0 * a, "kw_ac_nonfirm": 10.0 * a, "per_year": per_year(a)} for a in (1, 2, 3)]
    return {"points": pts, "price_years": list(years), "doe_adopters": 2, "z_star_pct": 3.0}


def test_the_dsos_case_has_a_central_figure_and_a_range_over_the_years():
    from kapacitet.engine.summaries import business, overinstallation_summary, reinforcement

    over = overinstallation_summary(_over(range(2015, 2026)), central=[2023, 2024, 2025])
    assert over["price_years"] == [2023, 2024, 2025]
    k = over["budget_spread"]["curtailed_sek"]
    assert over["budget"]["curtailed_sek"] == pytest.approx(k["central"]) == pytest.approx(4.0 * 10)
    assert k["range"]["min"] == pytest.approx(4.0) and k["range"]["max"] == pytest.approx(44.0)
    tornado = {"base": {"static_kw": 1, "timeseries_kw": 2, "doe_kw": 3},
               "cases": [{"key": "trafo", "level": "0.63 MVA", "static_kw": 1, "timeseries_kw": 2, "doe_kw": 3}]}
    cost = {"reinforce_trafo_500_sek": 150_000.0, "reinforce_trafo_800_sek": 210_000.0,
            "reinforce_station_800_sek": 300_000.0, "reinforce_urban_supplement_sek": 100_000.0,
            "reinforce_price_level_year": 2022}
    reinf = reinforcement(tornado, over, cost)
    b = business(reinf, 4.53, horizon=4)
    lo = b["range"]["transformer"][0]
    assert lo["central"] == pytest.approx(b["at_horizon"]["transformer"][0]["net_sek"])
    # more curtailment, less worth deferring: the year with the most is the range's minimum
    assert lo["range"]["min"] == pytest.approx(next(e["value"] for e in lo["by_year"] if e["year"] == 2025))
    assert lo["years_range"] == list(range(2015, 2026)) and lo["years_central"] == [2023, 2024, 2025]
    assert b["prices"]["source_keys"] == ["elprisetjustnu", "entsoe"]
    # only 2023-2025 priced: the range is those years and the API says so
    only = business(reinforcement(tornado, overinstallation_summary(_over((2023, 2024, 2025)), central=[2023, 2024, 2025]),
                                  cost), 4.53, horizon=4)
    assert only["prices"]["range_is_central"] and only["range"]["transformer"][0]["range_is_central"]


def test_the_households_case_is_central_over_2023_2025_with_its_range():
    from kapacitet.engine.economics import owner_case
    from kapacitet.timeindex import year_index

    idx = year_index(2025)
    rows = [{"year": y, "self_use_kwh": 2000.0, "self_use_spot_sek": 100.0 * (y - 2014), "export_kwh": 3000.0,
             "export_sek": 100.0 * (y - 2014), "curtailed_kwh": 100.0, "curtailed_sek_net": 10.0} for y in range(2015, 2026)]
    res = {"economics": {"by_year": rows}, "profiles": {"year": 2025, "desired_kw": np.ones(len(idx)).tolist(),
                                                      "curtailed_kw": np.zeros(len(idx)).tolist()}}
    out = owner_case(res, years=4, rate_pct=3.0, start_year=2030, central=[2023, 2024, 2025])
    central = owner_case({**res, "economics": {"by_year": rows[-3:]}}, years=4, rate_pct=3.0, start_year=2030,
                         central=[2023, 2024, 2025])
    assert out["price_years"] == [2023, 2024, 2025]
    assert out["connect_now_sek"] == pytest.approx(central["connect_now_sek"])
    r = out["range"]["connect_now_sek"]
    assert r["central"] == pytest.approx(out["connect_now_sek"])
    assert r["range"]["min"] < r["central"] < r["range"]["max"] and r["years_range"] == list(range(2015, 2026))
    assert out["prices"]["source_keys"] == ["elprisetjustnu", "entsoe"]
    assert central["prices"]["range_is_central"]


def test_no_entsoe_file_enters_a_bundle_and_no_token_rides_in_its_manifest(tmp_path, monkeypatch):
    from kapacitet.bundle import BundleError, excluded, make_bundle
    from kapacitet.ingest import prices

    root = tmp_path / "data"
    files = {"processed/results/showcase.json": "{}", "raw/prices/elpris_SE3_2025.jsonl": "",
             "raw/prices/entsoe_SE3_2019.xml": "<x/>", "raw/prices/entsoe_SE3_2015.xml": "<x/>",
             "raw/prices/ecb_eursek_2015_2022.csv": "date,sek_per_eur\n",
             "raw/manifest.json": json.dumps({"entsoe_SE3_2019": {"params": {"securityToken": "***"}}})}
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    secret = "f00dfeed-1234-4abc-9def-0123456789ab"
    monkeypatch.setattr(prices, "entsoe_token", lambda: secret)
    make_bundle(tmp_path / "b.zip", data_dir=root)
    names = zipfile.ZipFile(tmp_path / "b.zip").namelist()
    assert not [n for n in names if "entsoe" in n.lower()]
    assert "data/raw/prices/elpris_SE3_2025.jsonl" in names and "data/raw/manifest.json" in names
    assert excluded("raw/prices/entsoe_SE3_2019.xml") and not excluded("raw/prices/elpris_SE3_2019.jsonl")
    for n in names:
        assert secret not in zipfile.ZipFile(tmp_path / "b.zip").read(n).decode("utf-8", "replace")
    # a manifest that somehow holds the token is refused before the archive is written
    (root / "raw/manifest.json").write_text(json.dumps({"x": {"params": {"securityToken": secret}}}), encoding="utf-8")
    with pytest.raises(BundleError) as err:
        make_bundle(tmp_path / "c.zip", data_dir=root)
    assert err.value.code == "secret_in_manifest" and secret not in str(err.value)
    assert not (tmp_path / "c.zip").exists()


# --- the real fetch path, with only the transport stubbed ----------------------------------

SECRET = "a1b2c3d4-0000-4fff-8eee-123456789abc"


class _Reply:
    def __init__(self, status, content, url):
        self.status_code, self.content, self.url, self.text = status, content, url, content.decode("utf-8", "replace")


def _real_fetch(monkeypatch, tmp_path, status, body):
    from urllib.parse import urlencode

    from kapacitet.ingest import prices, provenance

    raw_dir = tmp_path / "raw"
    monkeypatch.setattr(provenance, "RAW", raw_dir)
    monkeypatch.setattr(provenance, "MANIFEST", raw_dir / "manifest.json")
    monkeypatch.setattr(prices, "raw", lambda *p: raw_dir.joinpath(*p))
    monkeypatch.setattr(prices, "entsoe_token", lambda: SECRET)

    class _Session:
        def request(self, method, url, params=None, data=None, timeout=None):
            return _Reply(status, body, f"{url}?{urlencode(params or {})}")

    monkeypatch.setattr(provenance, "session", lambda: _Session())
    return raw_dir


def test_a_failed_entsoe_request_leaks_no_token(monkeypatch, tmp_path, caplog):
    from kapacitet.ingest import prices
    from kapacitet.ingest.provenance import FetchError

    raw_dir = _real_fetch(monkeypatch, tmp_path, 401, b"unauthorized")
    with caplog.at_level(logging.DEBUG), pytest.raises(FetchError) as err:
        prices.fetch_entsoe_day_ahead("SE3", 2019)
    assert SECRET not in str(err.value) and all(SECRET not in r.getMessage() for r in caplog.records)
    assert not (raw_dir / "manifest.json").exists()


def test_a_successful_entsoe_fetch_writes_the_file_and_no_token_to_the_manifest(monkeypatch, tmp_path, caplog):
    from kapacitet.ingest import prices

    body = _doc(_period("2018-12-31T23:00Z", "2019-01-01T01:00Z", "PT60M", {1: 40.0, 2: 41.0}))
    raw_dir = _real_fetch(monkeypatch, tmp_path, 200, body)
    with caplog.at_level(logging.DEBUG):
        dest = prices.fetch_entsoe_day_ahead("SE3", 2019)
    assert dest.read_bytes() == body
    manifest = (raw_dir / "manifest.json").read_text(encoding="utf-8")
    assert "entsoe_SE3_2019" in manifest and SECRET not in manifest
    assert all(SECRET not in r.getMessage() for r in caplog.records)


def test_an_acknowledgement_reply_is_an_error_and_is_not_cached(monkeypatch, tmp_path):
    from kapacitet.ingest import prices
    from kapacitet.ingest.provenance import FetchError

    ack = (f'<?xml version="1.0"?><Acknowledgement_MarketDocument xmlns="{NS}">'
           "<Reason><code>999</code><text>No matching data found</text></Reason>"
           "</Acknowledgement_MarketDocument>").encode()
    raw_dir = _real_fetch(monkeypatch, tmp_path, 200, ack)
    with pytest.raises(FetchError, match="no prices for SE3 2016"):
        prices.fetch_entsoe_day_ahead("SE3", 2016)
    assert not (raw_dir / "prices" / "entsoe_SE3_2016.xml").exists()
    assert not (raw_dir / "manifest.json").exists()


# --- the API's attribution and the unpack path ---------------------------------------------

def test_price_sources_attribute_entsoe_and_the_ecb_only_for_their_years():
    from kapacitet.api.main import price_sources

    assert price_sources(None) == ("elprisetjustnu",)
    assert price_sources({"source_keys": ["elprisetjustnu"]}) == ("elprisetjustnu",)
    assert price_sources({"source_keys": ["elprisetjustnu", "entsoe"]}) == ("elprisetjustnu", "entsoe", "ecb_exr")


def test_hourly_prefers_elprisetjustnu_when_both_files_exist(monkeypatch, tmp_path):
    from kapacitet import paths
    from kapacitet.engine import economics
    from kapacitet.ingest import prices
    from kapacitet.timeindex import year_index

    monkeypatch.setattr(paths, "RAW", tmp_path)
    (tmp_path / "prices").mkdir()
    (tmp_path / "prices" / "elpris_SE3_2023.jsonl").write_text("", encoding="utf-8")
    idx = year_index(2023)
    marker = pd.DataFrame({"sek_kwh": 1.0, "eur_kwh": 0.1, "filled": False}, index=idx)
    monkeypatch.setattr(prices, "read_elpris", lambda z, y: pd.DataFrame())
    monkeypatch.setattr(prices, "hourly_prices", lambda df, i: marker)
    monkeypatch.setattr(prices, "entsoe_hourly", lambda *a: pytest.fail("ENTSO-E used although elprisetjustnu has the year"))
    economics.hourly.cache_clear()
    try:
        assert economics.hourly(2023, "SE3") is marker
    finally:
        economics.hourly.cache_clear()


def test_an_old_bundle_with_entsoe_raw_files_does_not_unpack_them(tmp_path, monkeypatch):
    from kapacitet import bundle

    root = tmp_path / "data"
    for rel, body in {"processed/results/showcase.json": "{}", "raw/prices/elpris_SE3_2025.jsonl": "x",
                      "raw/prices/entsoe_SE3_2019.xml": "<x/>"}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    with monkeypatch.context() as m:                     # an older version, which did not exclude them
        m.setattr(bundle, "EXCLUDED", ())
        bundle.make_bundle(tmp_path / "old.zip", data_dir=root)
    assert any("entsoe" in n for n in zipfile.ZipFile(tmp_path / "old.zip").namelist())
    target = tmp_path / "out"
    bundle.unpack_bundle(tmp_path / "old.zip", data_dir=target, web_dir=tmp_path / "web", only_data=True)
    assert (target / "raw/prices/elpris_SE3_2025.jsonl").exists()
    assert not list(target.rglob("entsoe*"))
