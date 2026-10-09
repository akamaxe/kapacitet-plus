"""The data contract an operator's network export has to meet.

Four CSV files, the smallest set that lets the engine run on someone else's low-voltage
grid. The point of writing it down is that a DSO can produce it from any GIS without
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
               fallback="the Velander estimate from the fuse rating"),
    ],
)

TABLES = (BUSES, LINES, TRANSFORMER, CUSTOMERS)
BY_NAME = {t.name: t for t in TABLES}


def as_markdown() -> str:
    """The contract as a document to hand to an operator."""
    out = ["# Network export contract", "",
           "Four CSV files in one directory, UTF-8, comma separated, with a header row.",
           "Run `python -m kapacitet check <directory>` to get a data-quality report before "
           "sending anything.", ""]
    for table in TABLES:
        out += [f"## `{table.name}.csv`", "", table.description, "",
                "| column | unit | required | meaning | if missing |",
                "| --- | --- | --- | --- | --- |"]
        for c in table.columns:
            out.append(f"| `{c.name}` | {c.unit} | {'yes' if c.required else 'no'} | "
                       f"{c.description} | {c.fallback or '-'} |")
        out.append("")
    return "\n".join(out)
