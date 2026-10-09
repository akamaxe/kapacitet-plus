<!-- Generated from src/kapacitet/importer/contract.py. Do not edit by hand:
     tests/test_importer.py checks that this file still matches the code. -->

# Network export contract

Four CSV files in one directory, UTF-8, comma separated, with a header row.
Run `python -m kapacitet check <directory>` to get a data-quality report before sending anything.

## `buses.csv`

Every point where something connects: the substation, cable cabinets and service points.

| column | unit | required | meaning | if missing |
| --- | --- | --- | --- | --- |
| `bus_id` | - | yes | Unique identifier, any string or integer. | - |
| `kind` | - | yes | substation, cabinet or service. | - |
| `name` | - | no | Label for the map and the reports. | the identifier |
| `lon` | degrees | no | Longitude, WGS84. | no map, the engine still runs |
| `lat` | degrees | no | Latitude, WGS84. | no map, the engine still runs |

## `lines.csv`

Low-voltage cables and overhead spans, one row per section.

| column | unit | required | meaning | if missing |
| --- | --- | --- | --- | --- |
| `line_id` | - | yes | Unique identifier. | - |
| `from_bus` | - | yes | bus_id at the substation end. | - |
| `to_bus` | - | yes | bus_id at the far end. | - |
| `length_m` | m | yes | Route length of the section. | - |
| `std_type` | - | no | Cable type name, for example NAYY 4x150 SE. | the r/x/c columns, or the site default cable |
| `r_ohm_per_km` | ohm/km | no | Positive-sequence resistance. | looked up from std_type |
| `x_ohm_per_km` | ohm/km | no | Positive-sequence reactance. | looked up from std_type |
| `c_nf_per_km` | nF/km | no | Positive-sequence capacitance. | looked up from std_type |
| `max_i_ka` | kA | no | Continuous current rating. | looked up from std_type |
| `parallel` | - | no | Cables in parallel on this section. | 1 |

## `transformer.csv`

The secondary substation transformer, one row.

| column | unit | required | meaning | if missing |
| --- | --- | --- | --- | --- |
| `lv_bus` | - | yes | bus_id of the low-voltage busbar. | - |
| `sn_mva` | MVA | yes | Rated apparent power. | - |
| `vn_hv_kv` | kV | yes | Rated medium-voltage side voltage. | - |
| `vn_lv_kv` | kV | yes | Rated low-voltage side voltage. | - |
| `vk_percent` | % | no | Short-circuit voltage. | 4.0 |
| `vkr_percent` | % | no | Resistive part of the short-circuit voltage. | 1.325 |
| `vector_group` | - | no | Winding connection, for example Dyn. | Dyn, which is what a Swedish distribution transformer is |
| `tap_pos` | - | no | Off-load tap position. | 0 |

## `customers.csv`

Connection points. One row per metering point, not per building.

| column | unit | required | meaning | if missing |
| --- | --- | --- | --- | --- |
| `customer_id` | - | yes | Unique identifier. | - |
| `bus` | - | yes | bus_id of the service point it hangs from. | - |
| `fuse_a` | A | yes | Main fuse rating per phase. | - |
| `phase` | - | no | a, b or c for a single-phase service, blank for three-phase. | three-phase, which understates voltage unbalance |
| `footprint_m2` | m2 | no | Roof area available for PV. | no rooftop PV potential is estimated for this customer |
| `annual_kwh` | kWh | no | Metered annual consumption. | the Velander estimate from the fuse rating |
