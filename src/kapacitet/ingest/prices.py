"""Day-ahead spot prices.

Default source: elprisetjustnu.se (free, no key; ENTSO-E data converted to SEK by the provider,
history from 1 Nov 2022; 15-minute resolution from Oct 2025). The weather years before its first
whole year come from the ENTSO-E Transparency Platform with a personal token in .env
(ENTSOE_API_TOKEN), converted to SEK at the ECB's daily reference rate. Without the token those
years are skipped, and the business case's range is only the elprisetjustnu years.

The token never leaves the request: not in a log line, the manifest, a seal or a bundle. The raw
ENTSO-E XML stays out of bundles too (bundle.EXCLUDED) until its licence is decided.
"""

from __future__ import annotations

import io
import json
import os
import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import load_dotenv
from ..paths import raw
from .provenance import FetchError, cached, http, record, session

ELPRIS_URL = "https://www.elprisetjustnu.se/api/v1/prices/{y}/{m:02d}-{d:02d}_{zone}.json"
ENTSOE_URL = "https://web-api.tp.entsoe.eu/api"
# elprisetjustnu starts on 1 Nov 2022, so its first whole study year is 2023 (a UTC+1 year also
# needs 31 Dec of the year before); the weather years before it are ENTSO-E's
ELPRIS_FIRST_DAY = date(2022, 11, 1)
ELPRIS_FIRST_YEAR = 2023
ECB_URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.SEK.EUR.SP00.A"
ENTSOE_DOMAINS = {
    "SE1": "10Y1001A1001A44P",
    "SE2": "10Y1001A1001A45N",
    "SE3": "10Y1001A1001A46L",
    "SE4": "10Y1001A1001A47J",
}


def source_for_year(year: int) -> str:
    """Which source a year's prices come from: elprisetjustnu where it has the whole year, else ENTSO-E."""
    return "elprisetjustnu" if year >= ELPRIS_FIRST_YEAR else "entsoe"


def elpris_path(zone: str, year: int) -> Path:
    return raw("prices", f"elpris_{zone}_{year}.jsonl")


def fetch_elpris_year(zone: str, year: int, *, refresh: bool = False, pause_s: float = 0.05) -> Path:
    """Fetch every day of `year` (plus 31 Dec of the previous year for the UTC+1 boundary)."""
    dest = elpris_path(zone, year)
    entry = f"elpris_{zone}_{year}"
    if cached(entry, dest, refresh=refresh):
        return dest
    days = [date(year - 1, 12, 31)] + [date(year, 1, 1) + timedelta(days=i) for i in range(366)]
    days = [d for d in days if d.year < year + 1]
    lines, missing = [], []
    for d in days:
        if d > date.today() + timedelta(days=1):
            break
        url = ELPRIS_URL.format(y=d.year, m=d.month, d=d.day, zone=zone)
        try:
            resp = session().get(url, timeout=30)
            if resp.status_code == 404:
                missing.append(d.isoformat())
                continue
            resp.raise_for_status()
            rows = resp.json()
        except Exception:  # retry once with the robust helper
            try:
                rows = http("GET", url, timeout=30).json()
            except FetchError:
                missing.append(d.isoformat())
                continue
        for r in rows:
            lines.append(json.dumps({"start": r["time_start"], "end": r["time_end"],
                                     "sek_kwh": r["SEK_per_kWh"], "eur_kwh": r["EUR_per_kWh"],
                                     "exr": r.get("EXR")}))
        time.sleep(pause_s)
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    record(entry, source="elprisetjustnu", path=dest,
           url=ELPRIS_URL.format(y=year, m=1, d=1, zone=zone).replace("01-01", "{MM}-{DD}"),
           params={"zone": zone, "year": year, "days_missing": missing}, rows=len(lines),
           notes="Daily JSON files concatenated; 15-min values (from Oct 2025) are averaged to hourly means.")
    return dest


