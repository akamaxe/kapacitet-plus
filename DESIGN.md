---
name: Kapacitet+
description: A digital twin of one Stockholm low-voltage neighbourhood, its network on the real map.
colors:
  ground: "#ffffff"
  well: "#f2f4f6"
  rule: "#e1e5e9"
  rule-strong: "#858e97"
  ink: "#16202b"
  ink-2: "#3d4752"
  ink-3: "#56606b"
  sl: "#123e7c"
  sl-deep: "#0c2d5c"
  sl-wash: "#e4ecf7"
  on-sl: "#ffffff"
  sl-ink-2: "#c9d6ea"
  sl-chrome: "#123e7c"
  green: "#1f7a44"
  green-ink: "#22703a"
  green-wash: "#e1f0e5"
  amber: "#c48405"
  amber-ink: "#8a5800"
  amber-wash: "#fbeed2"
  red: "#d8352a"
  red-ink: "#b3261e"
  red-wash: "#fbe2df"
  blue-line: "#2b67b0"
  pencil: "#8a939b"
  cat-hydro: "#24508a"
  cat-nuclear: "#8e5cb0"
  cat-wind: "#2e8d7f"
  cat-solar: "#b07818"
  cat-thermal: "#8b3a28"
  cat-other: "#b4b9be"
  load-0: "#b5c3bb"
  load-50: "#3d8f58"
  load-85: "#c48405"
  load-over: "#d8352a"
  volt-low: "#3d6fb0"
  volt-mid: "#7b8389"
  volt-high: "#d2782f"
  ground-night: "#101317"
  raised-night: "#1c2127"
  well-night: "#171b20"
  rule-night: "#2a2f35"
  rule-strong-night: "#727c86"
  ink-night: "#eceff2"
  ink-2-night: "#b7c0c9"
  ink-3-night: "#959fa9"
  sl-night: "#86aee6"
  sl-deep-night: "#a5c3ee"
  sl-wash-night: "#1b2c44"
  on-sl-night: "#0b1220"
  sl-ink-2-night: "#b4c6e0"
  sl-chrome-night: "#0f2d57"
  green-night: "#3fa868"
  green-ink-night: "#74c891"
  green-wash-night: "#173021"
  amber-night: "#e7b04b"
  amber-ink-night: "#e9b65a"
  amber-wash-night: "#352a12"
  red-night: "#ef6b5e"
  red-ink-night: "#f3887d"
  red-wash-night: "#3a1b18"
  blue-line-night: "#6f9fdc"
  pencil-night: "#737c85"
  cat-hydro-night: "#5b9be0"
  cat-nuclear-night: "#d46fa8"
  cat-wind-night: "#7fd0b8"
  cat-solar-night: "#e0a93b"
  cat-thermal-night: "#c05a3a"
  cat-other-night: "#4a5258"
  load-0-night: "#33433b"
  load-50-night: "#4fa16b"
  load-85-night: "#e7b04b"
  load-over-night: "#ef6b5e"
  volt-low-night: "#5b8bd0"
  volt-mid-night: "#cfcbc3"
  volt-high-night: "#e08a45"
  print-paper: "#ffffff"
  print-ink: "#000000"
  print-muted: "#4a4a4a"
  print-rule: "#9a9a9a"
typography:
  display:
    fontFamily: "Familjen Grotesk Variable, Familjen Grotesk, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "2.5rem"
    fontWeight: 700
    lineHeight: 1.05
    letterSpacing: "-0.022em"
    fontFeature: "tnum"
  headline:
    fontFamily: "Familjen Grotesk Variable, Familjen Grotesk, system-ui, sans-serif"
    fontSize: "1.625rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.018em"
  title:
    fontFamily: "Familjen Grotesk Variable, Familjen Grotesk, system-ui, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  title-small:
    fontFamily: "Familjen Grotesk Variable, Familjen Grotesk, system-ui, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 600
    lineHeight: 1.35
    letterSpacing: "-0.005em"
  body:
    fontFamily: "Familjen Grotesk Variable, Familjen Grotesk, system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "tnum"
  label:
    fontFamily: "Familjen Grotesk Variable, Familjen Grotesk, system-ui, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 500
    lineHeight: 1.45
  caption:
    fontFamily: "Familjen Grotesk Variable, Familjen Grotesk, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.4
rounded:
  sm: "4px"
  md: "8px"
  lg: "12px"
spacing:
  "1": "4px"
  "2": "8px"
  "3": "12px"
  "4": "16px"
  "5": "24px"
  "6": "32px"
  "7": "48px"
  "8": "64px"
components:
  button-primary:
    backgroundColor: "{colors.sl}"
    textColor: "{colors.on-sl}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "0 16px"
    height: "40px"
  button-primary-hover:
    backgroundColor: "{colors.sl-deep}"
    textColor: "{colors.on-sl}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "0 16px"
    height: "40px"
  button-ghost-hover:
    backgroundColor: "{colors.well}"
  button-ghost-danger:
    backgroundColor: "transparent"
    textColor: "{colors.red-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "0 16px"
    height: "40px"
  button-small:
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "32px"
  icon-button:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.sm}"
    size: "40px"
  segmented:
    backgroundColor: "{colors.well}"
    rounded: "{rounded.md}"
    padding: "4px"
  segmented-option:
    textColor: "{colors.ink-2}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "4px 12px"
    height: "32px"
  segmented-option-selected:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.ink}"
  top-bar:
    backgroundColor: "{colors.sl-chrome}"
    textColor: "{colors.sl-ink-2}"
    height: "56px"
    padding: "0 24px"
  top-bar-link-current:
    textColor: "{colors.on-sl}"
  foot-line:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.ink-3}"
    typography: "{typography.caption}"
    height: "30px"
  side-panel:
    backgroundColor: "{colors.ground}"
    width: "460px"
  side-panel-wide:
    backgroundColor: "{colors.ground}"
    width: "560px"
  section-bar:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.ink-2}"
    typography: "{typography.label}"
    height: "44px"
  data-sheet-row:
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    padding: "8px 0"
  field:
    backgroundColor: "{colors.well}"
    rounded: "{rounded.md}"
    padding: "12px 16px"
  search-field:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "40px"
  command-line:
    backgroundColor: "{colors.well}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "4px 8px"
  tour-card:
    backgroundColor: "{colors.sl-wash}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.md}"
    padding: "16px 24px"
    width: "380px"
  popover:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
    padding: "8px 12px"
  dialog:
    backgroundColor: "{colors.ground}"
    rounded: "{rounded.lg}"
---

# Design System: Kapacitet+

## Overview

**Creative North Star: "The Network on the Map, in Stockholm's Colours"**

