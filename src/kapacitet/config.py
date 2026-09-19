"""Loading of config/site.toml and config/assumptions.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from .paths import CONFIG, ROOT


@dataclass(frozen=True)
class Site:
    key: str
    name: str
    center_lat: float
    center_lon: float
    substation_osm: str
    osm_radius_m: float
    max_houses: int
    max_network_distance_m: float
    bidding_zone: str
    metering_grid_area: str


@dataclass(frozen=True)
class Settings:
    site: Site
    reference_year: int
    weather_years: tuple[int, ...]
    display_utc_offset_h: int
    temperature_station: int
    strang: dict[str, int]
    price_zones: tuple[str, ...]
    price_years: tuple[int, ...]
    national_year: int
    user_agent: str
    overpass_endpoints: tuple[str, ...]
    raw: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class Assumption:
    """A model input with its provenance, shown in the UI's assumptions table."""

    key: str
    value: Any
    unit: str
    tag: str  # sourced | regulation | library | derived | team-assumption
    label: str
    source: str
    url: str
    note: str


def _read_toml(name: str) -> dict[str, Any]:
    with open(CONFIG / name, "rb") as fh:
        return tomllib.load(fh)


@lru_cache(maxsize=None)
def settings(site_key: str | None = None) -> Settings:
    cfg = _read_toml("site.toml")
    site_key = site_key or os.environ.get("KAPACITET_SITE") or "default"
    s = dict(cfg["site"])
    if site_key != "default":
        alt = cfg.get("alternatives", {}).get(site_key)
        if alt is None:
            raise KeyError(f"Unknown site '{site_key}'. Options: default, {', '.join(cfg.get('alternatives', {}))}")
        s.update(alt)
    site = Site(
        key=site_key,
        name=s["name"],
        center_lat=float(s["center_lat"]),
        center_lon=float(s["center_lon"]),
        substation_osm=str(s.get("substation_osm", "")),
        osm_radius_m=float(s["osm_radius_m"]),
        max_houses=int(s["max_houses"]),
        max_network_distance_m=float(s["max_network_distance_m"]),
        bidding_zone=s["bidding_zone"],
        metering_grid_area=s["metering_grid_area"],
    )
    t = cfg["time"]
    sm = cfg["smhi"]
    pr = cfg["prices"]
    return Settings(
        site=site,
        reference_year=int(t["reference_year"]),
        weather_years=tuple(range(int(t["weather_first_year"]), int(t["weather_last_year"]) + 1)),
        display_utc_offset_h=int(t["display_utc_offset_h"]),
        temperature_station=int(sm["temperature_station"]),
        strang={"ghi": int(sm["strang_ghi"]), "dni": int(sm["strang_dni"]), "dhi": int(sm["strang_dhi"])},
        price_zones=tuple(pr["zones"]),
        price_years=tuple(range(int(pr["first_year"]), int(pr["last_year"]) + 1)),
        national_year=int(cfg["national"]["year"]),
        user_agent=cfg["network"]["http_user_agent"],
        overpass_endpoints=tuple(cfg["network"]["overpass_endpoints"]),
        raw=cfg,
    )


@lru_cache(maxsize=None)
def assumptions() -> dict[str, Assumption]:
    cfg = _read_toml("assumptions.toml")
    out: dict[str, Assumption] = {}
    for key, entry in cfg.items():
        out[key] = Assumption(
            key=key,
            value=entry["value"],
            unit=entry.get("unit", ""),
            tag=entry.get("tag", "team-assumption"),
            label=entry.get("label", key),
            source=entry.get("source", ""),
            url=entry.get("url", ""),
            note=entry.get("note", ""),
        )
    return out


_OVERRIDES: dict[str, Any] = {}


def A(key: str) -> Any:
    """Value of an assumption by key (a sensitivity override wins over the file value)."""
    if key in _OVERRIDES:
        return _OVERRIDES[key]
    return assumptions()[key].value


def set_overrides(values: dict[str, Any] | None) -> None:
    """Replace the active overrides (used by the sensitivity analysis)."""
    _OVERRIDES.clear()
    if values:
        _OVERRIDES.update(values)


def load_dotenv() -> None:
    """Minimal .env reader (KEY=VALUE lines) so secrets never live in code."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())
