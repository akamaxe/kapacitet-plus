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
| `kapacitet compliance` | The audit trail and the annual filing a conditional agreement leaves behind |
| `kapacitet forecast` | What the published envelopes owe to perfect foresight, against a forecast-free alternative (also written to `results/forecast.json`) |
| `kapacitet serve` | FastAPI serving the API and the built interface on one port |
| `kapacitet all` | The four build steps in order |

Useful flags: `--site <key>` picks another neighbourhood from `config/site.toml`,
`--quick` shortens a compute run, `compute --stage extra` recomputes only the scenario,
tornado, foresight, over-installation, phase and validation blocks, `--only foresight,phases`
narrows that to a comma list, and `--workers 2` runs the heavy stages on fewer processes when
memory is short.

`compute --stage extra --only overinstallation` writes `overinstallation.json`: for the
deployment points of `zcurve.json`, the energy the customers beyond the static limit produce and
lose to envelopes in every weather year, and its spot value in the years with price data. It is
the profit curve of connecting more than the static study allows.

### Optional: ENTSO-E token

Prices come from [elprisetjustnu.se](https://www.elprisetjustnu.se) (free, no key, history from
November 2022 — attribution: *Elpriser tillhandahålls av Elpriset just nu.se*). With a personal
ENTSO-E Transparency token in `.env` (`ENTSOE_API_TOKEN=...`, see `.env.example`) the same
prices can be fetched from the source for all 11 weather years.

### Demo in five minutes

Start `python -m kapacitet serve`, open `http://127.0.0.1:8000`, press **F** for fullscreen and
**N** for the presenter notes. **1**–**6** jump straight to a screen, Ctrl + arrow keys or
PageUp/PageDown step through them, **ES** switches language, and **?** lists every key.

**Ctrl + K** is the way in if you know what you are looking for: it searches the six screens,
all 120 houses by address — diacritics folded, so `slatt` finds Smedslättsvägen — and the
handful of actions the chrome offers. Picking a house selects it everywhere.

The interface opens on a white ground for new visitors; a designed night theme and the system
setting are in the top bar, and the choice is remembered. The six screens sit in that bar, and
below 1100 px they fold into a menu. **Static vs envelopes** opens with the network drawn as
lines: each feeder leaves the substation like a metro line and every house is a station,
coloured by how its solar is connected. Click a station to test a connection there.

In the twin, step an hour at a time, scrub the year (the strip reads out the hour under the
pointer), play at 1×/4×/8× speed, or return to the hour the transformer is most loaded without
control. **Lines**, at the top right of the map, redraws the feeders in the loading colour of the
hour on screen. The reset-view button restores the map camera. Under the year strip, **120 houses**
opens the neighbourhood as a table you can search and sort: address, feeder, connection, solar,
fuse, distance from the substation, and the voltage and net power of the hour on screen, read from
the same decoded state the map is painted from. Picking a row selects the house, and so does
clicking it on the map; either way the panel leads with it, shows where it sits on its feeder and
offers a connection request for it.

The connection request asks for the house first, the size second and the grid situation last.
The answer comes into view by itself, and changing the request afterwards marks the answer as
out of date instead of removing it. Words of the trade, such as static study, envelope,
curtailment, CVaR, kWp and pu, carry a short definition on hover, focus or tap.

The two long screens, **Static vs envelopes** and **Method & data**, carry a bar of their own at
the top of the scroller: their sections on the left, which follow the scroll and jump when
clicked, and the basis of every figure on that screen on the right. The 63 model inputs are
searchable and sortable: filter by where a value comes from, or search a word in any column.

The address bar carries what you are looking at — `#/twin?hour=4000&scenario=uncontrolled&cid=14`
— so a view can be sent to someone and reproduce exactly. Scrubbing the year does not fill your
history: only changing screen does. Charts retain their table alternatives, and
animations respect reduced-motion preferences. Use **Ctrl + Left/Right** or **PageUp/PageDown**
to navigate screens without affecting form fields.

1. **Sweden** — why this matters: production in the north, demand in the south, and connection
   queues as the new bottleneck.
2. **Neighbourhood** — the twin opens on the most loaded hour of 2025. Switch *No control*: the
   substation turns red, the transformer runs above its rating. Switch *With envelopes*: the same
   houses, always within limits. Drag the year strip to move through the year.
