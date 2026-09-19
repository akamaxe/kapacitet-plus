"""Day-ahead spot prices.

Default source: elprisetjustnu.se (free, no key; ENTSO-E data converted to SEK by the provider,
history from 1 Nov 2022; 15-minute resolution from Oct 2025). Optional: ENTSO-E Transparency
Platform with a personal token in .env (ENTSOE_API_TOKEN).
"""

from __future__ import annotations

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
ENTSOE_DOMAINS = {
    "SE1": "10Y1001A1001A44P",
    "SE2": "10Y1001A1001A45N",
    "SE3": "10Y1001A1001A46L",
    "SE4": "10Y1001A1001A47J",
}


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


# --- ENTSO-E (optional, needs a personal token) -------------------------------------------

def entsoe_token() -> str | None:
    load_dotenv()
    tok = os.environ.get("ENTSOE_API_TOKEN", "").strip()
    return tok or None


def fetch_entsoe_day_ahead(zone: str, year: int, *, refresh: bool = False) -> Path:
    token = entsoe_token()
    if token is None:
        raise FetchError("ENTSOE_API_TOKEN not set (see .env.example)")
    dest = raw("prices", f"entsoe_{zone}_{year}.xml")
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
    dest.write_bytes(resp.content)
    safe = {k: v for k, v in params.items() if k != "securityToken"}
    record(entry, source="entsoe", path=dest, url=ENTSOE_URL, params=safe,
           notes="Day-ahead prices in EUR/MWh (Publication_MarketDocument).")
    return dest


def parse_entsoe_xml(xml_bytes: bytes) -> pd.Series:
    """EUR/MWh series (UTC, interval starts) from an ENTSO-E A44 document.

    Handles PT60M and PT15M resolutions and the A03 curve type (missing positions repeat
    the previous price).
    """
    root = ET.fromstring(xml_bytes)
    ns = {"ns": root.tag.split("}")[0].strip("{")}
    out: dict[pd.Timestamp, float] = {}
    for period in root.iterfind(".//ns:TimeSeries/ns:Period", ns):
        start = pd.Timestamp(period.find("ns:timeInterval/ns:start", ns).text)
        end = pd.Timestamp(period.find("ns:timeInterval/ns:end", ns).text)
        res = period.find("ns:resolution", ns).text
        step = pd.Timedelta(minutes=15) if res == "PT15M" else pd.Timedelta(hours=1)
        n = int((end - start) / step)
        prices = np.full(n, np.nan)
        for pt in period.iterfind("ns:Point", ns):
            pos = int(pt.find("ns:position", ns).text)
            prices[pos - 1] = float(pt.find("ns:price.amount", ns).text)
        prices = pd.Series(prices).ffill().to_numpy()
        for i in range(n):
            out[start + i * step] = prices[i]
    s = pd.Series(out).sort_index()
    s.index = pd.to_datetime(s.index, utc=True)
    return s