The neighbourhood grid is shown where it is: the real Smedslätten map in 3D, the houses raised by their floors and coloured by how their solar is connected, the synthetic cables in ink and the substation where OpenStreetMap places it. Around it, the interface takes its colours from Stockholm's public transport: a white ground, ink type, one SL-blue top bar carrying the eight screens, and the line colours (green, amber, red) kept for the data. It is a working tool for a DSO connection planner, who answers requests, decides them in an inbox and a queue, issues each day's limits and files the year's report, and for the applicant on the other side of the same connection, who has a page of their own. Hierarchy comes from type size, weight and colour fields, not from boxes; hairlines appear in tables, data sheets and between regions, and nowhere as decoration. (The redesign first drew the network as a schematic metro-line diagram; the user chose the 3D map instead, and that decision overrides the direction's first signature.)

It is dense where the work is (the Neighbourhood instrument, the request form, the Annual report's tables) and calm where the answer is (Static vs envelopes, one figure at display size). It reads from a projector in a lit room and on a 1536×730 laptop, and it holds on a phone. Night is a designed second theme, not an inversion: the same roles on a deep ground, the line colours lifted, the chrome kept a deep SL blue.

**Key Characteristics:**
- White ground, ink type, SL blue only on the chrome, the one action, selection, links and focus.
- Line colours live in the data: green firm, amber envelope or near a limit, red only above a rating.
- One typeface, Familjen Grotesk, with its default tabular figures for every reading.
- No boxed cards: regions part by hairlines and space; only floating layers cast a shadow.
- The network on the real map in 3D, coloured by connection, is the signature on three screens; every number on it comes from the API.
- Real data is drawn solid, synthetic data dashed, on every screen.
- One way to write money (SEK), dates (`1 Nov 2026, 14:03`) and shares, on screen and on paper.

## Colors

A white sheet with ink and one public-transport blue, and three signal colours that only appear where the data is.

`web/src/styles/tokens.css` holds primitives named after this world (`--ground`, `--ink`, `--sl`, `--green`…) and, once, the role tokens that components read (`--bg-base`, `--text-primary`, `--accent`…). A theme swaps primitives only. Components, `lib/colors.ts` and the maps read role tokens; nothing outside `tokens.css` holds a raw colour. The frontmatter keys without a suffix are day; `-night` keys are the night primitives of the same name.

### Primary
- **SL Blue** (`sl`; role `--accent`, `--focus`): the one primary action per view ("Test a connection"), links, the selected house, focus rings, the substation ring on the opening map, and the open entry of the evaluation history. 10.5:1 on white by day, 8.2:1 on the night ground. Its hover step is **SL Deep** (`sl-deep`, `--accent-hover`); selected rows, palette items, the current menu entry and the tour card sit on **SL Wash** (`sl-wash`, `--accent-quiet`, accent on it 8.8:1 day, 6.2:1 night). Text on the accent is **On SL** (`on-sl`, `--on-accent`): white by day, near-black at night, because the night accent is a lifted light blue.
- **Chrome Blue** (`sl-chrome`; role `--chrome`): the top bar. It equals SL Blue by day and stays a deep blue (`sl-chrome-night`) at night instead of following the lifted accent. Chrome text is white (`--chrome-ink`, 10.5:1 day, 13.7:1 night) and **Chrome Mist** (`sl-ink-2`, `--chrome-ink-2`, 7.1:1 / 7.9:1) for the inactive links and tools.

### Secondary (the line colours, data only)
| Role token | Primitive | Job | Text on ground (day / night) |
|---|---|---|---|
| `--good-fill`, `--series-dynamic` | `green` | firm houses on the maps, the envelope (dynamic) series, the "with envelopes" bar, energy delivered in the envelope, the live mark on Sweden | 5.4 / 6.2 (graphic) |
| `--good` | `green-ink` | the ratio line, pass icons, good outcomes as text (a firm decision in the queue, the decided check) | 6.1 / 9.2; 5.2 / 7.0 on its wash |
| `--good-quiet` | `green-wash` | quiet good fields | |
| `--warning-fill` | `amber` | houses under an envelope on the maps, a meter at or above 85 % of its limit, curtailed energy in a chart, the strong end of the envelope heatmaps | 3.2 / 9.5 (graphic) |
| `--warning` | `amber-ink` | limited / non-firm text and icons (an envelope decision in the queue) | 6.0 / 10.0; 5.3 / 7.6 on its wash |
| `--warning-quiet` | `amber-wash` | the stale-answer note | |
| `--danger-fill` | `red` | above rating: a cable, the transformer, a meter past its limit | 4.7 / 6.2 (graphic) |
| `--danger` | `red-ink` | rejected, failed, a reading that is itself the bad news, the label of a destructive answer | 6.5 / 7.7; 5.3 / 6.4 on its wash |
| `--danger-quiet` | `red-wash` | error notes | |
| `--series-main` | `blue-line` | the one series of a single-series chart | 5.7 / 6.8 |
| `--neutral-series` | `pencil` | the static (worst-case) series and bar | 3.1 / 4.4 (graphic) |

The greens were chosen to part from the amber under protanopia (day ΔE 10.3, night ΔE 9.1, dataviz validator); the day amber graphic sits at 3.2:1 on white.

### Tertiary (data palettes)
- **Production mix** (`--cat-1`…`--cat-6`: hydro, nuclear, wind, solar, thermal, other), categorical, at most six, in that fixed order. "Other" is deliberately pale by day and dark at night, so it parts from the rest by lightness; it sits below 3:1, so its chart always keeps a legend and a table view.
- **Production / consumption** (`--data-production`, `--data-consumption`): the hydro blue and the solar ochre, on the Sweden screen and its zone balance.
- **Loading** (`--load-0`, `--load-50`, `--load-85`, `--load-over`): sequential, stops at 0, 50 and 85 % of the rating, held flat from 85 to 100 %, then one flat red above the rating. Exceeding a rating is binary, never a gradient.
- **Voltage** (`--volt-low`, `--volt-mid`, `--volt-high`): diverging around 1.00 pu, blue below and orange above, saturating at ±6 %. Never red and green. Net power at a house uses the same poles (import blue, export orange) so the two share one legend bar.
- **Balance** (`balanceColor`): a zone's balance from consumption ochre through the page colour to production blue.

The loading and voltage stops stay plain `#rrggbb` because `lib/colors.ts` interpolates them.

**Heatmaps.** Two palettes, by what the cell holds. A house's lowest export limit by month and hour (the envelope heatmap on the request answer and in the Annual report's house record) is one amber ramp, `--warning-fill` at full strength for 0 kW fading to 30 % opacity at the system's own capacity: the lower the limit, the stronger the amber. The ramp is the one amber's opacity, not amber to its wash, because the wash is as light as the page and the well and its end of the scale would vanish. Only cells below that capacity are drawn, square-cornered and parted by 2 px of the page colour. The transformer's loading through the year (Static vs envelopes, "When") takes the loading palette in its own pieces: 0–50, 50–85, 85–100 % and the flat red above 100 %. Its other view, how many houses are held back in each hour, counts a limit, so it takes the same opacity ramp the other way round: 35 % for one house, full amber for the most.

### Print
The printed record is black on white whatever the screen theme: **Paper** (`print-paper`), **Print Ink** (`print-ink`) for text, the brand and the head rule, **Print Muted** (`print-muted`) for labels and notes, **Print Rule** (`print-rule`) for table rows and the foot rule. No other colour reaches paper.

