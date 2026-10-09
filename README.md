# Kapacitet+

A digital twin of one Swedish low-voltage neighbourhood that puts two connection studies side
by side: the conservative worst-case **hosting capacity** used today, and the capacity unlocked
by **Dynamic Operating Envelopes** (DOE) — time-varying export limits that are verified hour by
hour with a full nonlinear power flow.

The demo neighbourhood is **Smedslätten, Bromma (Stockholm, SE3)**: 120 real villas around a
substation that OpenStreetMap maps at Smedslättsvägen 71A. Buildings, streets, weather, prices
and national volumes are real data; the cables, the transformer and household behaviour are
modelled, and the interface labels which is which on every screen.

Course project for **KTH EI2600 — Innovations and Entrepreneurship in Electric Power
Engineering**.

## What it shows

Median over 20 seeded adoption orders and 11 weather years (2015–2025), transformer 0.4 MVA:

| Method | Solar connected | Houses |
|---|---|---|
| Static study, worst case | **413 kW AC** (P10–P90 405–415) | 50 |
| Static study + fixed export cap at 3 % curtailment | 416 kW | 51 |
| Real hours, no control (every hour of 11 years within limits) | 609 kW | 74 |
| **Dynamic envelopes at 3 % curtailment** | **787 kW** (782–800) | 96 |

So the same grid carries **1.9×** more solar with envelopes, and the customers who join beyond
the static limit lose about **2–3 %** of their annual solar energy. Without any control, the
same 94 systems would push the transformer above its rating in **148 hours** of 2025.

The 3 % threshold is an anchor, not an invention: German EnWG §11(2) *Spitzenkappung* lets a
DSO plan with up to 3 % of the annual energy of each installation curtailed. The full
curtailment-versus-capacity curve is on screen, not just that point.

Three findings worth a sentence each:

- **The transformer is the binding limit here, not voltage.** Even with no control, voltages
  stay at 1.048 pu (limit 1.10). The sensitivity chart shows the result moves with the
  transformer rating and the inverter sizing, and barely with the voltage assumptions.
- **Envelopes beat reinforcement on the same asset.** 0.4 MVA with envelopes (787 kW) carries
  more than a 0.63 MVA transformer studied statically (651 kW).
- **Fairness is free in this network.** Max-min, pro-rata, proportional-fair and max-total all
  curtail the same total energy (1.98 % in the showcase year) because one shared constraint
  binds; they only differ in *who* is curtailed (Jain index 0.59 → 0.92).

Every number above is computed by this repository — nothing in the interface is typed in by hand.

## Quickstart

Requires Python 3.12 and Node 20+ for the interface. The engine needs a combination of numpy,
scipy, pandas and pandapower that all four support, so it lives in its own virtual environment
rather than in whatever the machine happens to have; `constraints.txt` pins that combination and
says why.

```bash
python -m venv .venv                                           # once
.venv/Scripts/python -m pip install -c constraints.txt -e ".[dev]"   # Linux/macOS: .venv/bin/python
cd web && npm install && npm run build && cd ..                # the interface (writes web/dist)

.venv/Scripts/python -m kapacitet all      # fetch + build + profiles + compute (~15 min)
.venv/Scripts/python -m kapacitet serve    # http://127.0.0.1:8000  (API + UI on one port)
.venv/Scripts/python -m pytest             # 91 tests
```

`python -m kapacitet all --quick` runs a reduced version (fewer years and adoption orders) in a
few minutes, for a first look or for CI.

The API documentation is at `/docs`; every JSON payload carries a `provenance` block with the
sources and assumptions behind it.

### Commands

| Command | What it does |
|---|---|
| `kapacitet fetch` | Download and cache every external dataset, with a `manifest.json` recording URL, parameters, licence, timestamp, row count and SHA-256 |
| `kapacitet build` | Build the synthetic LV network on the real OSM neighbourhood (feeders, cabinets, service cables, transformer) |
| `kapacitet profiles` | Hourly weather, PV, household, heat-pump and EV profiles for every year |
| `kapacitet compute` | Hosting capacity, envelopes, risk, 2030 scenarios, validation and the UI payloads |
| `kapacitet serve` | FastAPI serving the API and the built interface on one port |
| `kapacitet all` | The four build steps in order |

