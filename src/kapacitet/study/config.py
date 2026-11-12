"""`study.toml`: what a DSO's study is, read and checked before anything is computed.

Every section has defaults (the owner's study defaults), so a study.toml of one line is valid;
an unknown section or key is an error rather than a setting silently ignored. The sections:

    [dso]          name, method_version (the method the study is pinned to)
    [site]         where the weather comes from: lat/lon with the SMHI temperature station, the
                   STRÅNG point (defaults to lat/lon), the bidding zone and the eSett metering grid
                   area. Without lat/lon the study has no place of its own: `study fetch` refuses,
                   and its stations reuse the reference network's weather, labelled synthetic
    [time]         reference_year and the weather years, when they differ from config/site.toml
    [method]       percentile = 50, budget = "aggregate" | "installation", consumption_device = "ev" |
                   "heat_pump", approval_reference (the DSO's approval of the envelope method: without
                   it no envelope capacity is published)
    [mv]           band_low_pu, band_high_pu, source: generation is computed at the high end and
                   consumption at the low end
    [disclosure]   level = "station" | "grid" | "zone", grid_m, min_customers = 5, hidden stations,
                   round_kw and round_below_customers: the figures of a cell with fewer customers than
                   that are rounded down to a multiple of round_kw, so that two successive
                   publications say less about one household (0 turns it off)
    [validation]   the DSO's acceptance criteria for a station's model against its measurements
                   (study/validation.py): voltage_p95_pu, transformer_p95_pct, min_metered_share and
                   source. Without one, a station is compared with its measurements and never called
                   validated; with them, a station outside a tolerance is not published
    [cases]        processing_days, protection = "dppower" | "kapacitet"
    [output]       csv_dialect = "sv-excel" (beside the machine copy, always written), retention_days
    [costs]        the DSO's own contract prices for `study options`: transformer_swap_sek by the new
                   unit's kVA, curtailment_sek_per_kwh, price_level_year, deferral_years and source.
                   A price left out is "not entered", never guessed (Ei's norm values are shown only
                   as a labelled reference)
    [devplan]      the scenario `study devplan` checks beside today's grid: scenario_year (2030),
                   pv_share, ev_share, hp_share of the station's customers in that year (existing
                   ones counted) and source. Without a share there is no scenario ("not entered")
    [scenarios]    the growth `study scenarios` runs each station forward with, from the quarter's
                   year to `year` (2030): pv_per_year, ev_per_year, hp_per_year, battery_per_year,
                   each the share of the station's customers who add the device in a year, and
                   source. A rate left out is the default of config/assumptions.toml, labelled so
    [publication]  cadence = "quarterly" | "monthly" (only the run id the commands take when none is
                   given: 2026Q4 or 2026-11), map_tiles (an https://…/{z}/{x}/{y}.png raster template the
                   embedded map draws under its cells) and map_attribution (required with it). Without
                   map_tiles the embed makes no request outside its own folder
    [assumptions]  key = {value, unit, source}: an override of config/assumptions.toml. The key must
                   exist there and take effect in a station (NOT_IN_A_STUDY lists those that do
                   not), the unit must be the one it has, and the source must be stated.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

# The version of the station method this code computes. A study pins one in [dso]; a study pinned
# to another version is computed by the code that implements it, never silently by this one.
METHOD_VERSION = "1.2.0"      # 1.1.0: the phases step (ingest/phase_id.py) is part of a station build
                              # 1.2.0: the three-phase check in capacity.json and the publication

# Assumptions an override of which would change nothing in a station, so it is refused
# (tests/test_study_config.py keeps both lists in step with the code): those read only by the
# network builder on OpenStreetMap (grid/builder.build), which a study's stations never run since
# their networks are imported, and those no code reads, which document a value fixed in the code.
BUILDER_ONLY = ("cabinet_max_services", "cabinet_reach_m", "cable_design_utilisation", "feeder_max_customers",
                "fuse_thresholds_m2", "main_cable_type", "trafo_design_margin", "trafo_tap_pos", "trafo_types")
DOCUMENTED_ONLY = ("hp_cop_model", "pv_system_losses_pct")
NOT_IN_A_STUDY = BUILDER_ONLY + DOCUMENTED_ONLY


class StudyConfigError(ValueError):
    """study.toml cannot be used: the message lists every problem, one per line."""


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Dso(_Section):
    name: str = ""
    method_version: str = METHOD_VERSION


class Site(_Section):
    key: str = Field("study", pattern=r"^[a-z0-9][a-z0-9_-]{0,40}$")
    name: str = ""
    lat: float | None = Field(None, ge=55.0, le=69.5)
    lon: float | None = Field(None, ge=10.5, le=24.5)
    temperature_station: int | None = Field(None, gt=0)
    strang_lat: float | None = Field(None, ge=55.0, le=69.5)
    strang_lon: float | None = Field(None, ge=10.5, le=24.5)
    zone: Literal["SE1", "SE2", "SE3", "SE4"] | None = None
    metering_grid_area: str | None = None

    @model_validator(mode="after")
    def _pairs(self):
        for a, b in (("lat", "lon"), ("strang_lat", "strang_lon")):
            if (getattr(self, a) is None) != (getattr(self, b) is None):
                raise ValueError(f"[site] {a} and {b} go together")
        if self.lat is not None and self.temperature_station is None:
            # otherwise the reference site's station would stand in, with nothing to say so
            raise ValueError("[site] lat and lon need the temperature_station of that place")
        if self.strang_lat is not None and self.lat is None:
            raise ValueError("[site] strang_lat and strang_lon need the study's lat and lon")
        return self

    @property
    def given(self) -> bool:
        """The study names a place of its own; without one it fetches no weather."""
        return self.lat is not None


class Time(_Section):
    reference_year: int | None = Field(None, ge=2000, le=2100)
    weather_first_year: int | None = Field(None, ge=2000, le=2100)
    weather_last_year: int | None = Field(None, ge=2000, le=2100)

    @model_validator(mode="after")
    def _order(self):
        first, last, ref = self.weather_first_year, self.weather_last_year, self.reference_year
        if (first is None) != (last is None):
            raise ValueError("[time] weather_first_year and weather_last_year go together")
        if first is not None and first > last:
            raise ValueError("[time] weather_first_year is after weather_last_year")
        if ref is not None and first is not None and not first <= ref <= last:
            raise ValueError("[time] reference_year is not one of the weather years")
        return self


class Method(_Section):
    percentile: float = Field(50.0, ge=0.0, le=100.0)
    budget: Literal["aggregate", "installation"] = "aggregate"
    consumption_device: Literal["ev", "heat_pump"] = "ev"
    approval_reference: str = ""


class Mv(_Section):
    band_low_pu: float = Field(0.98, ge=0.9, le=1.1)
    band_high_pu: float = Field(1.06, ge=0.9, le=1.1)
    source: str = "planning band, not measured"

    @model_validator(mode="after")
    def _band(self):
        if self.band_low_pu > self.band_high_pu:
            raise ValueError("[mv] band_low_pu is above band_high_pu")
        return self


class Disclosure(_Section):
    level: Literal["station", "grid", "zone"] = "station"
    grid_m: int = Field(1000, gt=0)
    min_customers: int = Field(5, ge=1)
    hidden: list[str] = Field(default_factory=list)
    # The figures of a cell with fewer than round_below_customers customers are rounded down to a
    # multiple of round_kw (study/publish.py): k customers per cell says nothing about what two
    # successive publications show together, and in a small cell a figure that drops can point at
    # the one household that connected. Both values are the team's default, to be set with the
    # DSO's own disclosure analysis; round_kw = 0 turns the rounding off.
    round_kw: float = Field(10.0, ge=0.0)
    round_below_customers: int = Field(20, ge=0)

    def step_kw(self, customers: int | None) -> float | None:
        """The step a cell of `customers` customers is rounded down to, or None when it is not."""
        if self.round_kw <= 0 or customers is None or int(customers) >= self.round_below_customers:
            return None
        return float(self.round_kw)


class Validation(_Section):
    """The DSO's acceptance criteria for a station's model against its own measurements. Each is
    the DSO's to set: none has a default, and without any a station is never called validated."""
    # the 95th percentile of |model - measured| over the metered customers' voltages (pu), replayed
    # at the measured MV voltage
    voltage_p95_pu: float | None = Field(None, gt=0.0, le=0.5)
    # the same for the transformer's loading, in percentage points
    transformer_p95_pct: float | None = Field(None, gt=0.0, le=200.0)
    # the least share of the station's customers that must have meter readings
    min_metered_share: float | None = Field(None, gt=0.0, le=1.0)
    source: str = ""

    @property
    def entered(self) -> bool:
        return any(v is not None for v in (self.voltage_p95_pu, self.transformer_p95_pct, self.min_metered_share))

    @model_validator(mode="after")
    def _sourced(self):
        if self.entered and not self.source.strip():
            raise ValueError("[validation] a tolerance needs its source (who set it, and when)")
        return self


