# Kapacitet+

A digital twin of one Swedish low-voltage neighbourhood that puts two connection studies side
by side: the conservative worst-case **hosting capacity** used today, and the capacity unlocked
by **Dynamic Operating Envelopes** (DOE) — time-varying export limits that are verified hour by
hour with a full nonlinear power flow.

The demo neighbourhood is **Smedslätten, Bromma (Stockholm, SE3)**: 120 real villas around a
substation that OpenStreetMap maps at Smedslättsvägen 71A. Buildings, streets, weather, prices
and national volumes are real data; the cables, the transformer and household behaviour are
modelled, and the interface labels which is which on every screen.

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
  more than a 0.63 MVA transformer studied statically (655 kW, sensitivity run). At Ei's standard
  costs that transformer alone costs 156–209 kSEK, as much as 54–73 years of the curtailment the
  envelopes impose, valued at spot prices.
- **Fairness is free in this network.** Max-min, pro-rata, proportional-fair and max-total all
  curtail the same total energy (1.98 % in the showcase year) because one shared constraint
  binds; they only differ in *who* is curtailed (Jain index 0.59 → 0.92).

Every number above is computed by this repository — nothing in the interface is typed in by hand.

## Run it in the browser (GitHub Codespaces)

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/akamaxe/kapacitet-plus?quickstart=1)

Nothing to install: Kapacitet+ runs in a *codespace*, a computer in GitHub's cloud that opens in
the browser and that only you can reach. A free GitHub account includes 60 hours a month of it on
the two-core machine this one uses. Chrome or Edge work best.

1. Sign in to GitHub (a free account is enough) and open
   **<https://codespaces.new/akamaxe/kapacitet-plus?quickstart=1>**. Keep it as a bookmark: the same
   link reopens your codespace later.
2. Click **Create new codespace**. A code editor opens in the browser and sets everything up by
   itself, about five minutes the first time: the engine, the interface and the data, which it
   downloads from this repository's [data release](https://github.com/akamaxe/kapacitet-plus/releases/tag/data).
   You do not need to type anything.
3. If the editor asks whether you trust the authors of the files in this folder, choose
   **Trust Folder and Continue**. Kapacitet+ runs either way, but only then does the editor show
   what it is doing, and that is what keeps the codespace running while you use it.
4. Kapacitet+ opens in a new browser tab. If no tab opens, click **Ports** at the bottom of the
   editor, then the globe next to **Kapacitet+ (8000)**, and allow pop-ups for github.dev.
5. The next times, open the same link and click **Resume**: your codespace opens again in about a
   minute, with its data and the decisions you made in it.

While you use Kapacitet+, keep the editor tab open, even in the background: it shows what the app
is doing, and that is what keeps the codespace running. Once both tabs are closed it stops by
itself after 30 minutes and stops using your hours; <https://github.com/codespaces> lists it, to
stop or delete it. Its port is private to you: do not make it public, because Kapacitet+ has no
login. [docs/codespaces.md](docs/codespaces.md) says what to do when something goes wrong.