Useful flags: `--site <key>` picks another neighbourhood from `config/site.toml`,
`--quick` shortens a compute run, `compute --stage extra` recomputes only the scenario,
tornado and validation blocks.

### Optional: ENTSO-E token

Prices come from [elprisetjustnu.se](https://www.elprisetjustnu.se) (free, no key, history from
November 2022 — attribution: *Elpriser tillhandahålls av Elpriset just nu.se*). With a personal
ENTSO-E Transparency token in `.env` (`ENTSOE_API_TOKEN=...`, see `.env.example`) the same
prices can be fetched from the source for all 11 weather years.

### Demo in five minutes

Start `python -m kapacitet serve`, open `http://127.0.0.1:8000`, press **F** for fullscreen and
**N** for the presenter notes (Ctrl + arrow keys or PageUp/PageDown move between screens, **ES** switches
language).

The control centre opens in dark mode for new visitors; the header offers light, dark and system
themes and remembers the selection. Use the sidebar to navigate, or scroll the navigation strip
on a phone. The capacity overview links directly to the geographic twin and connection study.
In the twin, step an hour at a time, scrub the year, play at 1×/4×/8× speed, or return to the showcase
hour. The reset-view button restores the map camera. Charts retain their table alternatives, and
animations respect reduced-motion preferences. Use **Ctrl + Left/Right** or **PageUp/PageDown**
to navigate screens without affecting form fields.

1. **Sweden** — why this matters: production in the north, demand in the south, and connection
   queues as the new bottleneck.
2. **Neighbourhood** — the twin opens on the most loaded hour of 2025. Switch *No control*: the
   substation turns red, the transformer runs above its rating. Switch *With envelopes*: the same
   houses, always within limits. Drag the year strip to move through the year.
3. **Static vs DOE** — the headline: 413 kW against 787 kW, what it costs in curtailment, and the
   hour-by-hour verification.
4. **Connection request** — click a house, ask for 15 kWp and an 11 kW charger, press *Evaluate*:
   the static study says no, the envelope says yes with this limit, this risk and this value.
   Switch the grid situation to *2030* and ask again to show the queue effect.
5. **2030** — how far the neighbourhood can electrify before either method needs reinforcement.
6. **Method & data** — live status of every API, every assumption with its source, the validation
   charts and the limitations.

## How it works

```
ingest/     SMHI STRÅNG + metobs, elprisetjustnu (or ENTSO-E), eSett, SCB, OSM Overpass
              -> data/raw + manifest.json (url, params, licence, sha256, fetched_at)
grid/       OSM buildings and streets -> street graph -> radial feeders from the mapped
              substation -> cabinets, service cables, fuses, transformer (pandapower)
profiles/   weather (hour-beginning means), PV (pvlib, roof geometry from footprints),
              households (CREST/richardsonpy pool), heat pumps (when2heat COP), EV charging
engine/     Z-bus power flow, static and time-series hosting capacity, envelopes with
              nonlinear verification, risk, live connection requests, 2030 scenarios
api/        FastAPI: precomputed payloads, SSE for live requests, serves web/dist
web/        React + MapLibre + ECharts: 6 screens, English with a Spanish toggle
```

**Power flow.** A batched Z-bus fixed point `V = w + Z·conj(S/V)` on pandapower's exact model
(cable shunts, transformer tap and magnetising branch). It solves a full year in 1.6 s and
matches `pp.runpp` to 1e-12 pu — the tests check this on both a synthetic feeder and the real
network.

**Hosting capacity.** Static: worst-case summer (PV at rating, minimum daytime load, high MV
voltage) and winter (record demand hour) snapshots. Time series: every hour of 11 weather years
must be within limits. Both use seeded adoption orders, so the result is a distribution, not a
single number.