3. **Static vs envelopes** — the headline: 413 kW against 787 kW beside the network drawn as
   lines, what it costs in curtailment, and the hour-by-hour verification.
4. **Connection request** — click a house, ask for 15 kWp and an 11 kW charger, press *Evaluate*:
   the static study says no, the envelope says yes with this limit, this risk and this value.
   Switch the grid situation to *2030* and ask again to show the queue effect.
5. **2030 scenarios** — how far the neighbourhood can electrify before either method needs
   reinforcement.
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
python -m pytest            # 157 tests
```

They check the things the demo claims: the Z-bus solver against pandapower, the water-filling
allocation against a lexicographic max-min LP written independently for the test, the
conservativeness of the linear screens (they may never miss a violating hour), that published
envelopes verify with the nonlinear power flow, that a planning state issued a day ahead
dominates the year it plans, the radiality of the built network, the parsers
on offline fixtures, the API contract, and that the published results are internally consistent.
Tests that need pipeline output skip themselves on a fresh clone.

## Running it on someone else's network

The engine does not have to stay on the reference neighbourhood. `src/kapacitet/importer/`
defines a four-file CSV contract an operator can produce from any GIS, and checks an export
against it before anyone promises a capacity:

```bash
.venv/Scripts/python -m kapacitet export-grid example/   # the contract, filled with our grid
.venv/Scripts/python -m kapacitet check  example/        # the data-quality report
.venv/Scripts/python -m kapacitet import example/        # replace the network with theirs
.venv/Scripts/python -m kapacitet profiles && .venv/Scripts/python -m kapacitet compute
```

The report is the point. It separates what stops the engine from what only costs accuracy,
and it prices the gap: a missing cable type falls back to the site default, missing
coordinates cost the map and nothing else, while no phase column means every service is
taken as three-phase, which on this grid with single-phase inverters reports 402 kW of static
hosting capacity where the median placement of unknown phases allows 225 kW. Errors, such as a cable to a bus that does not exist or a section of zero length,
stop the import rather than being guessed around, and every fallback that is applied is counted and printed next to the result.

The import is faithful, not approximate. Writing the reference network out in the contract and reading it back gives a network that is electrically the same one: voltages at the 120 services agree to 1e-14 pu and the transformer loading to 1e-13 per cent, so every stage after it returns the same answer by construction.

## The paperwork the agreement leaves behind

Ei permits a conditional connection agreement only where scarcity is real, on approved
terms, with curtailment as a last resort and annual reporting. Two of those four are
evidence the operator has to produce afterwards, so the engine produces them:

```bash
.venv/Scripts/python -m kapacitet compliance
```

`audit_trail.csv` is one row per curtailment: the hour, the customer, what they asked for,
what they were granted, and **which component was out of room**, together with the load that
component would have carried had no envelope been issued. That last column is what makes
"last resort" a claim anybody can check. On the reference year it reads 3 533 rows over 148
hours, every one of them bound by the transformer, which would have reached 126 % of its
rating at 11:00 on 19 May.

`compliance.json` and `compliance.txt` are the annual filing derived from that trail, so the
report and its evidence cannot drift apart: 43 agreements, 7 261 kWh curtailed, 2.67 % of
what those customers would have exported, zero violations left after verification, and the
allocation policy that decided who bore it.

## What the forecast is worth

Every figure above is computed with perfect foresight: the envelope for 11:00 is allocated
knowing exactly what the sun and the houses will do at 11:00. A real envelope is issued the
day before. That was the caveat most often held against these numbers, so it is now
measured instead of stated:

```bash
.venv/Scripts/python -m kapacitet forecast
```

The measurement rests on one property. Call a planning state **dominating** if, hour by
hour, it assumes at least as much generation and at most as much load as actually occurs.
Every export-side constraint is monotone in those two quantities, so an envelope that is
feasible against a dominating state is feasible against what happens — including in the
hours the planner never flagged as critical, which is exactly where a point forecast fails.
Safety then needs no forecast skill at all; what it costs is how loose the bound is.

The bound used here is weather-blind: generation at the highest the same fortnight reached
in the other ten weather years, demand at the lowest, hour of the day by hour of the day. A
clock and eleven years of history, no forecast. On the published showcase deployment, 43
non-firm customers in 2025:

| scheme | envelopes issued | limit violations | curtailed | % of export |
|---|---|---|---|---|
| static worst case, no envelope | – | 0 | 267 559 kWh | 98.54 |
| day ahead, no forecast | 1 400 h | **0** | 48 681 kWh | 17.93 |
| day ahead, yesterday's weather | 148 h | **109** | 3 998 kWh | 1.47 |
| perfect foresight (published) | 148 h | 0 | 7 261 kWh | 2.67 |

The persistence row is the instructive failure. It curtails *less* than the published
envelopes and breaks the limits in 109 hours while doing it, because the failure mode is
not an envelope that comes out slightly too generous — it is an hour dropping out of the
critical set altogether, after which no envelope is issued and the customer runs to its
inverter rating. A point forecast is not a weaker version of foresight; it is a different
thing, and it needs a bound.

The static row is the alternative on offer today. On a grid already at its static limit the
worst-case-feasible share of an extra inverter is 0.3 % of its rating, which is why there is
anything to discuss.

Curtailment is one half of the question; the other is how much capacity each scheme carries
at the same 3 % budget. `compute --stage extra` runs the published capacity search again
against the day-ahead state over all 20 adoption orders and writes `foresight.json`, which
the Method screen reads. On adoption order 0 it carries 73 adopters and 622 kW where perfect
foresight carries 96 and 801 kW, against 45 and 402 kW static — so on that order roughly two
thirds of what the envelopes add survives with no forecast at all, and the rest is what a
forecast buys.

History is a bound on the record, not a guarantee. In 2025 the neighbourhood total beat its
own eleven-year envelope in **1 hour of 8 760**, by 2.8 %, and that hour produced no
violation. The per-house exceptions — 333 customer-hours of 1 051 200 — are counted in
`exceedance_audit` rather than argued away.

Two things this is not. The climatology leaves out the year it plans, which for 2025 is also
strictly causal because every other weather year precedes it; planning an early year uses
years that came later, so the multi-year medians are leave-one-out rather than real-time.
And nobody would run a grid on this bound by choice — a real day-ahead forecast is far
better than the climatological extreme, so the honest reading is a bracket: the published
figure is what a perfect forecast buys, this one is what no forecast at all still carries,
and an operator lands between them.

## Limitations

Stated openly on the Method screen and repeated here: the published envelopes use perfect
foresight (measured against a forecast-free alternative in the section above); the model is hourly while the
Swedish voltage limit applies to 10-minute means (covered by a 1 % margin); **every published
number comes from the balanced three-phase model**, so single-phase unbalance is not in them;
the LV topology is synthetic;
household demand comes from a UK behaviour model scaled to Swedish consumption; the MV supply is
held at 1.00 pu; economics use the SE3 spot price only. Protection (minimum fault current at
feeder ends versus fuse ratings) is not checked yet.

An unbalanced three-phase model now exists alongside the balanced one
(`engine/powerflow3ph.py` and `engine/netmodel3ph.py`), validated against pandapower on the
IEEE European Low Voltage Test Feeder. It does not drive the published results yet, but it
has already been used to check them, and they hold: with the inverter sizes this model
produces, a median of 7.0 kW AC, only 6 of the 120 villas fall below the 4.6 kVA
single-phase infeed limit, so almost every connection is three-phase, voltage unbalance
stays at 0.27 %, and the static hosting capacity is identical to the balanced answer, 45
adopters and 402 kW on adoption order 0.

Where it does bite is a neighbourhood of small installations. Forcing every connection
single-phase on the same grid: 43 adopters and 388 kW when the phases are spread evenly,
and, when nobody keeps track of which phase each house went on, a median of 225 kW over 20
random placements, anywhere between 125 and 375 kW (`compute --stage extra --only phases`
writes `phases.json`). In the median placement 44 % of the static hosting capacity rides on a
record the operator may not have. Under the same
export pattern the balanced model sees 1 violating hour out of 300 where the unbalanced one
sees 75.

## Licence

MIT — see [LICENSE](LICENSE). Two things the licence does not cover: the household profile generator
(`richardsonpy`) is GPL-3.0 and is used only offline to produce the profiles, which stay out of this
repository; and data derived from OpenStreetMap is subject to ODbL share-alike, which is why `data/`
is not published here but regenerated from the sources.
