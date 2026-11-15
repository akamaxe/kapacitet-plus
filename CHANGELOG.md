# Changelog

What changed for someone who runs Kapacitet+ or reads its figures. The version is still 0.1.0 and
nothing here is a release: `main` is the supported version, and the commit history has the rest.
Changes before 12 November 2026 are only in that history.

## Unreleased

### Added

- **A study of many stations in the workbench.** The start window lists the studies of the studies'
  folder (`KAPACITET_STUDY_DIR`), and *New study…* (also in the File menu) makes the demonstration,
  an empty study, or one from contract folders (a .zip with one folder per station, read in the
  browser) or from CGMES files with their table of customers. A study's screen (`#/study?id=`)
  builds its stations in a process of its own, with each station's step as it goes, shows each
  one's state, published capacities, binding limits and margin, and takes a run through collect,
  review, sign, publish and compare, with every file of the run to download. Opening a study
  restarts nothing: it is read from its folder. The routes (`/api/v1/studies…`, `api/studies.py`)
  call what `kapacitet study` calls, and a test checks that what the app publishes is byte for byte
  what the command publishes; they are the API's third place to write. Signing takes the approver's
  or the administrator's role, the rest a planner's.

- **A start window, and projects.** The interface opens on the projects of the server: the
  reference network and every network made in the app. *New project…* makes one in three steps
  from a network export (four CSV tables), from one of thirteen example networks (pandapower's
  low-voltage benchmark networks, synthetic and labelled so) or from a project file, and
  *Export (.zip)* writes a project as one. `GET /api/v1/templates`, `GET /api/v1/templates/{id}`
  and `GET /api/v1/datasets/{id}/project.zip` are new; `GET /api/v1/datasets` gained each
  network's summary and where it came from.
- **A workbench around the open project.** A bar with the logo and six menus (File, View, Study,
  Results, Tools, Help), a project tree (the network with its transformer, feeders and houses, the
  studies, the results), an output window that keeps what the program did with its time, and a
  status bar. Keys: **0** the start window, **T** the project tree, **O** the output window;
  **1**–**8** as before.
- **Study > Computation status…** says what is computed for the project, when, and the command
  that computes each part again. `/health` now gives every part its command, missing or not.
- **Installable.** The browser can install the interface with a window and an icon of its own
  (a web manifest; no service worker, nothing cached).

- **The consumption capacity, on screen.** It was computed for every project and shown nowhere:
  the capacity screen now has a section with the new EV chargers, heat pumps and constant loads the
  street takes by the hourly study, against the static study's convention (a constant load of the
  charger's rating in every hour): 24.5 chargers against 3 on the reference network. A house's panel
  says the load it alone can add, and the start window's card the chargers a project takes.

### Changed