### Neutral
| Role token | Primitive | Job | Day / night |
|---|---|---|---|
| `--bg-base`, `--bg-surface` | `ground` (`sheet`) | the page and side panels: the same white, parted by a hairline | |
| `--bg-elevated` | `raised` | dialogs, popovers, tooltips, the chosen segment, the house search and decision-note fields, the offline-map note | white / `raised-night` |
| `--bg-inset` | `well` | segmented tracks, range fields, the dense well band, skeletons, meter tracks, a command to copy | |
| `--border` | `rule` | every hairline; any border drawn without a colour takes it | |
| `--border-strong` | `rule-strong` | ghost-button edges, houses with no solar on the maps, meter ticks | 3.3 / 4.4 on ground |
| `--text-primary` | `ink` | headings, values | 16.5 / 16.1 |
| `--text-secondary` | `ink-2` | body, labels | 9.5 / 10.1 |
| `--text-muted` | `ink-3` | captions, sources, units | 6.4 / 6.9 (5.8 / 6.4 on a well) |

### Named Rules
**The Quarantine Rule.** Green, amber and red appear only in the network, charts, maps, meters and status marks. The chrome is ink and SL blue. A button, a heading or a field is never green, amber or red. One exception: the destructive answer in a confirm dialog is a ghost button with its label in `--danger`, never filled, so it never reads as the recommended one.

**The One Blue Rule.** SL blue is the action and the selection, never a data value. A chart series never takes `--accent`; the selected house does.

**The Pencil Rule.** The static study is never red. It is conservative, not wrong: it is pencil grey beside the envelope green, in one comparison.

**The Token Rule.** No colour, size, spacing value, radius or duration is written outside `tokens.css`. A missing value is added there first, as a primitive and a role.

**The Comma Tint Rule.** `--night-tint` is three comma-separated channels, so alpha over it is written `rgba(var(--night), a)`: the dialog scrim at 0.38, MapLibre's gesture notice at 0.55. `rgb(var(--night) / a)` is invalid there and paints nothing. The same holds for any tint token written as channels.

## Typography

**Display Font:** Familjen Grotesk Variable (with system-ui, -apple-system, Segoe UI, sans-serif)
**Body Font:** the same
**Label/Mono Font:** none; identifiers and readings use the same face

**Character:** A Stockholm studio's grotesk, compact and plain, with tabular default figures, so one face carries words, identifiers and every reading without a monospace.

### Hierarchy
The scale steps about 1.2 on a 15 px body (rem on a 16 px root).
- **Display** (700, `--text-display` 2.5rem, 1.05, −0.022em): the one answer figure per screen ("787 kW" on Static vs envelopes; the curtailed share of export on the Annual report). Nothing else is set at this size. Falls to h1 size below 700 px.
- **Headline** (600, `--text-h1` 1.625rem, 1.2): the screen title; also the "from" figure of the answer pair and the verdict figure. A map screen's h1 sits in its side panel one step down (`--text-h2`).
- **Title** (600, `--text-h2` 1.25rem, 1.3): a region heading; also the lead answer sentence under a title (at 500).
- **Title small** (600, `--text-h3` 1.0625rem, 1.35): a sub-heading in a region or panel, the hour verdict, the inspector title, the decision and live-strip heads, the tour card's title, the brand name (700).
- **Body** (400, `--text-body` 0.9375rem, 1.5): prose, data-sheet values (500).
- **Label** (500, `--text-small` 0.8125rem, 1.45): data-sheet labels, buttons, navigation, legends, table cells.
- **Caption** (400, `--text-caption` 0.75rem, 1.4): sources, units beside values, axis ticks, notes, the foot line.

Weights: 400 body, 500 labels, buttons and values, 600 headings, 700 only for the brand name and the display figure. Headings use `text-wrap: balance`, prose `pretty`; captions stop at 72ch, lead lines at 46–60ch.

### Numbers and units
- `font-variant-numeric: tabular-nums` is set on the body and restated on every reading, so values do not jitter while the hour moves.
- Every number goes through `lib/format.ts`: thousands are grouped with a narrow no-break space in all three languages (`2 503 kWh`), the decimal mark follows the language (en-GB, es-ES and sv-SE formatting through `locale()` in `lib/i18n/core.ts`), and decimals are fixed per figure (power 0–1, percentages 1, voltage 3 in pu, prices 2–3). Never more precision than the data supports. A missing value is an en dash.
- On the request answer and its printed record, shares read to one decimal (`pct1` in `lib/format.ts`), the risk figures included; a non-zero share that would round to 0.0 reads `< 0.1` instead. Only a histogram's bin edges keep two decimals, since neighbouring bins can sit closer than 0.1.
- The unit sits beside the value at caption size in `--text-muted` (`kW`, `%`, `pu`, `kWp`); on a chart it appears once, in the axis name.
- Inside a sentence the figure and its unit never part: when a dictionary loads, `lib/i18n/core.ts` binds every placeholder to the unit after it (`{mean} %`, `{h} h`, `{kw} kW`, `{x} SEK`, `{v} pu`) with a no-break space, so no line starts with a unit. Strings are written with an ordinary space; the binding is automatic.
- A list inside a sentence is joined in the reader's language by `listJoin` (`lib/format.ts`, `Intl.ListFormat`, conjunction), never with a hand-written "and".
- A wait reads in the largest unit that stays readable (`duration`: `42 s`, `12 min`, `3.5 h`, `2.0 d`).
- An hour of the study year reads `19 May 2025, 11:00` (`hourLabel`), in the year's local standard time. A moment in real time (an evaluation saved, a decision taken, a model run) reads the same way through `format.dateTime`: medium date, short time, `1 Nov 2026, 14:03`, or the date alone. A reading taken today shows only its time, `14:03` (`format.clock`).
- A figure is said once per view: if the heading says it, the chart does not.

### Named Rules
**The SEK Rule.** Money is SEK, never "kr": `SEK/kWh` for prices, `SEK/yr` for a yearly sum (through `unit.sekYr` in `lib/i18n/`, "SEK/año" in Spanish, "SEK/år" in Swedish), and `kSEK` or `kSEK/yr` (`unit.ksekYr`) when a figure is shown in thousands.

**The One Date Rule.** Every real-time date on screen and on paper goes through `format.dateTime`, and every time of day through `format.clock`. No screen builds its own date string.

**The One Face Rule.** Familjen Grotesk sets everything, identifiers and figures included. No monospace, no second display face.

**The Three Levers Rule.** A step in the hierarchy changes size, weight and ink together; a label is small/500/ink-2, its value body/500/ink, its note caption/400/ink-3. Letter-spaced uppercase is not used anywhere, and every heading, button and label is sentence case.

## Layout

Base unit 4 px; every margin, padding and gap is a `--space-*` step (4, 8, 12, 16, 24, 32, 48, 64).