class Cases(_Section):
    processing_days: int | None = Field(None, gt=0)
    protection: Literal["dppower", "kapacitet"] = "dppower"


class Output(_Section):
    csv_dialect: Literal["sv-excel", "rfc4180"] = "sv-excel"
    retention_days: int | None = Field(None, gt=0)


class Publication(_Section):
    cadence: Literal["quarterly", "monthly"] = "quarterly"
    map_tiles: str = ""
    map_attribution: str = ""

    @model_validator(mode="after")
    def _tiles(self):
        tiles, attribution = self.map_tiles.strip(), self.map_attribution.strip()
        if not tiles:
            if attribution:
                raise ValueError("map_attribution is set without map_tiles")
            return self
        if not tiles.startswith("https://") or any(c in tiles for c in " \"'<>"):
            raise ValueError(f"map_tiles must be an https:// address (a template such as "
                             f"https://tiles.example/{{z}}/{{x}}/{{y}}.png), not {tiles!r}")
        missing = [p for p in ("{z}", "{x}", "{y}") if p not in tiles]
        if missing:
            raise ValueError(f"map_tiles has no {', '.join(missing)}: the template names the tile as {{z}}/{{x}}/{{y}}")
        if not attribution:
            raise ValueError("map_tiles needs map_attribution: the tiles' licence names who to credit, and the "
                             "map shows it")
        return self


