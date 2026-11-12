---
version: 1
slug: "web-src"
primary_target: "web/src"
related_targets: []
---

## Scope

The whole Kapacitet+ interface (`web/src`): the start window, the workbench frame and its eight screens,
and the applicant's page (`#/apply`), which `main.tsx` loads instead of the application. Visitor mode:
**Operate**. The work is code-led.

## Audience and job

- The DSO connection planner opens a project (one low-voltage network with its studies), decides whether a
  connection fits and on what terms, decides the applications the DSO's rule passes to a person, issues each
  day's limits, and files the year's report and the evidence for Ei. They know PowerFactory, ETAP or PVsyst,
  and expect to find a project where those tools put one.
- The applicant judges a non-firm offer. A household does it on its own page and follows its application
  by a reference code.
- Reference viewport 1536×730 (a laptop at 125 %); also projected in meetings.
- The most frequent jobs:
  - open a project, or make one from a network export or an example network;
  - read the static-vs-envelopes answer;
  - move the hour in the neighbourhood;
  - pick a house and evaluate a request;
  - work through the inbox and issue a day's limits.

## User decisions

**2026-10-30.** After seeing the built line map, the user chose to see the network as before: on the real
map, in 3D, with interaction. This pinned decision overrides the direction's first signature. The line map,
the Neighbourhood lines view and the line strip were removed. The rest of the world stands: the SL-blue top
bar, the white ground, Familjen Grotesk, the line colours kept for data, and every usability change.

**2026-11-02.** The second round, chosen by the user, added the Operation screen (key 5, between the request
and the annual report) and the applicant's page. The top bar now folds by measuring whether the eight
screens fit in each language, not at a fixed width. Settled since the first contract: the night theme
shipped with its contrast measured (DESIGN.md), the schematic layout went with the line map, and Familjen
Grotesk's tabular figures held, so no second face was needed. The user asked for this brief to follow the
shipped interface.

**2026-11-12.** The user asked for the interface of electrical engineering software, naming DIgSILENT
PowerFactory, ETAP and PVsyst: what one sees on entering is a start window where a project is made or
opened (the 120-house network is one), and the program itself has that kind of frame. Asked how far, they
chose the whole workbench: a menu bar, a project tree, an output window and a status bar, with the eight
screens as the project's modules. Asked about the look, they kept the identity: the SL-blue bar, the white
ground, Familjen Grotesk and the 3D map, with the final logo (the K with the plus) in the bar, the start
window and the icon. A new project comes from a network export (CSV), an example network or a project
file, and the application can be installed with a window of its own. This pinned decision replaces the top
bar that carried the eight screens and the foot line; the screens themselves stand as they are.

## Direction contract

THESIS: The interface is a power-engineering workbench drawn like Stockholm's public transport graphics
around the real place: a project is opened from a start window, and inside it an SL-blue menu bar, a
project tree, an output window and a status bar frame the Smedslätten network on its real map in 3D. It
refuses the energy-dashboard default (dark control room, KPI tiles, neon map) and the costume of a 1990s
desktop program (grey bevels, icon ribbons, windows inside windows).

OWN-WORLD:
- **Ground and chrome:** a white ground, with a night theme as the alternative. One SL-blue bar, 44 px,
  carries the logo, the menus (File, View, Study, Results, Tools, Help), the open project's name and the
  search. Everything else in the frame is ink on the ground, parted by hairlines: the project tree on the
  left, the output window and the status bar at the foot. The applicant's page keeps only the bar and the
  real/synthetic strip.
- **The network:** the real map tilted in 3D, houses raised by their floors and coloured by connection
  (firm green, envelope amber, no solar grey), the synthetic cables in ink, the substation ringed in SL
  blue. Red only above rating.
- **Type:** Familjen Grotesk, a Stockholm studio's grotesk, for everything, with tabular figures.
- **Structure:** no boxed cards. Hierarchy comes from type size, weight and colour fields. Hairlines appear
  in tables and data sheets and between regions, blocks, steps and the frame's docks, never as decoration.
  A project list is a table, a project's facts a data sheet, a new project three numbered steps.
- **States:** the model's verdicts carry a status mark with its word; what is measured, awaited or does not
  apply is a muted word with no mark. A project says its state the same way: in use, ready, being built
  (with its step), failed.
- **The mark:** the K whose arms open for the plus, in the bar and on the start window; the plus is the
  one place the brand's orange appears.

STORY: The viewer enters at the start window, sees the projects on this machine and opens one, or makes one
from a network export, an example network or a project file. Inside, the tree says what the project holds
and the status bar which project it is. They see that the same grid takes more solar than the static study
allows and which houses ride on envelopes, test one connection, read its verdict with the hours behind it,
decide it, and find what the engine did in the output window.

FIRST VIEWPORT (the start window, 1536×730):
- **Menu bar:** the logo, File, View, Tools and Help, and the search.
- **Left, 280 px:** the start column: "New project…", "Open project file…", then help (the tour, the keys,
  the manual, about).
- **Right:** the h1 "Projects" with its search; the projects as a table (name, customers, transformer,
  results), the one in use marked; under a hairline the selected project's data sheet (network, weather
  years, capacity of the static and the hourly study, when it was computed) and its actions, with "Open
  project" in SL blue, the one primary action.
- **Status bar:** the server's connection and version, the real/synthetic legend, language and keys.

FORM: Grounded candidate 4 of 7, Stockholm public transport graphics (the tunnelbana line map, the line strip
above train doors). seed:9fb9a2a8671f2354. The workbench topology (start window, menu bar, project tree,
output window, status bar) is pinned by the user, from the tools the audience works in every day; the form
supplies its drawing, never their look.

Raises from the declined challengers:
- **Nothing disappears, it cancels** (ticket wallet): a stale request result is struck through and names the
  request it answered.
- **No boxes** (Metro tiles): type and colour fields carry the hierarchy.
- **Exact duration** (labanotation): every curtailed hour is drawn at its true length in the year strip.
- **Colour quarantined in the data** (lexicon): line and status colours appear only in the network, charts and
  map. The chrome is ink and SL blue.
- **One axis** (type specimen): the hour moves every reading on a screen at once, with a live readout.

Signature interaction: the network map is live.
- On Static vs envelopes it shows each house's connection status; dragging moves and turns it, and the
  wheel zooms only with Ctrl, so the page still scrolls.
- On Neighbourhood it takes the selected hour: houses by voltage or net power, cables by their loading.
- A house opens its connection request, and the chosen house stays in the URL. The same house can be
  reached from the project tree, under its feeder.

Motion grammar:
- Map colours change in place with no transition, and none during playback.
- The palette, the menus and dialogs open without animation; the tree and the output window open and
  close in place.
- Presses scale to 0.97.
- Reduced motion keeps only opacity and colour.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict,
DESIGN.md, and every shipping raster carrying its provenance

## Memorable moment

Opening the project: from a table of projects to the real Smedslätten network in 3D, every house coloured
by whether its solar is firm or rides on an envelope, with the project's tree beside it.

## Unresolved

- **The Swedish copy.** The interface is also in Swedish, which has not yet been reviewed by a native
  speaker; a Swedish-speaking DSO engineer should check it before it is shown to one (`docs/glossary.md`).
- **One primary per view on a long screen.** Operation carries one primary per section ("Issue the file",
  "Try a synthetic day"); whether a section of a long screen counts as a view is not decided.
- **What the opening study says.** The experiment of `docs/envelopes-by-network.md` found no network where
  the issuable envelopes beat the hourly study; what that changes on the screens is the owner's decision
  and is not taken in this frame.