**The frame.** `.app` is a three-row grid: the top bar (`--topbar-h` 56 px), the screen, and the foot line (`--statusbar-h` 30 px). The window never scrolls; each screen scrolls inside `.page`, which is `position: relative` so absolutely placed children cannot escape it.

- **Top bar**: Chrome Blue, white type. Left: the roundel (a white disc with the plus of the name) and "Kapacitet+", then the site scope, read from `/meta` (site and bidding zone). Right of centre: the eight screens as text links in Chrome Mist, in key order (Sweden, Neighbourhood, Static vs envelopes, Connection request, Operation, Annual report, 2030 scenarios, Method & data), the current one white at 600 with a 3 px white rule on the bar's bottom edge. Whether they fit is measured, not set at a width: a hidden probe holds the eight labels at 600 with the bar's gaps, and the bar folds them into the menu sheet when the room left by the brand, the scope and the tools is narrower than the probe, in whatever language is on screen; it unfolds only with 24 px to spare, so a window resized across the edge does not flicker. Right: search (a tinted field with its "Ctrl K" key), notes, language, theme, full screen. Focus inside the bar is a white ring drawn within it.
- **Foot line**: on the ground with a hairline above, caption size, muted: the real/synthetic legend on the left, the reference year, the tour button and the key-sheet button on the right. The same on every screen; while the setup screen is shown there is nothing to tour, so the tour button is left out.
- **Section bar**: the four long document screens (Static vs envelopes, Operation, Annual report, Method & data) carry a sticky bar at the top of their scroller, `--toolbar-h` 44 px: sections as text links on the left (the one being read underlined 2 px in ink), the basis of the figures on the right in caption. The section being read is the one crossing a line a third of the way down the scroller, never above the bar; while a section with its own basis is read, the bar shows that basis instead of the screen's. It scrolls only `.page`, never the window. A region's controls stay in its own head row, never in the bar.

**Screen shapes.**
- *Map screens* (Neighbourhood, Connection request, Sweden): `.split`, the map on the left and a side panel of `--panel-w` (460 px) on the right, `--panel-w-wide` (560 px) for the request's wider panel, the panel on the ground with a hairline on its left. Anything placed against the panel's edge (the tour card) reads the same tokens. The Neighbourhood map column carries the year strip and the folding house table beneath it.
- *Document screens* (Static vs envelopes, Operation, Annual report, 2030, Method & data): `.page-inner`, max 1550 px, 32 px gutters (24 px at 1200 px and below), 24 px between regions. The setup screen is a document screen held to 760 px.
- *The opening answer*: a 1.2 : 1 two-column hero with the network map first and the answer beside it, both ending together. The Annual report opens the same way: title, the filed figure and its two downloads on the left, its data sheet in one column on the right; one column below 1100 px. Operation opens in the same frame: title, lead line, the day-ahead curtailed share of export at display size with its foresight counterpart in the words beside it, and a one-column data sheet on the right.

**Three weights inside a document screen.**
| Weight | How it looks | Carries |
|---|---|---|
| Region `.region` | no surface; parted from the next by one hairline and 32 px | the screen's own divisions, `h2` |
| Block `.block` | a hairline above and 16 px of space; no fill, no box | a self-contained instrument inside a region, `h3` |
| Well `.well` | the inset grey, hairlines above and below, bleeding to the gutter | one dense band per screen: a long table, a year of hours |

A region whose figures rest on something narrower than the screen's basis (one adoption order, one price span) says so in its head: a caption line at 500 in ink-2 under the region's caption (`.region-basis`), the same words the section bar shows while it is read.

**Responsive.** The top bar gives way in steps before it folds: at 1680 px and below the gaps between the screens tighten from 24 to 16 px, at 1600 px the scope goes, at 1440 px the search keeps only its icon (label and key hidden); the fold itself is measured (above), not a breakpoint. Between 1101 and 1350 px `--panel-w` and `--panel-w-wide` narrow to 390/450 px. At 1100 px split screens stack (map above, panel below), and two-column grids and the report hero go to one column; at 900 px the bar's sides and gaps tighten to 16/12 px and the foot line keeps one legend item; at 700 px (phone) the bar drops full screen, theme and language (the menu sheet holds the last two), the answer comes before the network map, the display figure drops to h1 size, the year strip shortens to 64 px, the map key keeps only its scales, the reinforcement comparison stacks its two sides, each protocol check reads in two lines (the evidence pack's in three) and the tour card spans the width; at 600 px each application in the inbox reads as one stacked row and the applicant's page tightens its sides to 16 px. On coarse pointers every control grows to 44 px.

## Elevation & Depth

Flat by default. Depth is a tonal step plus a hairline; a surface that sits in the page never has a shadow. Only what floats casts one, and it is always the same: a 1 px ring and one soft offset depth (`--shadow-popover`). Floating layers stack at `--z-sticky` 3, `--z-overlay` 40 (notes drawer, glossary popovers, the tour card), `--z-dialog` 50 (dialogs, palette, key sheet, menu sheet), `--z-toast` 60. The dialog scrim is the night tint at 38 % (the Comma Tint Rule).

### Shadow Vocabulary
- **Float** (`box-shadow: 0 0 0 1px rgba(22,32,43,.08), 0 10px 28px -8px rgba(22,32,43,.22)`; night `0 0 0 1px rgba(255,255,255,.08), 0 14px 36px -10px rgba(0,0,0,.65)`): dialogs, the palette, toasts, the notes drawer, glossary popovers, chart and map tooltips, the "updating" note.

### Named Rules
**The Only-What-Floats Rule.** The map key, the segmented control's chosen option, MapLibre's controls, the offline-map note and the tour card are edged with a hairline, never shadowed; the tour card sits beside the screen on the SL wash rather than floating over it. No glows, no accent-coloured shadows, no decorative gradients; the only gradients are colour-scale keys drawn from the same function as the marks they explain.

## Shapes

Small, even corners: `--radius-sm` 4 px for controls, marks, badges, tooltips and bar ends; `--radius-md` 8 px for segmented tracks, range fields, popovers, toasts and the tour card; `--radius-lg` 12 px for dialogs and the notes drawer. Bars round only their free end. The well band and heatmap cells have no radius.

The recurring forms are few: the substation as a ring in SL blue on the maps; houses as extruded footprints, and as small square keys in legends, tables and the inspector. Provenance marks are 9 px squares (filled for real, dashed for synthetic, a hollow circle for a reference). Status marks are a circle (good), a triangle (warning) and a cross (critical), always with their word. The live strip carries an 8 px disc in `--good-fill` beside its heading. On paper the roundel turns to a print-ink disc with a paper-coloured plus.

## Components

