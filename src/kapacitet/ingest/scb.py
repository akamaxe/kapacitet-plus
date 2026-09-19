"""Statistics Sweden (SCB) PxWeb API: household size in owner-occupied småhus (Stockholm)."""

from __future__ import annotations

import json
from pathlib import Path

from ..paths import raw
from .provenance import cached, http, record

URL = "https://api.scb.se/OV0104/v1/doris/sv/ssd/START/HE/HE0111/HE0111A/HushallT26"
STOCKHOLM = "0180"


def path() -> Path:
    return raw("scb", "hushallT26_stockholm_smag.json")


def fetch_household_sizes(year: int = 2024, *, refresh: bool = False) -> Path:
    dest = path()
    if cached("scb_hushallT26", dest, refresh=refresh):
        return dest
    query = {
        "query": [
            {"code": "Region", "selection": {"filter": "item", "values": [STOCKHOLM]}},
            {"code": "Boendeform", "selection": {"filter": "item", "values": ["SMAG"]}},
            {"code": "Hushallsstorlek", "selection": {"filter": "item",
                                                      "values": ["1P", "2P", "3P", "4P", "5P", "6P", "7+P"]}},
            {"code": "ContentsCode", "selection": {"filter": "item", "values": ["HE0111BZ"]}},
            {"code": "Tid", "selection": {"filter": "item", "values": [str(year)]}},
        ],
        "response": {"format": "json"},
    }
    resp = http("POST", URL, data=json.dumps(query), timeout=60)
    data = resp.json()
    dest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    record("scb_hushallT26", source="scb", path=dest, url=URL,
           params={"region": "0180 Stockholm", "boendeform": "SMAG (småhus, äganderätt)", "year": year},
           rows=len(data.get("data", [])), notes="Number of households by household size.")
    return dest


def household_size_shares() -> dict[int, float]:
    """Share of households with 1..5 occupants (5 = five or more) in Stockholm owner-occupied småhus."""
    data = json.loads(path().read_text(encoding="utf-8"))
    counts: dict[int, float] = {}
    for row in data["data"]:
        size = row["key"][2]
        n = float(row["values"][0])
        k = 5 if size in ("5P", "6P", "7+P") else int(size[0])
        counts[k] = counts.get(k, 0.0) + n
    total = sum(counts.values())
    return {k: counts[k] / total for k in sorted(counts)}