- **The DSO's static method, as a profile.** The static study can be stated as the DSO states its
  own: the solar output of the summer snapshot (× AC rating), the household load then (the lowest
  daytime summer load, none, or a fixed figure per customer, which has no default), and the DSO's
  own limits on top of the model's: a largest voltage rise at a connection point over the same
  snapshot with nobody added, and a highest loading below the rating (`static_v_rise_limit_pct`,
  `static_max_loading_pct`, `static_fixed_load_kw`, new in `config/assumptions.toml`). A project
  keeps its profile in `data/requests/method_profile.json` (the Method screen, *The DSO's static
  method*; `GET/PUT /api/v1/method-profile`, with the role that changes the DSO's settings), and
  every process of that folder reads it into the study layer, so it reaches the engine's workers;
  a study states the same keys in its `study.toml` `[assumptions]`. The default profile is today's
  static study (51 installations, 414 kW in adoption order 9 of the reference network), and the
  solar output at 80 % gives the sensitivity's case (520.2 kW): a test checks both. A saved profile
  changes the static figures at the next computation of the capacity, and the screen gives the
  command. A study takes the DSO's own static results per customer from its screen
  (`PUT /studies/{id}/dso-static`), and its table shows each station's static capacity and model −
  DSO beside the hourly study once validated. Velander for the consumption snapshot is not in the
  profile: it is a formula for a group, and the profile does not yet group the customers by element.
- **Meter readings in the shapes metering systems write, and from the app.** Besides the long
  format with each instant's offset, the reader takes Swedish local time without an offset (a file
  whose every time lacks one: the hour the clocks show twice in October is taken in the meter's
  order, summer time first; a time shown once there, or the hour skipped in March, is left out and
  said), Wh, readings by the hour (`minutes` = 60, a complete hour in one reading), and one row per
  meter and day with 24 or 25 hourly or 96 to 100 quarter-hourly columns (`ingest/meter_formats.py`).
  Readings are uploaded to a project (*Add meter readings…* on its card, `PUT /datasets/{id}/meters`)
  or to a study (its screen, `PUT /studies/{id}/meters`) and checked at once; a project's build has
  the meters step a station has (the metered load in place of the pool's), and its card says how
  many customers have readings and over which period. A test builds and validates a station of the
  demonstration with readings in rows of a day and the tolerances of `study.toml`.
- **A study builds as many stations at once as the memory holds.** `study build` no longer runs six
  station processes by default: it runs what the free memory holds (the smaller of the memory
  available and the commit left, less 2 GB kept for the rest of the machine), at the largest peak a
  station of the study recorded in the same mode, or 1.5 GB before one has, and never more than the
  cores. `--jobs` still decides when given, and `study cases`, `options`, `devplan` and `scenarios`
  follow the same rule. The build says how many and why. Each station records the most memory its
  process took (`peak_memory_mb`): on the laptop it was measured on, 1.1 GB for 12 customers and
  1.7 GB for 146 in quick mode. While a study builds, Windows is asked not to suspend
  (`SetThreadExecutionState`); a jump of the clock all the same (a lid closed) is said after the
  build and marked on each station that was building then (`suspended_s`), since its seconds include
  that time. A station still runs in its own process: its imports cost about 3 s
  (`python -X importtime`), against the risk of one station's caches or paths reaching the next.
- **The weakest point takes fewer flows, with the same answer.** The five customers with the least
  linear headroom are searched with the nonlinear flow on their tightest hours, as before; the lowest
  is confirmed on every hour, and each of the other four is solved on every hour at that figure only:
  one that breaks a limit there is weaker, and is confirmed in turn. Before, each of the five was
  confirmed at its own figure, with its own rounds of searching again (`worst_point(confirm="all")`,
  kept to compare). The candidates are also searched on the 24 tightest hours of each year at the
  start, not 3, which saves rounds. On the 13 example networks in quick mode (generation at both ends
  of the MV band, consumption at the low end), both ways give the same customer and figure in all 39
  cases, in 231 s against 532 s; a small network with nothing to search again takes as long as before.
- **`tools/study_scale.py`** samples the free memory while the build runs and reports the least,
  counts hard links apart (the household pool a station links from the reference network is kept on
  disk once, though each station's folder counts it), and shows each station's peak. The weekly data
  suite builds five stations and writes their table to its summary.
- **A project can have a site of its own.** The last step of *New project* takes its position (the
  export's transformer, or typed) and its bidding zone, which is chosen, never guessed
  (`POST /datasets` `site`). The build's new `site` step finds the nearest SMHI temperature station
  with every weather year, writes the project's `study.toml` (a study's site layer) and fetches that
  place's STRÅNG irradiance, the station's temperature and the zone's prices into `<project>/raw`;
  `processed/site.json`, `/meta.dataset.site`, the project's card and the status bar say where the
  weather, the prices and the households come from. Without a site, the reference site's, as before.
- **Swedish cable types, and a table for the names of a network information system.** A NIS names
  its cables its own way (`AXQJ 4x150/41`, `N1XE-AR 4G150`), which pandapower's library does not
  know. `config/cable_types_se.toml` is a catalogue of Swedish low-voltage cables with their
  resistance, reactance, capacitance and current rating, each type with its manufacturer's
  datasheet, its address and the day it was read; a type without them is refused. The catalogue is TOML, like the rest of config/. It starts with
  AXQJ 4x50/16 to 4x240/72 (Nexans) and N1XV 4x10 and 4x16 (NKT); the datasheets give no
  capacitance, so it is 0 and the row says so. An export can bring an optional `cable_types.csv`
  that translates each name of `lines.csv` to a catalogue type or to its own data, and the build
  looks a name up there first, then in the catalogue, then in the library. A name none of them
  knows is an error that names it, with its sections, and step 2 of *New project* has a table to
  translate each one into the project's `cable_types.csv` (`GET /api/v1/cable-types` lists the
  catalogue). A translation to a type the catalogue lacks, without data of its own, is an error
  (`cable_map_type`).
- **SWEREF 99 TM coordinates are converted.** An export whose buses all give SWEREF 99 TM metres is
  converted to degrees and the check says so (`coordinates_converted`, a warning); metres for some
  buses and degrees for others is still an error. The check offers the transformer's position as the
  new project's site (`site_hint`).
- **A computation has two parts, and a project is built for its capacity first.** `compute --part
  capacity` computes what the four screens of an installation read: the capacity per adoption order
  by the static and the hourly study, the hourly study's deployment, the consumption capacity, the
  request's static baseline, the sensitivity of the two studies, the single-phase check, the
  validation and the protection. `compute --part envelopes` adds the envelopes per order and
  everything built on them; `compute` alone does both, as before. A project made in the app is
  ready to open once its capacity is computed (under a minute for the 12-house example network,
  quick), and where the envelopes are on the start window offers *Compute envelope studies*, a
  second build (`dataset build <id> --part envelopes`, `POST /datasets/{id}/build?part=envelopes`).
  `hosting_summary.json` holds only the measures computed and says which parts (`parts`), as
  `meta.json` does; `/health` has an `envelopes` part, and `results` is now the capacity part
  (`showcase.json` and `risk.json` moved to `envelopes`), so a project computed for its capacity is ready.
  `extra` is the capacity part's checks (validation, consumption, protection). Where the envelopes
  are on and a project lacks them, the screens show its capacity and the capacity screen says what
  to compute. The request's map is painted from the grid situations a request meets
  (`GET /baselines`).
- **Four parts are off unless an installation switches them on.** The dynamic operating envelopes,
  the Sweden screen, the 2030 scenarios and the applicant's portal stay in the code, tested, and are
  named in `KAPACITET_FEATURES` (`envelopes`, `national`, `scenarios`, `portal`, or `all`);
  `KAPACITET_ENVELOPES=1` switches the envelopes on alone. With none on, four screens remain: the
  neighbourhood, **Capacity** (the front page, called Static vs envelopes where the envelopes are on),
  the connection request and the method. A route of a part that is off answers 404 `feature_off`,
  `/health` and `/meta` list the parts, and `kapacitet instance check` reports them. Start the server
  with `KAPACITET_FEATURES=all` to see everything as before. The envelopes are now an experimental
  module: by the rule committed before the experiment, nothing that can be issued in advance
  connected more than the hourly study on any network measured.
- **Where the envelopes are off, a request is answered by the hourly study.** Its second verdict is
  firm when no hour of any weather year breaks a limit with all it asks for. The queue approves on
  that basis (`decision: "hourly"`, kept as a firm connection with `basis: "hourly"`) after checking
  every weather year again, and the DSO's rule does the same (`hourly`) or sends the application to
  a person with the hours it would break (`hourly_limits`); it never connects on an envelope then.
  A request cannot ask for the envelope scenario or a battery, the household's case of connecting
  now or waiting is not offered, and an assessment's hourly CSV names its columns as headroom.
- **With the portal on and the envelopes off, Operation is called Applications** and holds the
  applications and the settings of the rule and the portal; the rule's thresholds for curtailment
  are not shown, since nothing is curtailed.
- **The empty queue's message** no longer points to the envelope scenario.
- **Links to a screen are unchanged; a link to nothing opens the start window.** `#/compare` and
  the other seven open the project on that screen as before. `/` and `#/start` are the start
  window, where `/` used to open Static vs envelopes.
- **The eight screens left the top bar.** They are entries of the View, Study and Results menus
  and rows of the project tree, under the names they had. The language and the keys moved to the
  status bar, the theme and full screen to the View menu, the tour to Help.
- **`kapacitet dataset build <id>` builds in the network's own folder.** Typed by hand it used the
  folder it was typed in, which is the reference network's, and wrote that network's grid,
  profiles and results over. It now starts itself again in the network's folder, and a build
  refuses any other. A network's build also writes the redispatch report and the fleet's
  compliance check, and for an imported network every "not computed" note and `/health` name that
  build as the command, not `compute`.
- **A network made from tables is drawn.** `GET /api/v1/network` gives a house without an outline
  a square of its own footprint and a cable without a route a straight line, marks each such
  feature `drawn`, and says in `drawing` how many there are and whether the layout is a place or a
  schematic. `GET /api/v1/meta` says where the open network came from and whether it is synthetic,
  and each response's provenance follows it.
- **The sensitivity's transformer cases follow the network.** They are the standard units next to
  the network's own, for its own voltages, where they were 0.25 and 0.63 MVA at 10 kV for every
  network. The reference network's cases are the same as before.
- **The name and the logo are not part of the MIT licence.** The logo's files are in the
  repository (`web/public/brand`, the icons) so that the interface can show them; the README's
  licence section says the licence gives no right to use them for anything else.

- **The server refuses to listen beyond this machine without sign-in.** `kapacitet serve` with a
  host other than a loopback address now ends with exit code 2 until `KAPACITET_AUTH` is set to
  `oidc` or, on purpose, to `off`. The container listens on 0.0.0.0 inside, so `docker run` needs
  `-e KAPACITET_AUTH=off` (or the sign-in settings). On this machine it starts as before and says
  in its log that sign-in is off.
- **A network that is not radial is refused.** A ring, or two rows between the same two buses, was
  a warning and is now an error of the network check; the model refuses such tables too. Give
  cables laid in parallel as one row with `parallel`.
- **A study's publication.** `capacity.csv` and the GeoJSON gained
  `generation_shared_by_stations`, `consumption_shared_by_stations` (how many stations share the
  upstream headroom that caps a figure) and `rounded_to_kw`. The figures of a cell with fewer than
  20 customers are rounded down to 10 kW by default (`[disclosure] round_kw`,
  `round_below_customers`; `round_kw = 0` turns it off).
- **Validation is the DSO's verdict.** `[validation]` in `study.toml` holds the DSO's own
  tolerances. Without them no station is called validated; with them, a station outside a
  tolerance is not published. The validation report no longer counts a station without readings
  as validated.
- **The front page and the annual report lead with what can be issued.** The display figure of
  Static vs envelopes is the issuable range, not the perfect-foresight bound, and the annual
  report opens on the day-ahead basis. A link to the perfect-foresight filing now carries
  `basis=foresight`.
- **Sign-in misconfigured.** The API's replies say that sign-in cannot run and no longer which
  variable is missing; the server's log, the start-up and `kapacitet instance check` say it.
- **Results files** written from now on carry a `computed` block: when, the commit of the code and
  the library versions.

- **Swedish texts say that their language is not reviewed.** The notes of `study options` and
  `portfolio`, the development-plan inputs, a case's annex and drafts, the method dossier, the
  redispatch and meter-compliance pages and a draft publication carry one sentence saying that no
  native speaker has read them. A signed publication does not: signing is where the DSO takes the
  text as its own. The MV band's default source is printed in Swedish.

### Fixed

- A study in a deep folder on Windows failed with "no such file or directory": a temporary file
  was 14 characters longer than its file. It is now never longer, and a path Windows refuses for
  its length says the length, the limit and what to do.
- A station whose process ended without a result said only its exit code; it now gives the last
  line of the station's log and where the log is. A case's own error is no longer cut at 200
  characters, which lost the file's name.
- A logo whose image data was not exactly the image its header declares was accepted.
- The preview of the portal's bar, in the DSO's settings, showed the product's white logo on a white
  bar until the DSO typed its name; it now shows the dark bar the portal shows then.
- The RFC 3161 tokens a chain still owes were listed in the folder's own order, not in line order;
  with PostgreSQL, in whatever order the table gave them. Both are now in line order.
- An identity provider that does not answer gave a server error; it is now `provider_unavailable`.
- `kapacitet study options` ended with a `KeyError` and exit code 1 after writing its files.
- The first evaluation after a start waited on eight years of ENTSO-E prices read point by point;
  a year is now read in arrays, with identical results.
- Identifiers that name a folder or a file (a network of the app, a station, a run, a case) no
  longer accept a final newline.
- The README said the API had no authentication, that re-issuing during the day was outside the
  prototype, that an application was stored with the address, and that 1 146 hours were limited
  on the day-ahead basis (1 240). It now also says that the intraday replica fails its scorecard.
- **A real export could describe another network and still pass the check.** The check now stops
  an export whose cable types the library does not know (named as a NIS names them, without r/x/c),
  whose lengths read like kilometres, whose coordinates are SWEREF 99 TM metres, or that leaves a
  required value blank or writes a word where a number goes; each says what to do. A blank fuse no
  longer passes the check to fail later in the build. *New project* offers one way out for unknown
  cable types: build them with the site's default cable, which the check then reports as a warning.
- **On a network where every house fits, the screens said what is only true of the reference
  network.** The verification marked "Fail" next to 0 hours above a limit without control, the
  sentence under it named the transformer as the binding limit, the business case priced deferring
  a transformer swap nobody needs, the curtailment curve piled its labels on one point, a request
  called the grid "full for that method", and the status bar called the imported network, weather
  and prices real. Each is now drawn from the project's own figures: 0 hours passes; the binding
  limit is the one broken in the most hours, or none; there is no reinforcement case when the
  envelopes add nothing over the hourly study or a bigger transformer adds no capacity
  (`reinforcement_no_gain`, `reinforcement_not_binding`); equal marks share one label; and the status
  bar says that the weather and prices are the reference site's and which cables the build assumed.
- **A project built on assumptions looked ready like any other.** The build counted each fallback
  (a cable built as the default type, the transformer's impedance from the contract's default) and
  no screen read the count. The project's card now lists what the build assumed, with how many
  elements each touches and the type names a default cable stood in for; a project whose cables or
  transformer rest on an assumption says "Ready, with assumptions" in the start window, and the
  output window lists them when its build ends. `GET /datasets` and `/meta` carry them as
  `assumptions`.
- **A CSV saved by older versions of Excel (Windows-1252) ended in a traceback**, and in the browser
  its å, ä and ö turned into replacement characters without a word. Such a file is now read as
  Windows-1252, with a note in the report, by the command and by the browser alike.
- **POST /check and a new project read every CSV with commas and decimal points**, so a semicolon
  export from Swedish Excel, which `kapacitet check` read correctly, came out wrong in the browser.
  Every reader of the contract now reads a file the same way.
- The full test run with data failed in `tests/test_mv_band.py` depending on the order of the
  modules: a test server's warm-up, still loading the reference network, filled the caches while
  the next module read the tiny grid. The warm-up now stops once the caches are emptied under it
  (`KAPACITET_WARM=0` turns it off, as the tests do), the spot prices are kept per downloads folder,
  and a weekly workflow (`data-suite`) runs the whole suite on the published data.

### Added

- **The hourly study's deployment on the map.** `GET /api/v1/hourly` gives the median order's
  hourly-study deployment, its verification over every weather year and what the next house would
  break, and `/state/hourly` and `/state/hourly_static` its two states. A network built from now on
  writes them (`results/hourly.json`); for one built before, the server computes them the first time
  and keeps them in `requests/cache/hourly/`, so no data bundle had to change.

- `tools/envelope_networks.py` and `docs/envelopes-by-network.md`: the capacity stages and the
  intraday scorecard on three benchmark networks at two MV voltages, with the decision rule
  committed before any case was computed. **No case passes it:** what can be issued connects less
  solar than the hourly study without control in all six, with no hour above a limit. The
  document has the tables and what follows; the README says it beside the intraday scorecard.
- `tools/study_scale.py` and `docs/study-scale.md`: a synthetic study of any number of stations and
  the time and disk of each. Run with three stations to see that it works; the measurement of 50
  has not been made, and the page carries no figure until it is.
- `SECURITY.md`, and the paragraphs of elmarknadslagen (2026:1281) that replace ellagen's on
  1 January 2027 beside the conditions of Ei2025:01.