### The network map (signature)
The neighbourhood on the real map, in 3D, by `NetworkMap.tsx` on the opening screen; the Neighbourhood and request screens draw the same network on their own maps.
- **Map**: MapLibre over the OpenFreeMap basemap (positron by day, dark at night), tilted 50° and turned -18°, framed on the network and stepped in a little so the houses read. The basemap's own buildings are hidden: the modelled houses replace them on the same OpenStreetMap footprints.
- **Layers**: context buildings as a faint fill; the synthetic cables in ink, main cables thicker than service cables; the houses as extrusions raised by their floors and coloured by connection status (firm `--good-fill`, envelope `--warning-fill`, no solar `--border-strong`); the substation as a ground disc with an SL-blue ring. On the Neighbourhood the cables take the loading colour and the houses the voltage or net power of the hour on screen.
- **Interaction**: drag to move, right-drag or the compass to turn and tilt, the reset-view button to return. On the opening screen, which scrolls, MapLibre's cooperative gestures hold the wheel back unless Ctrl or Cmd is pressed and need two fingers on touch, with the words from `i18n.ts`; the page keeps its scroll.
- **Hover and pick**: hovering a house outlines it and shows a floating card with the address, its connection and, on the opening screen, "Test a connection here" in SL blue; a click selects the house and opens the request.
- **Key**: under the opening map as type on the ground (square house keys, then the real/synthetic sources); on the other map screens docked bottom-left as a flat field with a hairline.

### Data sheet (`Tile`)
A group of readings is rows parted by hairlines, never a grid of boxes: the label left in ink-2 at label size (with its provenance mark if any), the value right in ink at body/500 with its unit in caption, an optional note under it in caption. One column in a side panel, two by default, three or four where a band says so (two below 900 px). Only a reading that is itself the bad news takes `--danger`.

### Protocol
A set of checks is a table: one row per check, a status mark with its word (Pass / Fail, or the muted word "Measured" with no mark for a comparison that has no pass mark of its own), what was checked, the measured value right-aligned with its unit, and the criterion in caption. Only a real test carries Pass / Fail: on Operation's summer in shadow the pilot's two tests (no violation, agreements within the agreed level) are marked, and the comparisons beside them (perfect foresight, persistence) read "Measured", never Fail. Below 700 px each check reads in two lines: verdict, what and value on the first (the verdict column 6rem wide, room for "No cumple"), the criterion under it. The Annual report's last-resort checks and the network check's findings use the same marks and words; the evidence pack for Ei is a variant of it (below).

### Meter
A loading as a scale, not a progress bar: a 6 px inset track, a fill scaled from the left, 1 px ticks at 50 and 85 % of the limit and a 2 px ink mark at the limit. The fill is green, amber at or above 85 % of the limit, red past it: the same thresholds the map gives the cables. The reading sits in the head row like a data-sheet value.

### The answer and the verdict
- *Answer pair*: the static figure at h1/600 in muted ink, an arrow, the envelope figure at display/700 in ink, units at h3 size in muted ink; under it the ratio line in `--good`, then two bars (pencil and green, labels as words) and a data sheet.
- *Verdicts*: two columns under a 2 px ink rule, like the head of a form: the method in caption, a status mark with its word, the figure at h1/500, the detail and its basis.
- *Stale answer*: when the request changes after an evaluation, the answer is cancelled, not removed: it fades to 45 %, its figure is struck through, and an amber-wash note names what changed and offers to evaluate again.
- *Filed figure* (Annual report): the curtailed share of export at display size with its unit at h3 size in muted ink, the words beside it at body size in ink-2 (32ch), then the filing download as the primary button and the audit trail as a ghost one.

### The request answer
The answer in the side panel reads in one order: the row of ways to keep it, the full answer, then the decision.
- **Keep it**: small ghost buttons in one wrapping row: "Print or save as PDF", CSV and JSON downloads (download icon), "Copy link" (copy icon, confirmed by a toast). The link reopens the same saved answer.
- **Decision** (`DecisionBlock`), after the evidence and never before it, under a hairline with its h3. *Idle*: one muted line, an optional note (a textarea), and three answers in a row: "Connect with this envelope" (primary), "Connect firm" (ghost, disabled with its reason under it unless the static study agrees) and "Decline" (ghost). When the house's main fuse cannot carry the request, an export limit cannot help, so the order turns: "Decline" comes first as the primary, both connections follow as disabled ghosts, and the fuse's reason sits under them, referenced by both. *Working*: the chosen button says it is working, all three are disabled and a status line counts the weather years re-verified. *Decided*: a check in `--good`, the decision and its date (`format.dateTime`), the note in muted caption, and what happens next. When the queue has moved on, the block says why it cannot be decided instead of offering buttons. Errors take the alert icon in `--danger` with the text in ink.
- **History**: a folding list under a hairline: the address at label/500, what was asked in caption, the date on the right in caption, tabular. The open evaluation is in SL blue at 600.
- **Queue**: a folding list under a hairline, numbered in muted tabular figures: the address, the decision as a word (firm in `--good`, envelope in `--warning`, declined in ink-2, "by the rule" after it when the rule decided), the date, the note. Under it the base the queue started from and a muted line saying the decisions are sealed, then the decision log (CSV) and "Empty the queue" as small ghost buttons.
- **Confirm**: emptying the queue asks first, in a `Dialog` (440 px): a title, one sentence, then Cancel first, which takes the focus, and the destructive answer last as a ghost button with its label in `--danger`, edged in `--danger` on hover.

### Buttons
- **Shape:** gently squared (4 px), 40 px tall (32 px for the small variant), label size at 500.
- **Primary:** SL blue with On SL text; one per view. Hover steps to SL Deep.
- **Ghost:** transparent with an ink label and a `--border-strong` edge; hover fills with the well grey.
- **Icon button:** 40 × 40, hairline edge, ink-2 icon; pressed state takes the SL edge and icon.
- **Small:** 32 px tall with 12 px sides: the ways to keep an answer, retries, the tour's buttons, in-region actions.
- **Destructive:** never filled; a ghost button whose label is `--danger`, used only as the last answer of a confirm dialog.
- **File chooser:** a label styled as a button around a hidden file input; its focus ring shows when the input is focused.
- **Press:** compact controls scale to 0.97. Disabled: 50 % opacity, `not-allowed`.

### Segmented control
An inset well track (8 px corners, 4 px padding) with options as text; the chosen option is raised one surface (`--bg-elevated`) and edged with a hairline, never shadowed. Used for scenarios, colour-by and heat-pump choices, in the head row of the region it controls.

### Inputs / Fields
- **Range fields:** an inset panel (8 px) with the label left, the live value right in SL blue at 600, the slider across the full width (40 px hit height, SL accent) and its end values in caption under it.
- **Search fields:** inset with a hairline; focus turns the edge to the focus colour. The palette's one field shows focus as a 2 px rule under it.
- **Text fields** (the house search on the request panel, the decision note): 40 px (a textarea from two rows), a `--border-strong` edge on the elevated surface, 4 px corners, label size; focus is the 2 px focus outline at 1 px offset.
- **Command to copy** (`CommandLine`): the command as typed, in the one face (a base rule sets `code` to inherit the family) at label size on the inset grey with 4 px corners, wrapping anywhere, and a 32 px copy icon button beside it.
- **Errors:** a red-ink edge on a red-wash field, the icon in red, the text in ink, the retry as a small button.

