# Changelog

What changed for someone who runs Kapacitet+ or reads its figures. The version is still 0.1.0 and
nothing here is a release: `main` is the supported version, and the commit history has the rest.
Changes before 12 November 2026 are only in that history.

## Unreleased

### Added

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

### Changed

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

### Added

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