class Costs(_Section):
    transformer_swap_sek: dict[str, float] = Field(default_factory=dict)
    curtailment_sek_per_kwh: float | None = Field(None, ge=0)
    price_level_year: int | None = Field(None, ge=2000, le=2100)
    deferral_years: int | None = Field(None, ge=1, le=40)
    source: str = ""

    @field_validator("transformer_swap_sek")
    @classmethod
    def _kva(cls, v: dict[str, float]) -> dict[str, float]:
        for kva, sek in v.items():
            if not kva.isdigit() or int(kva) <= 0:
                raise ValueError(f"[costs] transformer_swap_sek is keyed by the new unit's kVA, not {kva!r}")
            if sek < 0:
                raise ValueError(f"[costs] transformer_swap_sek {kva} is negative")
        return {str(int(k)): float(x) for k, x in v.items()}

    @model_validator(mode="after")
    def _sourced(self):
        if (self.transformer_swap_sek or self.curtailment_sek_per_kwh is not None) and not self.source.strip():
            raise ValueError("[costs] a price needs its source (the DSO's contract, its date)")
        return self

    def swap_sek(self, sn_mva: float | None) -> float | None:
        """The DSO's price for a swap to a unit of `sn_mva`, or None when it is not entered."""
        if sn_mva is None:
            return None
        return self.transformer_swap_sek.get(str(int(round(float(sn_mva) * 1000))))


class Devplan(_Section):
    scenario_year: int = Field(2030, ge=2000, le=2100)
    pv_share: float | None = Field(None, ge=0.0, le=1.0)
    ev_share: float | None = Field(None, ge=0.0, le=1.0)
    hp_share: float | None = Field(None, ge=0.0, le=1.0)
    source: str = ""

    @model_validator(mode="after")
    def _sourced(self):
        if self.entered() and not self.source.strip():
            raise ValueError("[devplan] a scenario's shares need their source (the DSO's forecast, its date)")
        return self

    def entered(self) -> bool:
        """At least one share is entered: without one there is no scenario, never a guessed one."""
        return any(x is not None for x in (self.pv_share, self.ev_share, self.hp_share))