### Navigation
The top bar (above), the menu sheet when the bar has folded (a 420 px dialog: the eight screens as 48 px rows with their number keys, the current one on SL wash in SL blue, then language and theme as two segmented controls under a hairline), the section bar on the four long screens, and the command palette (Ctrl+K: screens, the 120 addresses and actions, grouped, the active item on SL wash).

### The tour
Offered once, on a first visit, as a card with "Take the tour" (small primary) and "Not now" (small ghost); it never starts itself, so a projector stays still. The foot line's tour button starts it at any time. While the setup screen is shown (nothing computed) neither the offer nor the tour is rendered. The card is not modal: the screen behind it is the point and stays usable. It sits bottom-right above the foot line, 380 px wide, on the SL wash with a hairline and 8 px corners, no shadow; its head row holds the step count in caption and a 32 px close button, then the step's title at h3 and its text at label size in ink-2, then Back (small ghost) and Next (small primary, focused). Each step drives the real screen (screen, scenario, hour, house). On map screens at 1101 px and wider (`data-place`) it moves to the top of the map, against the side panel's edge (`--panel-w` or `--panel-w-wide`), off the panel the step explains. Escape closes it unless a modal is open.

### Key sheet
`?` opens the key sheet (`Shortcuts.tsx`, a 460 px dialog) generated from the same list the keyboard handler walks. Under a hairline at its foot, a checkbox turns the single-key shortcuts (1–8, N, F, / and ?) off for speech input and assistive typing; Ctrl and Cmd combinations keep working.

### Glossary term
A word of the trade carries a dotted underline in `--border-strong` (SL blue while open). Hover after a short delay, focus or tap opens its definition in a floating popover above the word; Escape closes it.

### Live strip (Sweden)
"Right now" in the Sweden panel: an h3 with the live mark, then a two-column definition list at label size (labels in ink-2): the current price and today's range in SEK/kWh, the SMHI temperature, each with its time of day. When the live sources fail it is one muted line saying so, never a blank.

### Network check (Method & data)
`NetworkCheck.tsx`, two blocks side by side: the data contract (each CSV as its file name in bold with its description in muted caption, its columns as a wrapping row with required ones underlined, the example download as a small ghost button), and the check (a file chooser as the primary button, "Try the reference network" as ghost, then the verdict as a status mark with its word and the score at label/600, the row counts in muted caption, and the findings as a list of status mark plus finding, parted by hairlines).

### Two sides (reinforcement)
Reinforce or connect, on Static vs envelopes: two equal columns, each under its own hairline with an h3 and a definition list (connected capacity, curtailment cost and delivered value in SEK/yr; the bigger transformer's capacities and its cost in kSEK); under both, one lead sentence with the payback years and a muted note with the price level. One column below 700 px.

### Operation (screen 5)
A document screen with its section bar (Applications, A day, The summer in shadow, A meter; the basis on the right names the sharing rule and the year). Everything on it is the day-ahead basis, the one that can be sent; perfect foresight appears only as a comparison.
- **Applications** (`Inbox.tsx`): a four-column data sheet of what the inbox has done (received, share decided by the rule, median wait, kW connected), each with its note; then a controls row with the rule as a segmented control ("Rule on" / "Every application to a person") and, beside it in a muted caption, the rule's thresholds or what turning it off means; then a `DataTable`: reference, house (a link to its saved answer), what was asked, state, received (`format.dateTime`), wait (`duration`, right-aligned) and actions. The state is a status mark with its word (connected firm or with an envelope: good; needs a person: warning; declined: critical), except "Being evaluated", a muted word with no mark because nothing has been decided yet; under it, in caption, who decided ("by the rule", "by the DSO") and, for an application in review, each reason in ink-2 within 38ch. An application in review carries "Decide" and "Evaluate again", both small ghosts: a row's actions never take the primary, which belongs to the decision dialog "Decide" opens. Below 600 px the header goes and each row stacks: the reference at 500, the house and the state on the left, the actions stacked on the right; what was asked, when it came and the wait are left to the dialog and the answer. An empty inbox is a sentence with a link to the request screen.
- **The decision dialog**: a 560 px `Dialog` holding a sheet: the h2 ("Application KP-..."), the address and what was asked in caption, the reasons as a bulleted list at label size, a link to open the answer, then the same decision block as the request screen.
- **A day**: a controls row of two inline fields (the house with an agreement, the date); a three-column data sheet (energy curtailed that day, the lowest limit, the limit without a schedule); its legend; a stepped chart of the hour's limit in the envelope green against what the house would export in `--series-main`, the curtailed band between them in amber, with its table twin; the clock note. Then a block, "What the inverter receives": "Issue the file (XML)" as a small primary sealed download, the limit without a schedule, CSV and JSON as small ghosts, the seal receipt under them, and the XML itself as a payload panel: one element per line and indented, in the one face at label size on the inset grey with 4 px corners, at most 320 px tall, scrolling, focusable with the focus ring. The download is the file as sent; the panel is for reading.
- **The summer in shadow**: a lead sentence with the season's GO or NO-GO, a protocol of the pilot's two tests and the comparisons, and the season's chart (curtailed energy per day on the day-ahead basis as amber bars, perfect foresight as a `--series-main` line, the legend in the region's head). Clicking a day, in the chart or its table, opens it in "A day" and scrolls there.
- **A meter**: "Try a synthetic day" (small primary) and "Check a CSV file" (a small ghost file chooser), the format in a muted caption; the result names its source, with a synthetic chip when it is the example, then a four-column data sheet (readings, breaches in `--danger` when there are any, compliance, the largest excess) and the breaches as a table. A refusal is a critical status line. A check belongs to its house: changing the house clears it.