**Envelopes.** For each critical hour, non-firm customers get export limits from a linear model
(voltage sensitivities `S = Re Z` and a 0/1 downstream incidence for cables and transformer),
bounded by what each customer could actually export that hour. The linear solution is then
corrected against the nonlinear power flow: margins are tightened monotonically, and any hour
that still violates is scaled back by bisection. **Only verified envelopes are published**, and
the full-year re-check reports 0 violations.

**Live connection request.** Click a house, choose PV and a charger: the static verdict, the
residual envelope for 11 years, a nonlinear check of the binding hours, weather risk from a
weekly block bootstrap and the value at SE3 spot prices — streamed stage by stage over SSE,
typically in about a second.

## Data sources

| Data | Source | Licence / note |
|---|---|---|
| Irradiance (GHI, DNI, DHI), hourly 2015–2025 | SMHI STRÅNG point API | CC BY 4.0 |
| Air temperature, hourly | SMHI metobs, station 97200 Stockholm-Bromma | CC BY 4.0 |
| Day-ahead prices SE1–SE4 | elprisetjustnu.se (ENTSO-E data) | free API, attribution requested |
| Production and consumption per zone | eSett Open Data (EXP15/16), load profile EXP18 | open data |
| Household size mix | SCB PxWeb | CC0 |
| Buildings, streets, substation position | OpenStreetMap via Overpass | ODbL — share-alike applies to derived data |
| Bidding-zone geometry | entsoe-py | MIT |
| Basemap | OpenFreeMap / OpenMapTiles | ODbL |
| Capacity maps (Svk, Vattenfall, Ellevio) | linked and cited only, never scraped | — |
| LV cables, cabinets, transformer, fuses, household/EV/heat-pump behaviour | modelled here | synthetic, labelled in the UI |

Model inputs (57 of them) live in `config/assumptions.toml`, each with a value, unit, label,
tag and source; the Method screen shows them all with links, and the tests fail if any of them
loses its source.

## Tests

```bash
python -m pytest            # 84 tests
```

They check the things the demo claims: the Z-bus solver against pandapower, the water-filling
allocation against a lexicographic max-min LP written independently for the test, the
conservativeness of the linear screens (they may never miss a violating hour), that published
envelopes verify with the nonlinear power flow, the radiality of the built network, the parsers
on offline fixtures, the API contract, and that the published results are internally consistent.
Tests that need pipeline output skip themselves on a fresh clone.

## Limitations

Stated openly on the Method screen and repeated here: envelopes use perfect foresight (real
ones are issued from forecasts, so curtailment would be higher); the model is hourly while the
Swedish voltage limit applies to 10-minute means (covered by a 1 % margin); **every published
number comes from the balanced three-phase model**, so single-phase unbalance is not in them;
the LV topology is synthetic;
household demand comes from a UK behaviour model scaled to Swedish consumption; the MV supply is
held at 1.00 pu; economics use the SE3 spot price only. Protection (minimum fault current at
feeder ends versus fuse ratings) is not checked yet.

An unbalanced three-phase solver now exists (`engine/powerflow3ph.py`), validated against
pandapower on the IEEE European Low Voltage Test Feeder, but it does not drive the hosting
capacity or the envelopes yet. What it already shows on this network, with 120 villas each
exporting 4.6 kW of single-phase PV: the balanced model reports 1.048 pu at the worst bus and
stays inside the limit, while the same case with phases allocated at random reaches 1.094 pu
and breaks it. Spreading the connections evenly over the three phases brings that back to
1.067 pu. The balanced model is therefore optimistic, and knowing the phase of each service is
worth real capacity.

## Licence

MIT — see [LICENSE](LICENSE). Two things the licence does not cover: the household profile generator
(`richardsonpy`) is GPL-3.0 and is used only offline to produce the profiles, which stay out of this
repository; and data derived from OpenStreetMap is subject to ODbL share-alike, which is why `data/`
is not published here but regenerated from the sources.