def read_elpris(zone: str, year: int) -> pd.DataFrame:
    """Price intervals with UTC start/end, SEK/kWh and EUR/kWh."""
    rows = [json.loads(line) for line in elpris_path(zone, year).read_text(encoding="utf-8").splitlines() if line]
    df = pd.DataFrame(rows)
    df["start"] = pd.to_datetime(df["start"], utc=True)
    df["end"] = pd.to_datetime(df["end"], utc=True)
    return df.drop_duplicates("start").sort_values("start").reset_index(drop=True)


def hourly_prices(df: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    """Hourly mean price on the hour-beginning `index` (UTC). 15-min intervals are averaged."""
    hour = df["start"].dt.floor("h")
    agg = df.assign(hour=hour).groupby("hour")[["sek_kwh", "eur_kwh"]].mean()
    out = agg.reindex(index)
    out["filled"] = out["sek_kwh"].isna()
    out[["sek_kwh", "eur_kwh"]] = out[["sek_kwh", "eur_kwh"]].interpolate(limit=6, limit_direction="both")
    return out


# --- ENTSO-E (the years before elprisetjustnu, needs a personal token) ---------------------

def entsoe_token() -> str | None:
    load_dotenv()
    tok = os.environ.get("ENTSOE_API_TOKEN", "").strip()
    return tok or None


def entsoe_path(zone: str, year: int) -> Path:
    return raw("prices", f"entsoe_{zone}_{year}.xml")


def fetch_entsoe_day_ahead(zone: str, year: int, *, refresh: bool = False) -> Path:
    token = entsoe_token()
    if token is None:
        raise FetchError("ENTSOE_API_TOKEN not set (see .env.example)")
    dest = entsoe_path(zone, year)
    entry = f"entsoe_{zone}_{year}"
    if cached(entry, dest, refresh=refresh):
        return dest
    params = {
        "securityToken": token,
        "documentType": "A44",
        "in_Domain": ENTSOE_DOMAINS[zone],
        "out_Domain": ENTSOE_DOMAINS[zone],
        "periodStart": f"{year - 1}12312300",
        "periodEnd": f"{year}12312300",
    }
    resp = http("GET", ENTSOE_URL, params=params, timeout=180)
    # "no matching data" comes back as an Acknowledgement document at HTTP 200: keep it out of the cache
    try:
        empty = parse_entsoe_xml(resp.content).empty
    except ET.ParseError:
        empty = True
    if empty:
        raise FetchError(f"ENTSO-E returned no prices for {zone} {year}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    safe = {k: v for k, v in params.items() if k != "securityToken"}
    record(entry, source="entsoe", path=dest, url=ENTSOE_URL, params=safe,
           notes="Day-ahead prices in EUR/MWh (Publication_MarketDocument), converted to SEK at the "
                 "ECB's daily rate and averaged to hourly means. The raw XML stays out of data bundles.")
    return dest


def parse_entsoe_xml(xml_bytes: bytes) -> pd.Series:
    """EUR/MWh series (UTC, interval starts) from an ENTSO-E A44 document.

    Handles PT60M and PT15M resolutions and the A03 curve type (missing positions repeat the
    previous price). Periods are in UTC, so a delivery day across a daylight-saving change is
    simply 23 or 25 hours long. Where an hour is published at both resolutions, the 15-minute
    values win. An acknowledgement document (no data) gives an empty series.
    """
    root = ET.fromstring(xml_bytes)
    ns = {"ns": root.tag.split("}")[0].strip("{") if root.tag.startswith("{") else ""}
    by_res: dict[str, dict[pd.Timestamp, float]] = {"PT15M": {}, "PT60M": {}}
    for period in root.iterfind(".//ns:TimeSeries/ns:Period", ns):
        start = pd.Timestamp(period.find("ns:timeInterval/ns:start", ns).text)
        end = pd.Timestamp(period.find("ns:timeInterval/ns:end", ns).text)
        res = period.find("ns:resolution", ns).text
        step = pd.Timedelta(minutes=15) if res == "PT15M" else pd.Timedelta(hours=1)
        n = int((end - start) / step)
        prices = np.full(n, np.nan)
        for pt in period.iterfind("ns:Point", ns):
            pos = int(pt.find("ns:position", ns).text)
            if 1 <= pos <= n:
                prices[pos - 1] = float(pt.find("ns:price.amount", ns).text)
        prices = pd.Series(prices).ffill().to_numpy()
        out = by_res["PT15M" if res == "PT15M" else "PT60M"]
        for i in range(n):
            out[start + i * step] = prices[i]
    fine = by_res["PT15M"]
    fine_hours = {t.floor("h") for t in fine}
    merged = {t: v for t, v in by_res["PT60M"].items() if t.floor("h") not in fine_hours}
    merged.update(fine)
    if not merged:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))
    s = pd.Series(merged).sort_index()
    s.index = pd.to_datetime(s.index, utc=True)
    return s