For whoever maintains it: the codespace is defined in `.devcontainer/`. `install.sh` builds the
engine and the interface when the codespace is created, `data.sh` downloads and unpacks the data
release, and `start.sh` starts the server each time the codespace starts (outside the editor, so
before the folder is trusted) and, each time someone opens it, keeps the server's log in a
terminal, which is what GitHub counts as activity. If the download fails, the setup page takes the
same file from the browser instead (`KAPACITET_SETUP_UPLOAD=1`, set in `devcontainer.json`).
`.github/workflows/devcontainer.yml` builds the codespace in CI whenever what it installs changes.

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
.venv/Scripts/python -m pytest             # the test suite; tests that need data skip without it
```

`python -m kapacitet all --quick` runs a reduced version (fewer years and adoption orders) in a
few minutes, for a first look or for CI.

### Without the pipeline

The computed data are published as the [data release](https://github.com/akamaxe/kapacitet-plus/releases/tag/data):
download `kapacitet-data.zip` and unpack it instead of running the 15-minute pipeline.

```bash
curl -LO https://github.com/akamaxe/kapacitet-plus/releases/download/data/kapacitet-data.zip
.venv/Scripts/python -m kapacitet unbundle kapacitet-data.zip --sha256   # then: serve
```

Whoever computes new results packs them and replaces the release's file (someone who only
wants to look needs no Node with `--web`):

```bash
.venv/Scripts/python -m kapacitet bundle --pin --out ../kapacitet-data.zip   # full: every screen and live requests
gh release upload data ../kapacitet-data.zip --clobber                         # publish it on the data release
.venv/Scripts/python -m kapacitet bundle --level lite --web                  # the published results only (~7 MB)
```

The bundle records a checksum and the modification time of every file, and `unbundle` checks
the sums and restores the times (the API dates each result by its files); it never overwrites
existing files unless asked with `--force`.

The release's file is pinned: `config/data-release.json`, committed with the code, holds its
sha256 and size. `bundle --pin` rewrites it, so commit that file in the same push as the upload.
`unbundle --sha256` refuses any other file (or give it a digest, `--expect-sha256 <hex>`), the codespace
checks its download the same way, and the setup page takes only the pinned file unless its server
sets `KAPACITET_SETUP_ANY_BUNDLE=1` (for a bundle you made yourself).

A bundle is never committed: the code is MIT, and the data,
derived from OpenStreetMap, are published separately under ODbL ([DATA-LICENSE.md](DATA-LICENSE.md)).
A lite bundle shows every result but cannot evaluate new requests, and the request screen says so.

A container runs the same thing with the data mounted (`docker build -t kapacitet .`, then
`docker run -p 127.0.0.1:8000:8000 -v "$PWD/data:/app/data" kapacitet`); CI builds it on every push.
The port is published on 127.0.0.1 only, because the API has no login: whoever reaches the port
can use all of it, approvals included. Inside the container the server listens on 0.0.0.0, which is
what Docker needs to forward the port; reaching it from another machine needs a reverse proxy with
TLS and a login in front. The server runs as an unprivileged user (uid 10001) and writes only to
`/app/data`, so a folder mounted from the host must be writable by that uid: add
`--user "$(id -u):$(id -g)"` to run it as yourself, or mount a named volume instead
(`-v kapacitet-data:/app/data`). Without data, the interface opens on a page that lists the
commands to run.

A copy without results can also take the data file from its setup page, in the browser, when
its server allows it (`KAPACITET_SETUP_UPLOAD=1`): only on the reference network and while the copy
has no results. The container takes it that way with
`docker run -p 127.0.0.1:8000:8000 -e KAPACITET_SETUP_UPLOAD=1 kapacitet`.

Settings come from the environment: `KAPACITET_DATA_DIR` (another data folder),
`KAPACITET_HOST` and `KAPACITET_PORT`, `KAPACITET_CORS_ORIGINS` (comma-separated; cross-origin
access is off by default), `KAPACITET_MAX_JOBS` and `KAPACITET_JOB_TIMEOUT_S` (live requests),
`KAPACITET_SETUP_UPLOAD=1` (an empty copy takes its data bundle from the setup page) and
`KAPACITET_SETUP_ANY_BUNDLE=1` (it takes any well-formed bundle, not only the pinned release).

The API documentation is at `/docs`. Every response that carries results has a `provenance`
block: the sources behind it, when its own files were computed (`computed_at`), when
`results/meta.json` was last written (`full_run_at`: by any compute without `--only`, the extra
stage alone included) and which of its inputs are real and which synthetic.

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
| `kapacitet study …` | A DSO's study of many secondary substations, each built in its own process (below) |
| `kapacitet all` | The four build steps in order |

Useful flags: `--site <key>` picks another neighbourhood from `config/site.toml`,
`--quick` shortens a compute run, `compute --stage extra` recomputes only the stages that build
on the showcase (`compute --help` lists them), `--only foresight,phases` narrows that to a comma
list (it needs `--stage extra`), and `--workers 2` runs the parallel stages on fewer
processes when memory is short (six by default, four with `--stage extra`; `all` takes it too).
In `all`, `--only` names download sources (`all --only prices`): the build, the profiles and the
compute that follow still run whole.

`compute --stage extra --only overinstallation` writes `overinstallation.json`: for the
deployment points of `zcurve.json`, the energy the customers beyond the static limit produce and
lose to envelopes in every weather year, and its spot value in the years with price data. It is
the profit curve of connecting more than the static study allows.

### Optional: ENTSO-E token

Prices come from [elprisetjustnu.se](https://www.elprisetjustnu.se) (free, no key, history from
November 2022 — attribution: *Elpriser tillhandahålls av Elpriset just nu.se*). With a personal
ENTSO-E Transparency token in `.env` (`ENTSOE_API_TOKEN=...`, see `.env.example`) the same
prices can be fetched from the source for all 11 weather years.

### Getting around

Start `python -m kapacitet serve` and open `http://127.0.0.1:8000`. **1**–**8** jump straight to
a screen, Ctrl + arrow keys or PageUp/PageDown step through them, **F** toggles full screen,
**EN**/**SV** in the top bar switch language, and **?** lists every key.

**Ctrl + K** is the way in if you know what you are looking for: it searches the eight screens,
all 120 houses by address — diacritics folded, so `slatt` finds Smedslättsvägen — and the
handful of actions the chrome offers. Picking a house selects it everywhere.

The interface opens on a white ground for new visitors; a designed night theme and the system
setting are in the top bar, and the choice is remembered. The eight screens sit in that bar, and
when they no longer fit (measured in each language) they fold into a menu. **Static vs envelopes** opens with the network on the real
map in 3D: the houses raised by their floors and coloured by how their solar is connected, the
cables and the substation. Drag to move and turn it (the wheel zooms with Ctrl held, so the page
still scrolls), and click a house to test a connection there.

In the twin, step an hour at a time, scrub the year (the strip reads out the hour under the
pointer), play at 1×/4×/8× speed, or return to the hour the transformer is most loaded without
control. The reset-view button restores the map camera. Under the year strip, **120 houses**
opens the neighbourhood as a table you can search and sort: address, feeder, connection, solar,
fuse, distance from the substation, and the voltage and net power of the hour on screen, read from
the same decoded state the map is painted from. Picking a row selects the house, and so does
clicking it on the map; either way the panel leads with it, with its feeder and its distance from
the substation, and offers a connection request for it.

The connection request asks for the house first, the size second and the grid situation last.
The answer comes into view by itself, and changing the request afterwards marks the answer as
out of date instead of removing it. Words of the trade, such as static study, envelope,
curtailment, CVaR, kWp and pu, carry a short definition on hover, focus or tap.

The two long screens, **Static vs envelopes** and **Method & data**, carry a bar of their own at
the top of the scroller: their sections on the left, which follow the scroll and jump when
clicked, and the basis of every figure on that screen on the right. The model inputs are
searchable and sortable: filter by where a value comes from, or search a word in any column.

The address bar carries what you are looking at — `#/twin?hour=4000&scenario=uncontrolled&cid=14`
— so a view can be sent to someone and reproduce exactly. Every connection assessment is saved in
`data/requests/`, and on the request screen the address carries it (`#/request?cid=84&job=…`), so
an answer can be reopened, printed as a record, or downloaded hour by hour. Scrubbing the year does not fill your
history: only changing screen does. Charts retain their table alternatives, and
animations respect reduced-motion preferences. Use **Ctrl + Left/Right** or **PageUp/PageDown**
to navigate screens without affecting form fields.

1. **Sweden** — why this matters: production in the north, demand in the south, and connection
   queues as the new bottleneck.
2. **Neighbourhood** — the twin opens on the most loaded hour of 2025. Switch *No control*: the
   substation turns red, the transformer runs above its rating. Switch *With envelopes*: the same
   houses, always within limits. Drag the year strip to move through the year.
3. **Static vs envelopes** — the headline: 413 kW against 787 kW beside the network on the 3D
   map, what it costs in curtailment, and the hour-by-hour verification.
4. **Connection request** — click a house (or find it by address), ask for 15 kWp and an 11 kW
   charger, or add a heat pump or a battery, press *Evaluate*: the static study says no, the
   envelope says yes with this limit, this risk and this value. A charger or a heat pump is also
   checked against the house's own main fuse (its peak hour plus the new load, a single-phase
   charger on one phase), and the answer names the fuse subscription it would need.
   Approve it into the queue (*Connect with this envelope*), then ask for the house next door:
   it meets less headroom, because the approved house used it first. *Empty the queue* to start
   again. Every answer can be printed as a record or downloaded hour by hour.
5. **Operation** — what the DSO would send: one house's 24 limits for a day, issued the day
   before, beside the export it would make without them, and the file its inverter would receive.
   Below, the summer replayed in the shadow against a pilot's two tests, and a meter checked
   against what it was sent (try the synthetic day).
6. **Annual report** — what the DSO files with Ei: every curtailed hour, the part of the grid
   that was full, the 43 agreements, and each house's own record to read and download.
7. **2030 scenarios** — how far the neighbourhood can electrify before either method needs
   reinforcement.
8. **Method & data** — live status of every API, every assumption with its source, the validation
   charts, the forecast schemes, a check for your own network export, and the limitations.

New here? The *Tour* in the foot line walks through these screens in two minutes.

## How it works

```
ingest/     SMHI STRÅNG + metobs, elprisetjustnu (or ENTSO-E), eSett, SCB, OSM Overpass
              -> data/raw + manifest.json (url, params, licence, sha256, fetched_at)
grid/       OSM buildings and streets -> street graph -> radial feeders from the mapped
              substation -> cabinets, service cables, fuses, transformer (pandapower)
profiles/   weather (hour-beginning means), PV (pvlib, roof geometry from footprints),
              households (CREST/richardsonpy pool), heat pumps (when2heat COP), EV charging
engine/     Z-bus power flow, static and time-series hosting capacity, envelopes with
              nonlinear verification, risk, live connection requests (solar, EV charger,
              heat pump, battery), the queue of approvals, 2030 scenarios, day-ahead
              schedules and the inverter's file (CSIP-AUS), the shadow season, meter checks
api/        FastAPI: precomputed payloads, SSE for live requests and approvals, the saved
              assessments (data/requests/), serves web/dist
web/        React + MapLibre + ECharts: 8 screens in English and Swedish (docs/glossary.md)
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

Model inputs live in `config/assumptions.toml`, each with a value, unit, label,
tag and source; the Method screen shows them all with links, and the tests fail if any of them
loses its source.

## Tests

```bash
python -m pytest
```

They check the things the demo claims: the Z-bus solver against pandapower, the water-filling
allocation against a lexicographic max-min LP written independently for the test, the
conservativeness of the linear screens (they may never miss a violating hour), that published
envelopes verify with the nonlinear power flow, that a planning state issued a day ahead
dominates the year it plans, the radiality of the built network, the parsers
on offline fixtures, the API contract, and that the published results are internally consistent.
The solver, screen and envelope tests run on a tiny synthetic grid committed in
`tests/fixtures/grid_tiny` (twenty houses, a 0.1 MVA transformer, a feeder long enough for the
voltage to bind), with synthetic profiles written at test time, so they run on a fresh clone and
in CI; where the pipeline output exists they run on the reference network as well. The other tests
that need pipeline output skip themselves on a fresh clone.

The interface has its own, in `web/`:

```bash
npm run test:unit        # Vitest: formatting, dictionaries, the URL rule, the map's binary reader
npm run test:e2e         # Playwright on a fresh clone's view: the API with an empty data folder
npm run test:e2e:local   # Playwright on this machine's computed data (reuses a server on :8130)
npm run perf             # what the opening screen costs a first visit (bytes, requests, LCP; docs/perf.md)
```

Playwright drives the Chrome already installed on the machine (`channel: "chrome"`), so nothing
downloads a browser; the end-to-end runs need `web/dist` built and the project's Python
(`KAPACITET_PY`, or `.venv` by default). The map's binary layout is pinned from both sides by one
golden file, `tests/fixtures/state_tiny.json`: the Python test encodes it and the Vitest test
decodes it.

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

### Networks in the app

The same import runs from the Method screen, without touching the reference network. Check an
export (or the worked example), give it a name and *Create and build*: the four files are kept
in `data/datasets/<id>/import/`, and a separate process builds the network in its own folder,
`python -m kapacitet dataset build <id> --workers 2` with `KAPACITET_DATA_DIR` on that folder and
`KAPACITET_RAW_DIR` on the reference network's downloads. It reads the export, copies the
reference network's household profiles, builds the profiles for every weather year and computes
everything the screens read; `dataset.json` records each step, and `build.log` keeps the output.
A *quick* build (one weather year and fewer adoption orders in the computation) takes minutes.

`python -m kapacitet serve` now runs the server as the child of a small supervisor. *Use this
network* asks first, then writes `data/active.json` and has the server exit with code 75; the
supervisor starts it again on the network's folder, so no cache survives the switch. Switching
is refused without the supervisor (`KAPACITET_SUPERVISE=0` runs the server directly) and while an
evaluation or a decision is running, and it never happens by itself. Assessments, the queue and
applications belong to each network. A link made on an imported network carries `ds=<id>`; opened
while the server shows another network, it says so and switches nothing. An imported network has
its own grid, and the reference network's weather, prices and households (`/meta` says so).

The API writes to two places: `data/requests/` of the network in use, and `data/datasets/`.
A third, only on a copy without results and only when `KAPACITET_SETUP_UPLOAD=1`: the data bundle
uploaded from the setup page, unpacked into `data/processed/` and `data/raw/` and nowhere else.

### A study of many stations

A DSO's network is hundreds of secondary substations, not one. A *study* is a folder of its own
that holds them, kept outside the code and outside any git work tree (by default
`%USERPROFILE%\kapacitet-studies\<id>`, or `KAPACITET_STUDY_DIR`), because it holds the DSO's data:

```bash
.venv/Scripts/python -m kapacitet study demo  demo        # three synthetic stations, from pandapower's LV benchmarks
.venv/Scripts/python -m kapacitet study init  nb-2026     # an empty study with a study.toml of defaults
.venv/Scripts/python -m kapacitet study import nb-2026 --contract <folder> --station <id>
.venv/Scripts/python -m kapacitet study fetch nb-2026     # SMHI weather and spot prices for the study's site
.venv/Scripts/python -m kapacitet study check nb-2026     # the contract's report, per station
.venv/Scripts/python -m kapacitet study build nb-2026 --quick
.venv/Scripts/python -m kapacitet study status nb-2026
```

`study.toml` says where the weather comes from (lat/lon with the SMHI temperature station there),
the method's choices (percentile, curtailment basis, the consumption device), the MV band, what may
be disclosed and how the CSV files are written, and overrides of `config/assumptions.toml`; an
override must name an existing input that a station's build reads, its unit and its source. Each
station is one contract folder in `inputs/network/contract/<id>/` (with an `origin.json` that says
what produced it; without one the network is labelled unverified), split into `stations/<id>/`,
and is built by `kapacitet station build <id> --study <dir>`, a process of its own with
`KAPACITET_DATA_DIR` on the station's folder: a failing station marks only its own `station.json`,
and a station whose inputs (its network, the study's inputs, the weather and household profiles
it starts from), method and mode are unchanged is not built again. `station.json` records each
step, the digests of the inputs, the code's version and commit, and what is real, synthetic or
assumed. A study without a place of its own, or that has not fetched its weather, reuses copies of
the reference network's weather years, and says so; fetching makes its stations stale. The
household load is the synthetic profile pool, scaled when the study overrides
`household_kwh_per_year`, until meter data are read. The capacity figures of a station are marked
provisional: the study's MV band and method choices are not applied yet. The demo's networks are
built by pandapower at run time and written through `importer/from_pandapower.py`, which turns the
LV island behind any pandapower transformer into the contract.

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

Every assessment, every queue decision and every emptying of the queue is also **sealed**
(`src/kapacitet/seals.py`): its canonical JSON is hashed with SHA-256, kept as an immutable copy
in `data/requests/sealed/`, and chained into `data/requests/seals.jsonl` with the fingerprint of
the model and data it was computed with. Editing a kept answer, or removing or reordering a line
of the chain, shows at once: `GET /api/v1/seals/verify?kind=assessment&id=…` (the *Verify* button
under an answer) re-hashes both, `GET /api/v1/seals` gives the chain's head, and
`GET /api/v1/queue/log.csv` is every decision ever sealed, including those an emptied queue no
longer holds. This is tamper-evident, not tamper-proof: whoever can write the folder can rewrite
the whole chain, so the head is worth writing down somewhere else, as the printed record does.

## Every rule, and what can be sent the day before

```bash
.venv/Scripts/python -m kapacitet compute --stage extra --only operation    # about two minutes
```

The published envelopes use perfect foresight: each hour's limit knows that hour, which is the
bound an operator works towards but cannot send in advance. `engine/operation.py` computes the
published deployment under each of the four sharing rules and on two bases, perfect foresight
and **issued the day before** (the dominating state of `forecast.climatological`: safe without a
forecast), and verifies every combination over the whole year with the nonlinear flow before it
can be served. Each one gets its own envelopes, filing, audit trail and map state under
`results/operation/<basis>/<rule>/`; the audit trail now says whether each limited hour needed
limiting at all. On the reference year the day-ahead envelope keeps the grid inside its limits
in every hour but curtails 17.9 % of the non-firm export instead of 2.67 %, in 1 146 hours of
which only 148 needed it: the price of issuing without a forecast, and a reason to say so in the
filing rather than call it a last resort.

The DSO picks its sharing rule (`PUT /api/v1/settings`, or *Make this the DSO's rule* on the
Annual report screen); the change is sealed with the rule it replaced, and the report, the audit
trail, each house's envelope and the neighbourhood's envelope scenario follow it. The live
request still meets the published envelopes allocated equally (its baselines are computed once
for that rule).

### Sending it: the schedule, the inverter's file, the shadow season and the meter

Only the day-ahead basis is ever sent; a schedule with perfect foresight would be hindsight. The
**Operation** screen and these endpoints read what the operation stage wrote:

- `GET /api/v1/operation/schedule?cid=&date=` (`&format=csv`): a house's 24 limits for a date,
  each with its interval start in UTC, on the study's clock (UTC+1 all year) and in Stockholm time,
  beside the export it would make without them.
- `GET /api/v1/operation/schedule.xml`: the same day as an IEEE 2030.5 `DERControlList` in the
  CSIP-AUS v1.2 profile, one `DERControl` per hour with `csipaus:opModExpLimW`. Downloading it
  issues it, and the issue is sealed with the limits it sent; the seal comes back in `X-Seal-Digest`,
  `X-Seal-Seq` and `X-Seal-At`, and the screen keeps it as a receipt under the button
  (`issue=false` is a preview that leaves no record). `GET /api/v1/operation/default.xml` is the `DefaultDERControl` an inverter
  keeps when no schedule arrives: never above the lowest limit issued that year, so close to zero
  here. The format is modelled on the profile, not certified, and no device is connected.
- `GET /api/v1/operation/shadow`: May to August replayed day by day (`shadow_season_months`) and
  judged by two tests: no hour above a grid limit, and every agreement within the level it was
  promised (the DSO's `agreed_level_pct`, by default the capacity search's own 3 % of each house's
  solar energy over the year). On the reference year the day-ahead envelope passes the first and
  fails the second: 0 hours above a limit, but only 3 of 43 agreements within 3 % (median 10.4 %,
  worst 24.9 %). With perfect foresight 35 of 43 would be, worst 4.3 %. Not ready to go live, and
  the screen says so; the remedy, re-issuing during the day with measurements, is outside this
  prototype.
- `POST /api/v1/operation/meter-check` with `{cid, csv}`: measured export (a `timestamp` with its
  UTC offset and `export_kw` or `export_w`) against the limits the house was sent. A breach is a
  reading more than 250 W above its hour's limit, the measure SA Power Networks used in its
  Flexible Exports trial (`meter_breach_tolerance_w`). `GET /api/v1/operation/meter-example`
  writes a synthetic day to try it on, with one deliberate ten-minute breach.

## The evidence for Ei

Ei's position paper on conditional agreements (Ei2025:01, version 1.1, June 2026) sets the
conditions a DSO must meet; `config/ei_conditions.toml` lists them with their sections (and the
paragraphs of ellagen and Regulation (EU) 2019/943 they rest on), in English and Swedish.
`GET /api/v1/eipack?basis=&policy=` sets each one beside what this prototype can show for it:
**shown by the model** (with its figures), **stated by the DSO** (three statements only the DSO
can make, kept in its settings and sealed on change), **awaiting the DSO** (one of those statements
not yet made: never met, but not shown as a failure of the model), **not met here**, or **does not
apply**. On the day-ahead basis the model shows the capacity shortage, the terms and
the annual report, and says plainly that limiting 1 146 hours where 148 needed it does not meet
"only when the grid would otherwise be overloaded", nor "only once the market result shows an
overload". `export=true` downloads the pack sealed with what it showed, with the same seal headers
and receipt as an issued schedule. The Annual report screen
holds the section, the statements and a printable method description. It is evidence from a
simulation, never a legal assessment; the regulation's rule on compensation for non-market
redispatch is not assessed.

## Applications and the DSO's rule

An assessment answers *what if*; an application asks for the connection. *Apply* under an answer
on the request screen (or `POST /api/v1/applications {assessment}`) registers one, with a
reference such as `KP-1A2B-3C4D`, and seals it. One worker takes the applications in the order they
arrived. If the queue has moved since the assessment, it evaluates the request again against the
grid as it now stands, then `engine/rules.py` gives its verdict:

- **firm** when every part passes the static study and the house's own main fuse and phases take it;
- **with an envelope** when the static study rejects a part but the verified envelope keeps the
  house's curtailment within the DSO's thresholds on average and in one year in ten (by default the
  agreed level, 3 % of its solar energy), leaves at most 1 % of its EV charging unserved
  (`ev_unserved_limit_pct`), no heat unserved, and no hour above a grid limit;
- **to a person** otherwise, with the reasons. The rule never declines.

An approval by the rule goes through `queue.decide` like a person's, so it is verified on every
weather year before the house enters the grid, under the queue's write lock (a manual decision
waits for it, and it waits for a manual decision). The thresholds and the switch are the DSO's
settings (`PUT /api/v1/settings`, sealed). Every step is an event in
`data/requests/applications.jsonl`, appended and never rewritten. `GET /api/v1/inbox` folds them
into each application's state, and `GET /api/v1/inbox/kpis` measures the inbox from the same
events: the median and P90 time to a decision, the share the rule decided, and the kW connected
firm and with an envelope. The **Operation** screen shows the inbox; an application waiting for a
person is decided there with the queue's usual decision, or sent back to be evaluated again.

### The applicant's portal

`http://127.0.0.1:8000/#/apply` is a page for the household, not the DSO: pick your address, say
what you want to connect (solar, a charger, a heat pump, a battery), read the answer in plain
words, with what connecting now is worth against waiting, and apply. The application gets a
reference code such as `KP-1A2B-3C4D`, which is all the status form needs; the portal asks for no
personal data and keeps none. Its API, `/api/v1/apply/*`, serves only the addresses, the answer for
one house and the state behind one code. The grid the answer is computed against is decided on the
server (the queue once it holds approvals, otherwise the DSO's published situation), and sending
the same application twice returns the first (an idempotency key). `main.tsx` chooses the portal
before it imports the application, so the page loads neither the map nor the chart library, and
the application's address handling (`initUrlSync`) never sees `#/apply`.

## The business case

Two sides, both from published results and sourced prices (`config/assumptions.toml`, shown on
the Method screen):

- **The DSO's** (`GET /api/v1/business`, under the reinforcement comparison): deferring a cost C
  by T years is worth C(1 − (1+r)^−T) today; the envelopes cost the houses their curtailed energy,
  k a year, worth k·a(r, T) over the same years. With Ei's real rate for 2024–2027 (4.53 %) and
  its norm values at 2022 prices, deferring the transformer swap by one regulatory period (four
  years, the DSO's `deferral_years`) is worth 15–24 kSEK after the curtailment, 2 878 SEK a year
  at the spot prices of the price years (not deflated). It pays at any horizon while the swap
  costs more than k/r, about 64 kSEK, and the swap's own norm cost is 156–209 kSEK.
- **The house's** (`GET /api/v1/requests/{id}/owner-case`, under an answer with solar): connect now
  with the envelope, or wait for the reinforcement. Solar used at home saves the spot price plus
  the energy tax (36.0 öre/kWh from 2026), Ellevio's transfer fee (20.8 öre/kWh from June 2026)
  and VAT; exported solar earns the spot price and Ellevio's compensation for its net benefit
  (4.40 or 3.30 öre/kWh, weighted by when the house exports). The tax reduction for exported solar
  ended on 1 January 2026 and is not counted. The sum over the wait is discounted at a household
  rate that is a modelling assumption (3 %), with the undiscounted sum beside it; the solar system's own
  cost is the same either way and is left out.

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
number comes from the balanced three-phase model**, so single-phase unbalance is not in them
(a request with a single-phase charger or inverter is checked on the unbalanced model, phase by
phase, at the static study's worst hour); the LV topology is synthetic;
household demand comes from a UK behaviour model scaled to Swedish consumption; the MV supply is
held at 1.00 pu; economics use the SE3 spot price only.

Protection is checked by `compute --stage extra --only protection` (`engine/protection.py`, under a
second): a single-phase fault at the weakest point of each feeder and at each house, at the minimum
fault current (IEC 60909, pandapower `calc_sc`), must blow its fuse within 5 s (IEC 60364-4-41,
411.3.2.3). Each feeder's head fuse is the largest gG rating its main cable allows (1.6·I_n within
1.45·I_z, IEC 60364-4-43, 433.1), and the melting currents are the IEC 60269 gates (Enel GSCL004,
table 7). On the reference grid every house's main fuse clears (lowest fault current 805 A), but 6 of
the 11 feeders do not at their far end with a 200 A fuse: the synthetic design would need a smaller
fuse or a heavier cable there. Cabinet fuses and what inverters add to a fault are not modelled.

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

The newer parts carry their own limits. A **battery** in a request stores only the solar its
envelope would curtail and gives it back to the house, its efficiencies come from Orth et al.
(2023), standby consumption is not modelled, and its energy is valued at the spot price, so the
figure is what it recovers from curtailment, not a payback. A held-back **heat pump** could shift
some heating with the house's thermal mass, which is not modelled, and its import limit ignores
the house's own solar. The **queue** is one per server, first come first served; it is a tool for
one planner on a trusted machine, and the API has no accounts or authentication, so it must not
be exposed to the internet as it is. The **reinforcement** comparison uses Ei's norm values,
standard costs at 2022 prices rather than quotes, undiscounted, with the bigger unit's capacity
taken from one adoption order of the sensitivity run.

## Licence

MIT — see [LICENSE](LICENSE). Two things the licence does not cover: the household profile generator
(`richardsonpy`) is GPL-3.0 and is used only offline to produce the profiles; and the data, derived
from OpenStreetMap, are subject to ODbL share-alike. That is why `data/` is never committed here:
the computed data are published separately, as the asset of the
[data release](https://github.com/akamaxe/kapacitet-plus/releases/tag/data), under ODbL
([DATA-LICENSE.md](DATA-LICENSE.md)).
