"""Loading of config/site.toml and config/assumptions.toml, and of a study's study.toml over them.

When KAPACITET_STUDY names a study folder (a station's build process runs with it), its study.toml
is merged in: [site] and [time] over config/site.toml in `settings()`, and the validated
[assumptions] as the study layer of `A()`, under the sensitivity overrides of `set_overrides()`.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .paths import CONFIG, ROOT

if TYPE_CHECKING:
    from .study.config import StudyConfig


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
    # the STRÅNG point, when a study sets one apart from the centre
    strang_lat: float | None = None
    strang_lon: float | None = None

    @property
    def strang_point(self) -> tuple[float, float]:
        """(lat, lon) the irradiance is fetched for."""
        if self.strang_lat is not None and self.strang_lon is not None:
            return self.strang_lat, self.strang_lon
        return self.center_lat, self.center_lon


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
    study: str = ""          # the study folder whose study.toml was merged in, if any


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
    label_sv: str = ""
    note_sv: str = ""


def _read_toml(name: str) -> dict[str, Any]:
    with open(CONFIG / name, "rb") as fh:
        return tomllib.load(fh)


@lru_cache(maxsize=None)
def study_config() -> StudyConfig | None:
    """The study.toml of the study this process works for (KAPACITET_STUDY), checked; else None."""
    root = os.environ.get("KAPACITET_STUDY")
    if not root:
        return None
    from .study.config import load

    return load(Path(root) / "study.toml")


@lru_cache(maxsize=None)
def settings(site_key: str | None = None) -> Settings:
    study = study_config()
    return _settings(site_key or os.environ.get("KAPACITET_SITE") or "default", study,
                     os.environ.get("KAPACITET_STUDY", "") if study is not None else "")


def study_settings(study: StudyConfig, folder: str | Path = "") -> Settings:
    """The settings a station process of `study` runs with (what settings() returns there, where
    KAPACITET_STUDY is set and KAPACITET_SITE is not), read from this process without either."""
    return _settings("default", study, str(folder))


def _settings(site_key: str, study: StudyConfig | None, study_dir: str) -> Settings:
    cfg = _read_toml("site.toml")
    s = dict(cfg["site"])
    if site_key != "default":
        alt = cfg.get("alternatives", {}).get(site_key)
        if alt is None:
            raise KeyError(f"Unknown site '{site_key}'. Options: default, {', '.join(cfg.get('alternatives', {}))}")
        s.update(alt)
    t = dict(cfg["time"])
    sm = dict(cfg["smhi"])
    if study is not None:
        site_key = _merge_study(study, s, t, sm)
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
        strang_lat=s.get("strang_lat"),
        strang_lon=s.get("strang_lon"),
    )
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
        study=study_dir,
    )


def _merge_study(study: StudyConfig, s: dict, t: dict, sm: dict) -> str:
    """Lay a study's [site] and [time] over site.toml's tables (in place); returns the site key.

    The key is the study's, so its downloads never mix with the reference site's, even when the
    study keeps config/site.toml's place."""
    st = study.site
    s["name"] = st.name or study.dso.name or s["name"]
    if st.given:
        s["center_lat"], s["center_lon"] = st.lat, st.lon
    if st.strang_lat is not None:
        s["strang_lat"], s["strang_lon"] = st.strang_lat, st.strang_lon
    if st.zone:
        s["bidding_zone"] = st.zone
    if st.metering_grid_area:
        s["metering_grid_area"] = st.metering_grid_area
    if st.temperature_station:
        sm["temperature_station"] = st.temperature_station
    tm = study.time
    for key in ("reference_year", "weather_first_year", "weather_last_year"):
        if getattr(tm, key) is not None:
            t[key] = getattr(tm, key)
    if not int(t["weather_first_year"]) <= int(t["reference_year"]) <= int(t["weather_last_year"]):
        from .study.config import StudyConfigError

        raise StudyConfigError("study.toml: [time] reference_year is not one of the weather years")
    return st.key


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
            label_sv=entry.get("label_sv", ""),
            note_sv=entry.get("note_sv", ""),
        )
    return out


_OVERRIDES: dict[str, Any] = {}          # the sensitivity layer (the tornado's, case by case)
_STUDY_LAYER: dict[str, Any] | None = None  # a study's [assumptions]; None until first read


def _study_layer() -> dict[str, Any]:
    global _STUDY_LAYER
    if _STUDY_LAYER is None:
        study = study_config()
        _STUDY_LAYER = study.overrides() if study is not None else {}
    return _STUDY_LAYER


def A(key: str) -> Any:
    """Value of an assumption by key: a sensitivity override wins over the study's, which wins
    over the file value. Every process of a study reads the study's own from KAPACITET_STUDY, so
    a worker process the engine starts applies them as its parent does."""
    if key in _OVERRIDES:
        return _OVERRIDES[key]
    layer = _STUDY_LAYER if _STUDY_LAYER is not None else _study_layer()
    if key in layer:
        return layer[key]
    return assumptions()[key].value


def set_overrides(values: dict[str, Any] | None, layer: str = "sensitivity") -> None:
    """Replace the active overrides of one layer.

    "sensitivity" (the default) is the tornado's: one input varied at a time, cleared after each
    case. "study" is a study's [assumptions], validated by study.config (an unknown key, another
    unit or a missing source fails there); here an unknown key fails too. Clearing the
    sensitivity layer never clears the study's.
    """
    global _STUDY_LAYER
    if layer == "sensitivity":
        _OVERRIDES.clear()
        if values:
            _OVERRIDES.update(values)
        return
    if layer != "study":
        raise ValueError(f"unknown override layer {layer!r}")
    unknown = sorted(set(values or {}) - set(assumptions()))
    if unknown:
        raise KeyError(f"not assumptions of config/assumptions.toml: {', '.join(unknown)}")
    _STUDY_LAYER = dict(values or {})


def reload() -> None:
    """Forget what was read from the environment: the settings, the study and its overrides."""
    global _STUDY_LAYER
    settings.cache_clear()
    study_config.cache_clear()
    _STUDY_LAYER = None


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