class Scenarios(_Section):
    year: int = Field(2030, ge=2000, le=2100)
    pv_per_year: float | None = Field(None, ge=0.0, le=1.0)
    ev_per_year: float | None = Field(None, ge=0.0, le=1.0)
    hp_per_year: float | None = Field(None, ge=0.0, le=1.0)
    battery_per_year: float | None = Field(None, ge=0.0, le=1.0)
    source: str = ""

    @model_validator(mode="after")
    def _sourced(self):
        if self.entered() and not self.source.strip():
            raise ValueError("[scenarios] growth rates need their source (the DSO's forecast, its date)")
        return self

    def entered(self) -> bool:
        """At least one rate is the DSO's own; the others are config/assumptions.toml's defaults."""
        return any(x is not None for x in (self.pv_per_year, self.ev_per_year, self.hp_per_year,
                                           self.battery_per_year))


class Override(_Section):
    value: Any
    unit: str
    source: str

    @field_validator("source")
    @classmethod
    def _sourced(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("an override needs its source")
        return v


class StudyConfig(_Section):
    dso: Dso = Field(default_factory=Dso)
    site: Site = Field(default_factory=Site)
    time: Time = Field(default_factory=Time)
    method: Method = Field(default_factory=Method)
    mv: Mv = Field(default_factory=Mv)
    disclosure: Disclosure = Field(default_factory=Disclosure)
    validation: Validation = Field(default_factory=Validation)
    cases: Cases = Field(default_factory=Cases)
    output: Output = Field(default_factory=Output)
    costs: Costs = Field(default_factory=Costs)
    devplan: Devplan = Field(default_factory=Devplan)
    scenarios: Scenarios = Field(default_factory=Scenarios)
    publication: Publication = Field(default_factory=Publication)
    assumptions: dict[str, Override] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _known_assumptions(self):
        problems = check_overrides(self.assumptions)
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def overrides(self) -> dict[str, Any]:
        """The [assumptions] values, as config.set_overrides takes them."""
        return {k: o.value for k, o in self.assumptions.items()}

    def computation(self) -> dict:
        """What a station's computation reads: its digest decides whether the station is built again
        (a change of the DSO's name, [disclosure], [cases], [output] or [publication] rebuilds nothing)."""
        out = self.model_dump(include={"site", "time", "method", "mv", "assumptions"}, mode="json")
        out["method_version"] = self.dso.method_version
        return out


def check_overrides(overrides: dict[str, Override]) -> list[str]:
    """What is wrong with the [assumptions] overrides: an unknown key, another unit, another type."""
    from ..config import assumptions

    known = assumptions()
    out = []
    for key, o in overrides.items():
        a = known.get(key)
        if a is None:
            out.append(f"[assumptions] {key} is not an assumption of config/assumptions.toml")
            continue
        if key in BUILDER_ONLY:
            out.append(f"[assumptions] {key} is read only by the network builder on OpenStreetMap, which a "
                       "study's imported networks never run: an override would change nothing")
            continue
        if key in DOCUMENTED_ONLY:
            out.append(f"[assumptions] {key} documents a value fixed in the code, which reads no other: "
                       "an override would change nothing")
            continue
        if o.unit != a.unit:
            out.append(f"[assumptions] {key} has the unit {a.unit!r} there, not {o.unit!r}")
        if not _same_kind(o.value, a.value):
            out.append(f"[assumptions] {key} takes a value like {a.value!r}, not {o.value!r}")
    return out


def _same_kind(new: Any, old: Any) -> bool:
    if isinstance(old, bool) or isinstance(new, bool):
        return isinstance(old, bool) and isinstance(new, bool)
    if isinstance(old, (int, float)):
        return isinstance(new, (int, float))
    if isinstance(old, list):
        return isinstance(new, list) and bool(new) and (not old or all(_same_kind(n, old[0]) for n in new))
    return type(new) is type(old)


def parse(data: dict) -> StudyConfig:
    try:
        return StudyConfig.model_validate(data)
    except ValidationError as err:
        lines = []
        for e in err.errors():
            where = ".".join(str(p) for p in e["loc"])
            msg = e["msg"].removeprefix("Value error, ")
            lines.append(f"{where}: {msg}" if where else msg)
        raise StudyConfigError("study.toml: " + "\n  ".join(lines)) from None


def load(path: str | Path) -> StudyConfig:
    """Read and check a study.toml."""
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise StudyConfigError(f"no study.toml in {path.parent}") from None
    except tomllib.TOMLDecodeError as err:
        raise StudyConfigError(f"study.toml is not valid TOML: {err}") from None
    return parse(data)


def template(name: str = "", site: dict | None = None) -> str:
    """The study.toml `study init` writes: the defaults, each one stated and explained."""
    site = site or {}
    lines = [
        "# A Kapacitet+ study: one DSO's secondary substations, computed with one method.",
        "# Every value below is the default: a line left out keeps it, a line changed overrides it.",
        "#",
        "# What the DSO hands over goes in inputs/ (docs/study-inputs.md has every column). CSV files may",
        "# use , or ; and a decimal point or comma; times are ISO 8601 with their UTC offset",
        "# (2025-03-30T03:00:00+02:00 or Z): a time without one is refused in meters/, mv/ and dppower/.",
        "#   network/contract/<station>/  buses, lines, transformer, customers (.csv), one folder per station",
        "#   network/cgmes/               or CGMES 2.4.15 files, with cim_customers.csv beside them:",
        "#                                energyconsumer_mrid, customer_id, fuse_a [, phase, footprint_m2, annual_kwh]",
        "#   stations.csv                 station_id, name, trafo_mrid, feeder_id, hidden, zone",
        "#   equipment.csv                what is already connected, per customer_id",
        "#   meters/*.csv                 customer_id or meter_id, start, kwh_import [, kwh_export, v_l1..v_l3]",
        "#   upstream.csv                 level (feeder|substation|regional), id, parent_id, gen_headroom_kw,",
        "#                                load_headroom_kw, source, as_of: a blank headroom is not entered",
        "#   mv/<station>.csv             start, vm_pu [, trafo_p_kw, trafo_q_kvar]: the measured MV voltage",
        "#                                [, v_l1..v_l3]: the transformer secondary meter's phase voltages",
        "#   dppower/snapshot.csv         start, mrid, kind (bus|line|transformer), vm_pu, loading_pct",
        "#   dppower/static.csv           customer_id, gen_kw, load_kw: the DSO's own static results",
        "# A run (a quarter as 2026Q4, or a month as 2026-11): study collect, review, sign --by \"name, role\",",
        "# publish (runs/<run>/).",
        "",
        "[dso]",
        f"name = {_toml(name)}",
        f'method_version = "{METHOD_VERSION}"     # the method the study is pinned to',
        "",
        "[site]",
        "# Where the weather comes from: lat/lon and the SMHI metobs station with hourly air temperature",
        "# there (temperature_station). Without them the study has no place of its own: `study fetch`",
        "# refuses, and the stations reuse the reference network's weather, labelled synthetic.",
    ]
    examples = (("name", '"the area"', ""), ("lat", "59.32", ""), ("lon", "17.96", ""),
                ("temperature_station", "97200", "needed with lat/lon"),
                ("strang_lat", "59.32", "the STRÅNG point, when it is not lat/lon"), ("strang_lon", "17.96", ""),
                ("zone", '"SE3"', "the bidding zone"),
                ("metering_grid_area", '"STH"', "the eSett metering grid area"))
    for key, example, why in examples:
        if site.get(key) is not None:
            lines.append(f"{key} = {_toml(site[key])}")
        else:
            line = f"# {key} = {example}"
            lines.append(f"{line.ljust(32)}# {why}" if why else line)
    lines += [
        "",
        "[time]",
        "# The years of config/site.toml unless set; the reference year is one of the weather years.",
        "# reference_year = 2025",
        "# weather_first_year = 2015",
        "# weather_last_year = 2025",
        "",
        "[method]",
        "percentile = 50                 # of the adoption orders, per station",
        'budget = "aggregate"            # or "installation": the curtailment budget\'s basis',
        'consumption_device = "ev"       # or "heat_pump": what a consumption capacity is for',
        '# approval_reference = "..."     # the DSO\'s approval of the envelope method; without it no',
        '#                                # envelope capacity is published',
        "",
        "[mv]",
        "band_low_pu = 0.98              # consumption is computed here",
        "band_high_pu = 1.06             # generation is computed here",
        'source = "planning band, not measured"',
        "",
        "[disclosure]",
        'level = "station"               # or "grid" (squares of grid_m) or "zone"',
        "grid_m = 1000",
        "min_customers = 5               # cells with fewer customers are merged upward",
        "hidden = []                     # station ids never published",
        "round_kw = 10.0                 # the figures of a cell with fewer than round_below_customers",
        "round_below_customers = 20      # customers are rounded down to a multiple of round_kw (0: off).",
        "                                # The team's default: set both with your own disclosure analysis",
        "",
        "[validation]",
        "# Your own acceptance criteria for a station's model against its measurements (`study",
        "# validate`). None has a default: without one a station is compared and reported, never called",
        "# validated; with them, a station outside a tolerance is not published. The figures below only",
        "# show the form.",
        "# voltage_p95_pu = 0.01          # p95 of |model - measured| customer voltage, at the measured MV",
        "# transformer_p95_pct = 5.0      # the same for the transformer's loading, in percentage points",
        "# min_metered_share = 0.8        # the least share of the station's customers with readings",
        '# source = "who set them, and when"',
        "",
        "[cases]",
        "# processing_days = 30",
        'protection = "dppower"          # short circuit and selectivity stay in dpPower',
        "",
        "[output]",
        'csv_dialect = "sv-excel"        # ; and decimal comma, UTF-8 with BOM, beside an RFC 4180 copy',
        "# retention_days = 365          # off unless set",
        "",
        "[costs]",
        "# The DSO's own contract prices, which `study options` reads; a price left out is \"not",
        "# entered\" and no option that needs it is recommended. Ei's norm values are shown beside them",
        "# only as a labelled reference.",
        '# transformer_swap_sek = { "630" = 0, "800" = 0 }   # the whole swap, by the new unit\'s kVA',
        "# curtailment_sek_per_kwh = 0    # what curtailed energy costs the DSO (its compensation)",
        "# price_level_year = 2026",
        "# deferral_years = 4             # the horizon of waiting (one regulatory period by default)",
        '# source = "the contract and its date"',
        "",
        "[devplan]",
        "# The DSO's own scenario that `study devplan` checks beside today's grid: the share of each",
        "# station's customers with each device in scenario_year, existing ones counted. A share left",
        "# out keeps today's; with none entered there is no scenario (\"not entered\").",
        "scenario_year = 2030",
        "# pv_share = 0.0",
        "# ev_share = 0.0",
        "# hp_share = 0.0",
        '# source = "the forecast and its date"',
        "",
        "[scenarios]",
        "# The growth `study scenarios` runs each station forward with, from the quarter's year to",
        "# `year`: the share of the station's customers who add each device in a year. A rate left",
        "# out is the default of config/assumptions.toml (scenario_*_growth_per_year), labelled so.",
        "year = 2030",
        "# pv_per_year = 0.0",
        "# ev_per_year = 0.0",
        "# hp_per_year = 0.0",
        "# battery_per_year = 0.0",
        '# source = "the forecast and its date"',
        "",
        "[publication]",
        'cadence = "quarterly"           # or "monthly": the run id the commands take when none is given',
        "# The embedded map (runs/<run>/publication/embed/) draws its cells on nothing unless a raster",
        "# tile layer is named here; without one it makes no request outside its own folder.",
        '# map_tiles = "https://tiles.example/{z}/{x}/{y}.png"',
        '# map_attribution = "© the tile provider"   # required with map_tiles',
        "",
        "[assumptions]",
        "# An override of config/assumptions.toml, with the unit it has there and its source:",
        '# household_kwh_per_year = { value = 6000, unit = "kWh/yr", source = "the DSO\'s metering, 2024" }',
        "",
    ]
    return "\n".join(lines)


def _toml(value: Any) -> str:
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, bool):
        return "true" if value else "false"
    return repr(value)
