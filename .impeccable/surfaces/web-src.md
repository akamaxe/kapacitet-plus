---
version: 1
slug: "web-src"
primary_target: "web/src"
related_targets: []
---

## Scope

The whole Kapacitet+ interface (`web/src`): the shell and its six screens. Visitor mode: **Operate**. The
work is code-led.

## Audience and job

- The DSO connection planner decides whether a connection fits and on what terms.
- The applicant judges a non-firm offer.
- Seen on the author's laptop (1536×730), projected in the seminar, and by teammates on their own.
- The most frequent jobs:
  - read the static-vs-envelopes answer;
  - move the hour in the neighbourhood;
  - pick a house and evaluate a request.

## User decision (2026-10-30)

After seeing the built line map, the user chose to see the network as before: on the real map, in
3D, with interaction. This pinned decision overrides the direction's first signature. The line map,
the Neighbourhood lines view and the line strip were removed. The opening screen now shows
`NetworkMap.tsx`: the Smedslätten map tilted in 3D, the houses coloured by connection (firm green,
envelope amber, no solar grey), the cables in ink and the substation ringed in SL blue. Drag moves
and turns the map; the wheel zooms only with Ctrl, so the page still scrolls. A house opens its
connection request. The rest of the world stands: the SL-blue top bar, the white ground, Familjen
Grotesk, the line colours kept for data, and every usability change.

## Direction contract

THESIS: The neighbourhood grid is a line network. Eleven feeders leave the substation like metro lines and
every house is a station on its line. The surface refuses the energy-dashboard default (dark control room, KPI
tiles, neon map). It also refuses the drawing-set costume it replaces.

OWN-WORLD:
- **Ground and chrome:** a white ground and an SL-blue top bar carrying the six screens.
- **Lines:** ink of one fixed weight, bent only at 45°, with square badges carrying the feeder number.
- **Stations:** dots coloured by status: firm green, envelope amber, no solar hollow. Red only above rating.
- **Type:** Familjen Grotesk, a Stockholm studio's grotesk, with tabular figures.
- **Structure:** no boxed cards. Hierarchy comes from type size, weight and colour fields. Hairlines appear
  only in tables and data sheets.

STORY: At a glance the viewer sees that the same grid takes 413 → 787 kW and which houses ride on envelopes.
They can then test one connection and read its verdict with the hours behind it.

FIRST VIEWPORT (Static vs envelopes, 1536×730; the left column is now the 3D network map, see the user decision above):
- **Top bar:** wordmark, scope, the six screens, search, language and theme.
- **Left, about 60 %:** the 11 feeders as a schematic line diagram from the transformer symbol, with the key
  under it.
- **Right, about 40 %:**
  - the h1;
  - 413 kW → 787 kW at 40 px and the ×1.9 line;
  - two bars and a four-row data sheet;
  - "Test a connection" in SL blue, the one primary action.
- **Foot line:** the real/synthetic legend.

FORM: Grounded candidate 4 of 7, Stockholm public transport graphics (the tunnelbana line map, the line strip
above train doors). seed:9fb9a2a8671f2354.

Raises from the declined challengers:
- **Nothing disappears, it cancels** (ticket wallet): a stale request result is struck through and names the
  input that changed.
- **No boxes** (Metro tiles): type and colour fields carry the hierarchy.
- **Exact duration** (labanotation): every curtailed hour is drawn at its true length in the year strip.
- **Colour quarantined in the data** (lexicon): line and status colours appear only in the network, charts and
  map. The chrome is ink and SL blue.
- **One axis** (type specimen): the hour moves every reading on a screen at once, with a live readout.

Signature interaction: the line diagram is live.
- On Static vs envelopes it shows each house's connection status.
- On Neighbourhood it takes the loading colour of the selected hour.
- In the request panel and the house inspector, the chosen house's feeder appears as a line strip, like the one
  above train doors: every station of that line in order of distance, the chosen one marked.

Motion grammar:
- Lines and stations change colour in place: 120 ms, and none during playback.
- The palette and dialogs open without animation.
- The notes drawer slides in over 240 ms.
- Presses scale to 0.97.
- Reduced motion keeps only opacity and colour.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict,
DESIGN.md, and every shipping raster carrying its provenance

## Memorable moment

The first screen: the real network drawn as a line map, with every station coloured by whether that house's
solar is firm or rides on an envelope. It sits beside the 413 → 787 kW answer.

## Unresolved

- **The night theme.** The same world after dark: deep ground, the same line colours lifted. It still has to be
  measured.
- **Schematic layout.** Feeders are assigned to 45° directions by their real bearing from the substation, and
  houses are ordered by distance along the feeder. Branches are simplified to one line per feeder, and the
  interface labels the diagram as schematic.
- **Figures.** Familjen Grotesk is used if its tabular figures hold. Otherwise a second face is kept for
  figures only.
