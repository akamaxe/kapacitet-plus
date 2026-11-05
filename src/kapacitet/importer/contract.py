"""The data contract an operator's network export has to meet.

Four CSV files, the smallest set that lets the engine run on someone else's low-voltage
grid, and an optional fifth, `equipment.csv`, with what is already connected behind each
meter. The point of writing it down is that a DSO can produce it from any GIS without
knowing anything about this tool, and that `validate.py` can say exactly what is missing
before anybody promises an answer.

Columns marked optional have a documented fallback. The fallback is always stated in the
report, never applied quietly, because an envelope computed on a guessed cable type is not
an envelope anybody should sign.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Column:
    name: str
    unit: str
    description: str
    required: bool = True
    fallback: str = ""


@dataclass(frozen=True)
class Table:
    name: str
    description: str
    key: str
    columns: list[Column] = field(default_factory=list)

    @property
    def required_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.required]

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


BUSES = Table(
    "buses", "Every point where something connects: the substation, cable cabinets and "
             "service points.", "bus_id",
    [
        Column("bus_id", "-", "Unique identifier, any string or integer."),
        Column("kind", "-", "substation, cabinet or service."),
        Column("name", "-", "Label for the map and the reports.", required=False,
               fallback="the identifier"),
        Column("lon", "degrees", "Longitude, WGS84.", required=False,
               fallback="no map, the engine still runs"),
        Column("lat", "degrees", "Latitude, WGS84.", required=False,
               fallback="no map, the engine still runs"),
    ],
)

LINES = Table(
    "lines", "Low-voltage cables and overhead spans, one row per section.", "line_id",
    [
        Column("line_id", "-", "Unique identifier."),
        Column("from_bus", "-", "bus_id at the substation end."),
        Column("to_bus", "-", "bus_id at the far end."),
        Column("length_m", "m", "Route length of the section."),
        Column("std_type", "-", "Cable type name, for example NAYY 4x150 SE.", required=False,
               fallback="the r/x/c columns, or the site default cable"),
        Column("r_ohm_per_km", "ohm/km", "Positive-sequence resistance.", required=False,
               fallback="looked up from std_type"),
        Column("x_ohm_per_km", "ohm/km", "Positive-sequence reactance.", required=False,
               fallback="looked up from std_type"),
        Column("c_nf_per_km", "nF/km", "Positive-sequence capacitance.", required=False,
               fallback="looked up from std_type"),
        Column("max_i_ka", "kA", "Continuous current rating.", required=False,
               fallback="looked up from std_type"),
        Column("parallel", "-", "Cables in parallel on this section.", required=False,
               fallback="1"),
    ],
)

TRANSFORMER = Table(
    "transformer", "The secondary substation transformer, one row.", "lv_bus",
    [
        Column("lv_bus", "-", "bus_id of the low-voltage busbar."),
        Column("sn_mva", "MVA", "Rated apparent power."),
        Column("vn_hv_kv", "kV", "Rated medium-voltage side voltage."),
        Column("vn_lv_kv", "kV", "Rated low-voltage side voltage."),
        Column("vk_percent", "%", "Short-circuit voltage.", required=False, fallback="4.0"),
        Column("vkr_percent", "%", "Resistive part of the short-circuit voltage.",
               required=False, fallback="1.325"),
        Column("vector_group", "-", "Winding connection, for example Dyn.", required=False,
               fallback="Dyn, which is what a Swedish distribution transformer is"),
        Column("tap_pos", "-", "Off-load tap position.", required=False, fallback="0"),
    ],
)

CUSTOMERS = Table(
    "customers", "Connection points. One row per metering point, not per building.",
    "customer_id",
    [
        Column("customer_id", "-", "Unique identifier."),
        Column("bus", "-", "bus_id of the service point it hangs from."),
        Column("fuse_a", "A", "Main fuse rating per phase."),
        Column("phase", "-", "a, b or c for a single-phase service, blank for three-phase.",
               required=False,
               fallback="three-phase, which understates voltage unbalance"),
        Column("footprint_m2", "m2", "Roof area available for PV.", required=False,
               fallback="no rooftop PV potential is estimated for this customer"),
        Column("annual_kwh", "kWh", "Metered annual consumption.", required=False,
               fallback="nothing to compare: the engine's household profiles are synthetic either way, "
                        "and a given value is only compared with them and reported"),
    ],
)

EQUIPMENT = Table(
    "equipment", "Optional. What is already connected behind each meter: solar PV, batteries, EV "
                 "chargers and heat pumps, one row per customer that has any. A customer without a "
                 "row has none of them on record.", "customer_id",
    [
        Column("customer_id", "-", "customer_id in customers.csv."),
        Column("pv_kwp", "kWp", "Installed PV, DC peak.", required=False,
               fallback="no PV is counted for this customer"),
        Column("pv_kw_ac", "kW", "PV inverter AC rating.", required=False,
               fallback="pv_kwp divided by the site's DC/AC ratio"),
        Column("pv_phase", "-", "a, b or c for a single-phase inverter, abc for a three-phase one.",
               required=False, fallback="the phase allocation rule of config/assumptions.toml"),
        Column("battery_kwh", "kWh", "Usable battery capacity.", required=False,
               fallback="no battery is counted"),
        Column("battery_kw", "kW", "Battery power rating.", required=False,
               fallback="no battery is counted"),
        Column("ev_charger_kw", "kW", "EV charger rating.", required=False,
               fallback="no charger is counted"),
        Column("ev_phases", "-", "1 or 3: the phases the charger uses.", required=False,
               fallback="3, which is what a wallbox at a Swedish villa normally is"),
        Column("heat_pump", "-", "yes or no.", required=False,
               fallback="not known, so the study's share of heat pumps applies"),
        Column("heat_pump_kw", "kW", "Heat pump electrical rating.", required=False,
               fallback="the heat pump model's own sizing"),
        Column("source", "-", "Where the record comes from, for example the installer's notification.",
               required=False, fallback="the record's source is reported as not stated"),
        Column("connected_on", "date", "When it was connected, YYYY-MM-DD.", required=False,
               fallback="taken as connected before the study"),
    ],
)

TABLES = (BUSES, LINES, TRANSFORMER, CUSTOMERS)          # every export has these
OPTIONAL_TABLES = (EQUIPMENT,)                            # checked and used when present
ALL_TABLES = TABLES + OPTIONAL_TABLES
BY_NAME = {t.name: t for t in ALL_TABLES}
# the labels equipment.csv may use for an inverter's phase, and for yes and no
PV_PHASE_LABELS = {"a": "a", "b": "b", "c": "c", "l1": "a", "l2": "b", "l3": "c",
                   "abc": "abc", "3": "abc", "l1l2l3": "abc", "three": "abc"}
YES = frozenset({"yes", "y", "true", "1", "ja", "j"})
NO = frozenset({"no", "n", "false", "0", "nej"})


def as_markdown() -> str:
    """The contract as a document to hand to an operator."""
    out = ["# Network export contract", "",
           "Four CSV files in one directory, UTF-8, comma separated, with a header row, and an "
           "optional fifth, `equipment.csv`, which is checked and used when it is there.",
           "Run `python -m kapacitet check <directory>` to get a data-quality report before "
           "sending anything.", ""]
    for table in ALL_TABLES:
        optional = " (optional)" if table in OPTIONAL_TABLES else ""
        out += [f"## `{table.name}.csv`{optional}", "", table.description, "",
                "| column | unit | required | meaning | if missing |",
                "| --- | --- | --- | --- | --- |"]
        for c in table.columns:
            out.append(f"| `{c.name}` | {c.unit} | {'yes' if c.required else 'no'} | "
                       f"{c.description} | {c.fallback or '-'} |")
        out.append("")
    return "\n".join(out)