# --- ECB reference rate (EUR to SEK for the ENTSO-E years) ---------------------------------

def ecb_path(first_year: int, last_year: int) -> Path:
    return raw("prices", f"ecb_eursek_{first_year}_{last_year}.csv")


def fetch_ecb_eursek(first_year: int, last_year: int, *, refresh: bool = False) -> Path:
    """The ECB's daily EUR/SEK reference rate over the years whose ENTSO-E prices are converted."""
    dest = ecb_path(first_year, last_year)
    entry = f"ecb_eursek_{first_year}_{last_year}"
    if cached(entry, dest, refresh=refresh):
        return dest
    params = {"startPeriod": f"{first_year - 1}-12-01", "endPeriod": f"{last_year}-12-31", "format": "csvdata"}
    resp = http("GET", ECB_URL, params=params, timeout=60)
    df = pd.read_csv(io.StringIO(resp.text))
    if not {"TIME_PERIOD", "OBS_VALUE"} <= set(df.columns):
        raise FetchError("ECB reply without TIME_PERIOD/OBS_VALUE columns")
    out = df[["TIME_PERIOD", "OBS_VALUE"]].dropna().rename(columns={"TIME_PERIOD": "date", "OBS_VALUE": "sek_per_eur"})
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(dest, index=False)
    record(entry, source="ecb_exr", path=dest, url=ECB_URL, params=params, rows=len(out),
           notes="Daily EUR/SEK reference rate; days without one take the last rate published before them.")
    return dest


def read_ecb_eursek() -> pd.Series | None:
    """SEK per EUR by date, from every ECB file fetched (None if there is none)."""
    files = sorted(raw("prices").glob("ecb_eursek_*.csv"))
    if not files:
        return None
    df = pd.concat([pd.read_csv(f) for f in files]).drop_duplicates("date")
    s = pd.Series(df["sek_per_eur"].to_numpy(dtype=float), index=pd.to_datetime(df["date"]))
    return s.sort_index()


def entsoe_frame(eur_mwh: pd.Series, sek_per_eur: pd.Series) -> pd.DataFrame:
    """ENTSO-E prices as `read_elpris` gives them: start (UTC), SEK/kWh and EUR/kWh. Each interval
    takes the ECB rate of its delivery day (Swedish local date), or the last one published before it."""
    start = eur_mwh.index
    day = pd.DatetimeIndex(start.tz_convert("Europe/Stockholm").date)
    rates = sek_per_eur.sort_index()
    rates = rates[~rates.index.duplicated()]
    rate = rates.reindex(rates.index.union(day.unique())).ffill().bfill().reindex(day).to_numpy()
    eur_kwh = eur_mwh.to_numpy(dtype=float) / 1000.0
    return pd.DataFrame({"start": start, "sek_kwh": eur_kwh * rate, "eur_kwh": eur_kwh})


def entsoe_hourly(zone: str, year: int, index: pd.DatetimeIndex) -> pd.DataFrame | None:
    """Hourly prices of an ENTSO-E year on `index` (None if its XML or the ECB rates are missing)."""
    path = entsoe_path(zone, year)
    rates = read_ecb_eursek()
    if not path.exists() or rates is None:
        return None
    s = parse_entsoe_xml(path.read_bytes())
    if s.empty:
        return None
    return hourly_prices(entsoe_frame(s, rates), index)