### Sealed downloads
`SealedDownload.tsx`. A file whose sending must be provable (the evidence for Ei, a day's XML issued to an inverter) is fetched rather than linked and saved under the name the server gives. The page then keeps a receipt under the buttons, one muted caption line (`.seal-receipt`, a status region): when it was sealed (`format.clock`), its entry in the evidence chain, the file name and the first twelve hex digits of its seal. Until a file is saved the line is empty and takes no height. A failure is a toast; the button is disabled while it works.

### The evidence pack for Ei (Annual report)
`EiPack.tsx`: a lead sentence that counts the conditions by state (the list joined by `listJoin`), then the conditions as a protocol variant (`.protocol.ei-table`): the state, the condition, and its facts in caption with the source under them. Only the model's own verdicts carry a mark: "Shown by the model" is good, "Not met here" critical. What the DSO has stated, what still awaits the DSO and what does not apply are neutral muted words with no mark, because they are not verdicts. On a phone (700 px and below) the state takes its own line, above the condition and its facts. Under it, a block with the DSO's three statements as text fields and "Save the statements" (small ghost, enabled only when something changed); then "Download the evidence (sealed)" as the primary with its receipt, and "Print the method description" as a ghost; a muted note says it is evidence, never a legal conclusion.

### The applicant's page
`apply/Apply.tsx`, at `#/apply`, is its own page: `main.tsx` loads it instead of the application, so it has none of the application's chrome (no screens, no search, no foot line, no map, no charts). It asks for no personal data.
- **Bar**: the chrome blue, 56 px, the roundel and "Kapacitet+" on the left and the language buttons on the right, nothing else.
- **Strip**: under the bar, on the ground with a hairline below, at label size in ink-2: the real mark and its words, the synthetic mark and its words, and the privacy line.
- **Column**: one column held to 760 px and centred, 48 px between its parts: the headline and its lead, then numbered steps (the house, what to connect, the answer). Each step is a section with a hairline on top and its h2 at h3 size with the number beside it in tabular figures and muted ink; never a box. The house step uses the same house search as the request screen and confirms the chosen address with a check; the form is a wrapping row of inline fields (solar kWp, EV charger, a heat-pump checkbox, battery kWh, disabled without solar).
- **Answer** (`.portal-answer`): a lead sentence in plain words (firm; an envelope that never acted; less than 0.1 %; or the mean and bad-year curtailment), a short bulleted list (hours limited, EV charging not served, the main fuse, what the rule will do), the owner's figures when the answer is not firm, and which grid it was measured against in a muted caption. "Apply" follows with its note as the page's one primary: once an answer is shown, "Check my house" turns ghost.
- **Status** (`.portal-status`): where an application stands is a block under a `--border-strong` hairline, stronger than the steps', never a boxed card: an h2 at h3 size, the reference at h2 size and 600 in tabular figures, the state as a mark with its word (or the muted "Being evaluated"), who decided, the address and the request in caption, the dates through `format.dateTime`, the reasons when a person must look, and a note to keep the code. A last, unnumbered step looks an application up by its code (a field and a ghost button). Errors use the alert line of the decision block.
- Below 600 px the bar, the strip and the column take 16 px sides.

### The printed record
`AssessmentRecord.tsx` is a portal on `body` shown only in print; the app root is hidden, so printing the request screen gives this sheet. A4, margins 14/14/16 mm, black on white from the print tokens, the one face at 10 pt with tabular figures. The head: the roundel and "Kapacitet+" at 700, the title at 18 pt, the meta (id, date, engine, model run) as a two-column list with labels in print-muted, and a 1.5 pt ink rule under it. Sections at 11 pt headings: the house, the request, the answer (a table of static and envelope verdicts), the envelope minimum as a 7.5 pt month-by-hour table, the risk across weather years, the money in SEK/yr, and a foot under a 0.5 pt rule that says what is real, what is synthetic and the sources. Rows are 0.5 pt print-rule hairlines; sections never break inside.

Two more sheets share the same head, rules and type. `ReportRecord.tsx` (`print-record filing`) is what printing the Annual report gives: the year's figures as a key-value list with the basis note, the last-resort table, every agreement as a table (the one section allowed to break across pages), and a foot with what is real, what is synthetic and the method. The method description for Ei (`print-record ei-method`, in `EiPack.tsx`) has the basis, the rule and the date in its meta, the method in a paragraph, the conditions as a table with each state as a word, the DSO's three statements as a key-value list ("none" when empty) and the evidence note as its foot. The sheet is chosen by `html[data-print]`: "Print the method description" sets `data-print="ei"` just before printing and removes it after, and only then does the Ei sheet print; otherwise it is hidden and the screen's own record prints.

### Year strip
The Neighbourhood's only hour control and a year at a glance: a canvas of one column per day and one row per hour, coloured by transformer loading on the inset ground, the current hour boxed in ink. Every curtailed or loaded hour is drawn at its true length. Click or drag to move; arrow keys step an hour, Shift a day. A dark peek label shows the hour under the pointer; the controls (date, step, play, speed, showcase hour) sit on one row above it with a fixed-width date so they never shift during playback.

### Tables, states and layers
- `table.data`: label size, headers in caption/500 muted, hairline rows, numeric columns right-aligned; sortable headers expose `aria-sort` and show the active arrow in SL blue; selected rows on SL wash.
- **Loading:** skeletons in the inset grey that match the final layout (a data sheet loads as rows, a chart as its box). A refresh of what is on screen keeps the old values and shows a quiet floating "updating" note.
- **Empty:** never blank; a sentence and an action (pre-selected example house, "Try an example request").
- **Errors (`Missing`):** what failed, from which source, a ghost "Retry", and the endpoint under a details toggle for whoever reports it. A result that was never computed says so and shows the command that makes it (`CommandLine`).
- **A missing section (`SectionMissing`):** the section keeps its place under a hairline with its title in bold and why it is missing in caption, plus the command when a result is not computed; it never disappears.
- **Setup (`Setup.tsx`):** when the pipeline has not run, the whole interface is one document screen (760 px): the headline, a lead line, then numbered steps with a command to copy each (activate the project environment first, then `kapacitet all`, or unbundle a data package, then `serve`), the missing parts as a caption list, and "Check again" as the primary button. A server that takes the data from the browser (`setup_upload` in /health, as in a codespace) turns the page into "Load the data": one primary file chooser for `kapacitet-data.zip` beside a ghost link that downloads it from the data release, then the progress, an 8 px bar in SL blue on the inset grey with the MB sent or the files unpacked in tabular caption under it; a refusal as a critical status in plain words, and the restart said in a sentence before the page reloads by itself. The commands, the missing parts and a ghost "Check again" fold under a "From a terminal instead" disclosure behind a hairline.
- **Offline map:** a small note centred at the top of the map, on the elevated surface with a hairline and 4 px corners, in caption.
- **Layers:** `Dialog.tsx` is the one modal primitive: focus trap, Escape closes only the newest layer, focus returns where it came from, global shortcuts stay quiet while a modal is open. The notes drawer uses the same dismissal without a scrim. A dialog's content sits in a sheet (24 px padding, its title an h2 at h2 size with 16 px under it): the decision dialog, the queue's confirm, the key sheet and the imported networks.

### Charts
ECharts, modular, wrapped by `Chart.tsx`; every option is built from `tokens()`, so charts follow the theme and are rebuilt on theme and language changes.
- Text in the one face: values at label size in ink-2; ticks, axis names and source lines at caption size in muted ink; value-axis ticks pass through the interface's number format.
- Hairline axes, no ticks; horizontal gridlines only where a value must be read off the axis; no vertical gridlines.
- Lines 2 px, markers only on short series; area fill only under one highlighted series at 10–12 %. Bars round their free end only.
- Two series or fewer are labelled directly; more carry a `Legend` (and the production mix always does). A legend that names a region's series sits in the region's head row.
- Static vs dynamic is one comparison: the static series in pencil, the envelopes in green, on a shared axis.
- Energy kept against energy lost is green against amber: on the value of the extra solar, Delivered in the envelope green (`--series-dynamic`) and Curtailed in amber (`--warning-fill`), in kSEK; on the request's week, curtailed bars in amber.
- Tooltips float (elevated surface, float shadow, small corner, label size, tabular figures) and follow the pointer with no transition.
- Every chart has a table twin behind a quiet "Table" link (`ChartOrTable`) and a source line with its provenance mark.

### Maps
MapLibre on all four map views: the opening network map, the Neighbourhood, the request and Sweden.
- The national view turns every basemap label off, so no foreign toponym appears; zone labels SE1–SE4 are flat text on a small plate of the page colour, over zone fills from the balance scale.
- Houses on the request map: firm green, envelope amber, no solar grey, the selected house SL blue, with a docked key.
- Cables take the loading colour; houses the voltage or net-power colour; the night overlay over the map is one fixed tint whose opacity follows the sun.
- The map key is docked bottom-left, edged and not shadowed; under the opening map it is type on the ground. Controls are edged 40 px squares. Attribution (OpenStreetMap, OpenFreeMap) is always visible at caption size.

### Motion
| Token | Value | Use |
|---|---|---|
| `--ease` | `cubic-bezier(0.23, 1, 0.32, 1)` | everything that responds |
| `--ease-drawer` | `cubic-bezier(0.32, 0.72, 0, 1)` | the notes drawer |
| `--ease-in-out` | `cubic-bezier(0.77, 0, 0.175, 1)` | things that travel on screen |
| `--dur-micro` / `--dur-popover` / `--dur-panel` / `--dur-drawer` / `--dur-exit` | 120 / 160 / 200 / 240 / 140 ms | colour and press / popover / panels, toasts, the answer / drawer / exits |

- Colours ease over 120 ms, listed property by property, never `all`. Compact controls scale to 0.97 on press.
- Map colours change in place with the hour (feature state, no transition). Meters ease their fill, but not during playback, where an hour is shorter than the transition.
- Dialogs opened from the keyboard, the palette and screen changes do not animate.
- The notes drawer slides in from 24 px over 240 ms; toasts rise 12 px with a transition, so several in a row retarget.
- A glossary popover grows from its word (scale 0.97, 160 ms) after a 350 ms delay; once one has been open, the next opens instantly and without its entrance.
- The request answer's parts rise in once, 8 px, 40 ms apart.
- The tour card rises 8 px over 200 ms when opened by a click and appears at once from the keyboard; moving to the other side of the screen enters again rather than jumping.
- Charts draw once in 200 ms and never animate on a theme or language change.
- `prefers-reduced-motion: reduce` keeps colour and opacity only: no animation, no transforms on the drawer, toasts and the tour card, no smooth scrolling, no chart animation.

## Do's and Don'ts

### Do:
- **Do** read every colour, size, space, radius and duration from a role token; add a missing one to `tokens.css` first, as a primitive and a role, for both themes.
- **Do** take every figure from the API; the interface holds only units and labels.
- **Do** label what is real and what is synthetic on every screen: a filled 9 px square for real, a dashed one for synthetic, with the word, in the foot line, under the opening map and beside each source.
- **Do** keep one `--text-display` figure per screen and one SL-blue primary action per view.
- **Do** draw static against dynamic as one comparison, pencil against green.
- **Do** give every status a mark and a word (circle, triangle, cross); colour never carries meaning alone.
- **Do** keep thin-space thousands, fixed decimals and the unit beside the value, through `lib/format.ts`.
- **Do** write money as SEK (`SEK/kWh`, `SEK/yr`, `kSEK`, `kSEK/yr`) and every real-time date through `format.dateTime`.
- **Do** put a decision after its evidence, and the destructive answer of a confirm dialog last, as a ghost button, with Cancel first and focused.
- **Do** write every string in `lib/i18n/{en,es,sv}.ts`, in English, Spanish and Swedish, with the terms of `docs/glossary.md`.
- **Do** keep the network where it is: the real map, real footprints, the synthetic cables labelled as such.

### Don't:
- **Don't** colour chrome, headings, buttons or fields with green, amber or red; those belong to the data.
- **Don't** use SL blue for a data series.
- **Don't** colour the static series red, or draw voltage in red and green.
- **Don't** build a screen from boxed cards or a grid of identical tiles; use regions, blocks, one well, data sheets and protocols.
- **Don't** shadow anything that sits in the page; only floating layers take the float shadow.
- **Don't** use letter-spaced uppercase, kickers above headings, Title Case, two-tone headlines, a monospace or a second display face.
- **Don't** animate keyboard-opened dialogs, the palette or screen changes, and don't let anything pulse or loop.
- **Don't** write "kr", or build a date string by hand.
- **Don't** start the tour, or anything else, for the viewer.
- **Don't** show customers, pilots, testimonials, or a patented or proprietary algorithm; none exist.

### Voice
Short, factual, sentence case. Plain words first: a viewer who has never heard of hosting capacity can follow every screen, and the trade's words carry a glossary term where they appear. State what the data shows and let the reader conclude. No exclamation marks, no "powerful", "seamless", "revolutionary", no taglines. Say what is measured and what is assumed, every time, without hedging. An error says what failed, from where, and what to do.

### Accessibility floor
- WCAG 2.1 AA in both themes: text ≥ 4.5:1, large text and graphics ≥ 3:1. Every text and graphic pair in the tables above was measured and passes; the one known exception is the production mix's "Other", which is why that chart keeps its legend and table.
- Focus: a 2 px SL-blue outline at 2 px offset on every interactive element; a white ring inside the top bar.
- Hit targets 40 px (44 px on coarse pointers); smaller visible controls extend their hit area with a pseudo-element.
- A complete keyboard path: 1–8 for screens, PageUp / PageDown or Ctrl+← → for the previous or next screen, Ctrl+K and / search, ? key sheet, N notes, F full screen; the single-key shortcuts can be turned off in the key sheet; the year strip is a slider with arrow keys; tables sort from the keyboard; dialogs trap focus and return it. A new screen moves focus to its h1, which shows no ring because it is not a control.
- Every chart has a table twin and an `aria-label`; the maps carry descriptive labels; skip link to the content.
- Readable projected in a lit room and at 200 % zoom.

### Review checklist
- [ ] No raw colour, size, spacing, radius or duration in the diff outside `tokens.css`
- [ ] Checked by day and at night, at 1536×730 and at 1440, 768 and 390 px
- [ ] Every number from the API, tabular, thin-space grouped, fixed decimals, unit beside it, stated once
- [ ] Money in SEK, never kr; dates through `format.dateTime`
- [ ] One display figure and one primary action per view
- [ ] Line colours only in the data; SL blue only on chrome, action, selection, links and focus
- [ ] Real and synthetic marked with their filled and dashed squares and sources
- [ ] Status shown as mark + word; contrast measured, not assumed
- [ ] No boxed cards, tile grids, kickers, uppercase labels or in-page shadows
- [ ] New strings in English, Spanish and Swedish
- [ ] Motion: presses 0.97, colour 120 ms, nothing on keyboard dialogs or screen changes, reduced motion keeps colour and opacity only
- [ ] Keyboard path complete, focus visible, hit targets 40/44 px
- [ ] Alpha over a channel token written `rgba(var(--token), a)`
- [ ] **Signature test**: the network map, the SL top bar, the data sheet and the real/synthetic foot line are each where they belong
