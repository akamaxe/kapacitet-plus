---
version: 1
slug: "web-src"
primary_target: "web/src"
related_targets: []
---

## Scope

The whole Kapacitet+ interface (`web/src`): the shell and its eight screens, and the applicant's page
(`#/apply`), which `main.tsx` loads instead of the application. Visitor mode: **Operate**. The work is
code-led.

## Audience and job

- The DSO connection planner decides whether a connection fits and on what terms, decides the
  applications the DSO's rule passes to a person, issues each day's limits, and files the year's report
  and the evidence for Ei.
- The applicant judges a non-firm offer. A household does it on its own page and follows its application
  by a reference code.
- Reference viewport 1536×730 (a laptop at 125 %); also projected in meetings.
- The most frequent jobs:
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

## Direction contract

THESIS: The interface is drawn like Stockholm's public transport graphics around the real place: a white
ground, an SL-blue bar, ink type and the line colours kept for the data, with the Smedslätten network on
its real map in 3D and every house coloured by how its solar is connected. It refuses the energy-dashboard
default (dark control room, KPI tiles, neon map) and the drawing-set costume it replaced.

OWN-WORLD:
- **Ground and chrome:** a white ground, with a night theme as the alternative, and an SL-blue top bar
  carrying the eight screens, which fold into a menu sheet when they do not fit. The applicant's page keeps
  only the bar and the real/synthetic strip.
- **The network:** the real map tilted in 3D, houses raised by their floors and coloured by connection
  (firm green, envelope amber, no solar grey), the synthetic cables in ink, the substation ringed in SL
  blue. Red only above rating.
- **Type:** Familjen Grotesk, a Stockholm studio's grotesk, for everything, with tabular figures.
- **Structure:** no boxed cards. Hierarchy comes from type size, weight and colour fields. Hairlines appear
  in tables and data sheets and between regions, blocks and steps, never as decoration.
- **States:** the model's verdicts carry a status mark with its word; what is measured, awaited or does not
  apply is a muted word with no mark.

STORY: At a glance the viewer sees that the same grid takes 413 → 787 kW and which houses ride on
envelopes. They can then test one connection, read its verdict with the hours behind it, decide it, and
see what issuing the limits the day before costs.

FIRST VIEWPORT (Static vs envelopes, 1536×730):
- **Top bar:** wordmark, scope, the eight screens, search, notes, language and theme.
- **Left, about 60 %:** `NetworkMap.tsx`, the Smedslätten network on the real map in 3D, with its key.
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
- A house opens its connection request, and the chosen house stays in the URL.

Motion grammar:
- Map colours change in place with no transition, and none during playback.
- The palette and dialogs open without animation.
- The notes drawer slides in over 240 ms.
- Presses scale to 0.97.
- Reduced motion keeps only opacity and colour.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict,
DESIGN.md, and every shipping raster carrying its provenance

## Memorable moment

The first screen: the real Smedslätten network in 3D, every house coloured by whether its solar is firm or
rides on an envelope, beside the 413 → 787 kW answer.

## Unresolved

- **The Swedish copy.** The interface is also in Swedish and nobody in the team can review it; a
  Swedish-speaking DSO engineer should check it before it is shown to one (`docs/glossary.md`).
- **One primary per view on a long screen.** Operation carries one primary per section ("Issue the file",
  "Try a synthetic day"); whether a section of a long screen counts as a view is not decided.
