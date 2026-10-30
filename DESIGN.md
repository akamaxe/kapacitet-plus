---
name: Kapacitet+
description: A digital twin of one Stockholm low-voltage neighbourhood, drawn as a line network.
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
  section-bar:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.ink-2}"
    typography: "{typography.label}"
    height: "44px"
  feeder-badge:
    backgroundColor: "{colors.sl}"
    textColor: "{colors.on-sl}"
    rounded: "{rounded.sm}"
    size: "24px"
  data-sheet-row:
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    padding: "8px 0"
  field:
    backgroundColor: "{colors.well}"
    rounded: "{rounded.md}"
    padding: "12px 16px"
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

**Creative North Star: "The Line Network"**

The neighbourhood grid is drawn the way Stockholm draws its public transport: the eleven feeders leave the substation like tunnelbana lines, every house is a station on its line, and the chosen house's feeder can be read as the strip above a train door. The interface around that drawing is a working tool for a DSO connection planner and for the applicant on the other side of the same connection: a white ground, ink type, one SL-blue top bar carrying the six screens, and the line colours (green, amber, red) kept for the data. Hierarchy comes from type size, weight and colour fields, not from boxes; hairlines appear in tables, data sheets and between regions, and nowhere as decoration.

It is dense where the work is (the Neighbourhood instrument, the request form) and calm where the answer is (Static vs envelopes, one figure at display size). It reads from a projector in a lit room and on a 1536×730 laptop, and it holds on a phone. Night is a designed second theme, not an inversion: the same roles on a deep ground, the line colours lifted, the chrome kept a deep SL blue.

**Key Characteristics:**
- White ground, ink type, SL blue only on the chrome, the one action, selection, links and focus.
- Line colours live in the data: green firm, amber envelope or near a limit, red only above a rating.
- One typeface, Familjen Grotesk, with its default tabular figures for every reading.
- No boxed cards: regions part by hairlines and space; only floating layers cast a shadow.
- The line map and line strip are the signature, and every number on them comes from the API.
- Real data is drawn solid, synthetic data dashed, on every screen.

## Colors

A white sheet with ink and one public-transport blue, and three signal colours that only appear where the data is.

`web/src/styles/tokens.css` holds primitives named after this world (`--ground`, `--ink`, `--sl`, `--green`…) and, once, the role tokens that components read (`--bg-base`, `--text-primary`, `--accent`…). A theme swaps primitives only. Components, `lib/colors.ts` and the maps read role tokens; nothing outside `tokens.css` holds a raw colour. The frontmatter keys without a suffix are day; `-night` keys are the night primitives of the same name.

### Primary
- **SL Blue** (`sl`; role `--accent`, `--focus`): the one primary action per view ("Test a connection"), links, the selected house, focus rings, the feeder badges and the transformer symbol on the line map. 10.5:1 on white by day, 8.2:1 on the night ground. Its hover step is **SL Deep** (`sl-deep`, `--accent-hover`); selected rows and palette items sit on **SL Wash** (`sl-wash`, `--accent-quiet`, accent on it 8.8:1 day, 6.2:1 night). Text on the accent is **On SL** (`on-sl`, `--on-accent`): white by day, near-black at night, because the night accent is a lifted light blue.
- **Chrome Blue** (`sl-chrome`; role `--chrome`): the top bar. It equals SL Blue by day and stays a deep blue (`sl-chrome-night`) at night instead of following the lifted accent. Chrome text is white (`--chrome-ink`, 10.5:1 day, 13.7:1 night) and **Chrome Mist** (`sl-ink-2`, `--chrome-ink-2`, 7.1:1 / 7.9:1) for the inactive links and tools.

### Secondary (the line colours, data only)
| Role token | Primitive | Job | Text on ground (day / night) |
|---|---|---|---|
| `--good-fill`, `--series-dynamic` | `green` | firm stations, the envelope (dynamic) series, the "with envelopes" bar | 5.4 / 6.2 (graphic) |
| `--good` | `green-ink` | the ratio line, pass icons, good outcomes as text | 6.1 / 9.2; 5.2 / 7.0 on its wash |
| `--good-quiet` | `green-wash` | quiet good fields | |
| `--warning-fill` | `amber` | stations under an envelope, a meter at or above 85 % of its limit | 3.2 / 9.5 (graphic) |
| `--warning` | `amber-ink` | limited / non-firm text and icons | 6.0 / 10.0; 5.3 / 7.6 on its wash |
| `--warning-quiet` | `amber-wash` | the stale-answer note | |
| `--danger-fill` | `red` | above rating: a cable, the transformer, a meter past its limit | 4.7 / 6.2 (graphic) |
| `--danger` | `red-ink` | rejected, failed, a reading that is itself the bad news | 6.5 / 7.7; 5.3 / 6.4 on its wash |
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

### Neutral
| Role token | Primitive | Job | Day / night |
|---|---|---|---|
| `--bg-base`, `--bg-surface` | `ground` (`sheet`) | the page and side panels: the same white, parted by a hairline | |
| `--bg-elevated` | `raised` | dialogs, popovers, tooltips, the chosen segment | white / `raised-night` |
| `--bg-inset` | `well` | segmented tracks, fields, the dense well band, skeletons, meter tracks | |
| `--border` | `rule` | every hairline; any border drawn without a colour takes it | |
| `--border-strong` | `rule-strong` | ghost-button edges, hollow "no solar" stations, meter ticks | 3.3 / 4.4 on ground |
| `--text-primary` | `ink` | headings, values | 16.5 / 16.1 |
| `--text-secondary` | `ink-2` | body, labels | 9.5 / 10.1 |
| `--text-muted` | `ink-3` | captions, sources, units | 6.4 / 6.9 (5.8 / 6.4 on a well) |

### Named Rules
**The Quarantine Rule.** Green, amber and red appear only in the network, charts, maps, meters and status marks. The chrome is ink and SL blue. A button, a heading or a field is never green, amber or red.

**The One Blue Rule.** SL blue is the action and the selection, never a data value. A chart series never takes `--accent`; the selected house does.

**The Pencil Rule.** The static study is never red. It is conservative, not wrong: it is pencil grey beside the envelope green, in one comparison.

**The Token Rule.** No colour, size, spacing value, radius or duration is written outside `tokens.css`. A missing value is added there first, as a primitive and a role.

## Typography

**Display Font:** Familjen Grotesk Variable (with system-ui, -apple-system, Segoe UI, sans-serif)
**Body Font:** the same
**Label/Mono Font:** none; identifiers and readings use the same face

**Character:** A Stockholm studio's grotesk, compact and plain, with tabular default figures, so one face carries words, identifiers and every reading without a monospace.

### Hierarchy
The scale steps about 1.2 on a 15 px body (rem on a 16 px root).
- **Display** (700, `--text-display` 2.5rem, 1.05, −0.022em): the one answer figure per screen ("787 kW" on Static vs envelopes). Nothing else is set at this size. Falls to h1 size below 700 px.
- **Headline** (600, `--text-h1` 1.625rem, 1.2): the screen title; also the "from" figure of the answer pair and the verdict figure. A map screen's h1 sits in its side panel one step down (`--text-h2`).
- **Title** (600, `--text-h2` 1.25rem, 1.3): a region heading; also the lead answer sentence under a title (at 500).
- **Title small** (600, `--text-h3` 1.0625rem, 1.35): a sub-heading in a region or panel, the hour verdict, the inspector title, the brand name (700).
- **Body** (400, `--text-body` 0.9375rem, 1.5): prose, data-sheet values (500).
- **Label** (500, `--text-small` 0.8125rem, 1.45): data-sheet labels, buttons, navigation, legends, table cells.
- **Caption** (400, `--text-caption` 0.75rem, 1.4): sources, units beside values, axis ticks, notes, the foot line.

Weights: 400 body, 500 labels, buttons and values, 600 headings, 700 only for the brand name, the display figure and the feeder numbers. Headings use `text-wrap: balance`, prose `pretty`; captions stop at 72ch, lead lines at 46–60ch.

### Numbers and units
- `font-variant-numeric: tabular-nums` is set on the body and restated on every reading, so values do not jitter while the hour moves.
- Every number goes through `lib/format.ts`: thousands are grouped with a narrow no-break space in both languages (`2 503 kWh`), the decimal mark follows the language, and decimals are fixed per figure (power 0–1, percentages 1, voltage 3 in pu, prices 2–3). Never more precision than the data supports. A missing value is an en dash.
- The unit sits beside the value at caption size in `--text-muted` (`kW`, `%`, `pu`, `kWp`); on a chart it appears once, in the axis name.
- Dates read `19 May 2025, 11:00`; times are the study year's local standard time.
- A figure is said once per view: if the heading says it, the chart does not.

### Named Rules
**The One Face Rule.** Familjen Grotesk sets everything, identifiers and figures included. No monospace, no second display face.

**The Three Levers Rule.** A step in the hierarchy changes size, weight and ink together; a label is small/500/ink-2, its value body/500/ink, its note caption/400/ink-3. Letter-spaced uppercase is not used anywhere, and every heading, button and label is sentence case.

## Layout

Base unit 4 px; every margin, padding and gap is a `--space-*` step (4, 8, 12, 16, 24, 32, 48, 64).

**The frame.** `.app` is a three-row grid: the top bar (`--topbar-h` 56 px), the screen, and the foot line (`--statusbar-h` 30 px). The window never scrolls; each screen scrolls inside `.page`, which is `position: relative` so absolutely placed children cannot escape it.

- **Top bar**: Chrome Blue, white type. Left: the roundel (a white disc with the plus of the name) and "Kapacitet+", then the site scope ("Smedslätten · Bromma · SE3"). Right of centre: the six screens as text links in Chrome Mist, the current one white at 600 with a 3 px white rule on the bar's bottom edge. Right: search (a tinted field with its "Ctrl K" key), notes, language, theme, full screen. Focus inside the bar is a white ring drawn within it.
- **Foot line**: on the ground with a hairline above, caption size, muted: the real/synthetic legend on the left, the reference year and the key-sheet button on the right. The same on every screen.
- **Section bar**: the two long document screens (Static vs envelopes, Method & data) carry a sticky bar at the top of their scroller, `--toolbar-h` 44 px: sections as text links on the left (the one being read underlined 2 px in ink), the basis of the screen's figures on the right in caption. It scrolls only `.page`, never the window. A region's controls stay in its own head row, never in the bar.

**Screen shapes.**
- *Map screens* (Neighbourhood, Connection request, Sweden): `.split`, the drawing or map on the left and a 460 px side panel on the right (560 px for the wider panel), the panel on the ground with a hairline on its left. The Neighbourhood map column carries the year strip and the folding house table beneath it.
- *Document screens* (Static vs envelopes, 2030, Method & data): `.page-inner`, max 1550 px, 32 px gutters, 24 px between regions.
- *The opening answer*: a 1.2 : 1 two-column hero with the line map first and the answer beside it, both ending together.

**Three weights inside a document screen.**
| Weight | How it looks | Carries |
|---|---|---|
| Region `.region` | no surface; parted from the next by one hairline and 32 px | the screen's own divisions, `h2` |
| Block `.block` | a hairline above and 16 px of space; no fill, no box | a self-contained instrument inside a region, `h3` |
| Well `.well` | the inset grey, hairlines above and below, bleeding to the gutter | one dense band per screen: a long table, a year of hours |

**Responsive.** At 1440 px the scope gives way; at 1280 px the search label; between 1101 and 1350 px the side panel narrows to 390/450 px; at 1100 px the six screens fold into a menu sheet and split screens stack (map above, panel below); at 900 px two-column grids go to one and the foot line keeps one legend item; at 700 px (phone) the answer comes before the line map, the display figure drops to h1 size, the year strip shortens to 64 px and the map key keeps only its scales. On coarse pointers every control grows to 44 px.

## Elevation & Depth

Flat by default. Depth is a tonal step plus a hairline; a surface that sits in the page never has a shadow. Only what floats casts one, and it is always the same: a 1 px ring and one soft offset depth (`--shadow-popover`). Floating layers stack at `--z-sticky` 3, `--z-overlay` 40 (notes drawer, glossary popovers), `--z-dialog` 50 (dialogs, palette, key sheet, menu sheet), `--z-toast` 60. The dialog scrim is the night tint at 38 %.

### Shadow Vocabulary
- **Float** (`box-shadow: 0 0 0 1px rgba(22,32,43,.08), 0 10px 28px -8px rgba(22,32,43,.22)`; night `0 0 0 1px rgba(255,255,255,.08), 0 14px 36px -10px rgba(0,0,0,.65)`): dialogs, the palette, toasts, the notes drawer, glossary popovers, chart and map tooltips, the line-map hover card, the "updating" note.

### Named Rules
**The Only-What-Floats Rule.** The map key, the segmented control's chosen option and MapLibre's controls are edged with a hairline, never shadowed. No glows, no accent-coloured shadows, no decorative gradients; the only gradients are colour-scale keys drawn from the same function as the marks they explain.

## Shapes

Small, even corners: `--radius-sm` 4 px for controls, marks, badges, tooltips and bar ends; `--radius-md` 8 px for segmented tracks, fields, popovers, toasts and the line-map hover card; `--radius-lg` 12 px for dialogs and the notes drawer. Bars round only their free end. The well band has no radius.

The recurring forms come from the line network: lines of one fixed weight with round caps, bent only at 45°; round stations; square feeder badges with a 4-unit corner; the transformer as two overlapping rings. Provenance marks are 9 px squares (filled for real, dashed for synthetic, a hollow circle for a reference). Status marks are a circle (good), a triangle (warning) and a cross (critical), always with a word.

## Components

### The line map (signature)
The neighbourhood's eleven feeders as metro lines, drawn in SVG by `LineMap.tsx` from `/network`, laid out once and shared by every caller.
- **Layout**: each feeder takes the 45° direction nearest its real bearing from the substation (the longest feeders choose first); its houses sit in order of distance along the cable at an even spacing (15–34 units, about the same arm length per line); branches fold into one line; a line with more than nine stations bends once, always counter-clockwise; a second feeder in a direction runs 14 units beside the first and bends early. The first station sits clear of the transformer. The drawing is labelled schematic ("Cables: synthetic, drawn schematically").
- **Lines**: ink, 7 units, round caps and joins. On Neighbourhood each line takes the loading colour of its worst cable at the hour on screen.
- **Stations**: 6.5-unit discs with a ground-coloured ring. Default colour is connection status: firm green (`--good-fill`), envelope amber (`--warning-fill`), no solar hollow (ground fill, `--border-strong` ring). On Neighbourhood stations take the voltage colour, or net power. The selected station grows to 9 units with a 3-unit SL-blue ring, and every other line, station and badge dims to 25 %.
- **Hub and badges**: the transformer as two overlapping ground discs ringed in SL blue at the centre; a 24-unit SL-blue square at the end of each line with the feeder number (data index + 1) in white bold.
- **Key and hover**: the key sits under the drawing as type on the ground (status dots, then the real/synthetic sources). Hovering a station shows a floating card with the address, "feeder n, m metres", the status, and, where the map is pickable, the action in SL blue.

### The line strip (signature)
One feeder as the strip above a train door (`LineStrip`), in the request panel and the house inspector: the feeder badge, "Station k of n on its feeder", the distance from the substation; then a 4-unit ink line with the small transformer symbol at its left and every station of the line in order, at even spacing, coloured by status, the chosen one larger and ringed in SL blue.

### Data sheet (`Tile`)
A group of readings is rows parted by hairlines, never a grid of boxes: the label left in ink-2 at label size (with its provenance mark if any), the value right in ink at body/500 with its unit in caption, an optional note under it in caption. One column in a side panel, two by default, 3/4/6 where a band says so. Only a reading that is itself the bad news takes `--danger`.

### Protocol
A set of checks is a table: one row per check, a status mark with its word (Pass / Fail, or "measured" for a comparison with no pass mark), what was checked, the measured value right-aligned with its unit, and the criterion in caption.

### Meter
A loading as a scale, not a progress bar: a 6 px inset track, a fill scaled from the left, 1 px ticks at 50 and 85 % of the limit and a 2 px ink mark at the limit. The fill is green, amber at or above 85 % of the limit, red past it: the same thresholds the line map uses. The reading sits in the head row like a data-sheet value.

### The answer and the verdict
- *Answer pair*: the static figure at h1/600 in muted ink, an arrow, the envelope figure at display/700 in ink, units at h3 size in muted ink; under it the ratio line in `--good`, then two bars (pencil and green, labels as words) and a data sheet.
- *Verdicts*: two columns under a 2 px ink rule, like the head of a form: the method in caption, a status mark with its word, the figure at h1/500, the detail and its basis.
- *Stale answer*: when the request changes after an evaluation, the answer is cancelled, not removed: it fades to 45 %, its figure is struck through, and an amber-wash note names what changed and offers to evaluate again.

### Buttons
- **Shape:** gently squared (4 px), 40 px tall (32 px for the small variant), label size at 500.
- **Primary:** SL blue with On SL text; one per view. Hover steps to SL Deep.
- **Ghost:** transparent with an ink label and a `--border-strong` edge; hover fills with the well grey.
- **Icon button:** 40 × 40, hairline edge, ink-2 icon; pressed state takes the SL edge and icon.
- **Press:** compact controls scale to 0.97. Disabled: 50 % opacity, `not-allowed`.

### Segmented control
An inset well track (8 px corners, 4 px padding) with options as text; the chosen option is raised one surface (`--bg-elevated`) and edged with a hairline, never shadowed. Used for scenarios, map/lines view and colour-by choices, in the head row of the region it controls.

### Inputs / Fields
- **Range fields:** an inset panel (8 px) with the label left, the live value right in SL blue at 600, the slider across the full width (40 px hit height, SL accent) and its end values in caption under it.
- **Search fields:** inset with a hairline; focus turns the edge to the focus colour. The palette's one field shows focus as a 2 px rule under it.
- **Errors:** a red-ink edge on a red-wash field, the icon in red, the text in ink, the retry as a small button.

### Navigation
The top bar (above), the menu sheet below 1100 px (the six screens as 48 px rows with their number keys, the current one on SL wash in SL blue), the section bar on the two long screens, and the command palette (Ctrl+K: screens, the 120 addresses and actions, grouped, the active item on SL wash).

### Glossary term
A word of the trade carries a dotted underline in `--border-strong` (SL blue while open). Hover after a short delay, focus or tap opens its definition in a floating popover above the word; Escape closes it.

### Year strip
The Neighbourhood's only hour control and a year at a glance: a canvas of one column per day and one row per hour, coloured by transformer loading on the inset ground, the current hour boxed in ink. Every curtailed or loaded hour is drawn at its true length. Click or drag to move; arrow keys step an hour, Shift a day. A dark peek label shows the hour under the pointer; the controls (date, step, play, speed, showcase hour) sit on one row above it with a fixed-width date so they never shift during playback.

### Tables, states and layers
- `table.data`: label size, headers in caption/500 muted, hairline rows, numeric columns right-aligned; sortable headers expose `aria-sort` and show the active arrow in SL blue; selected rows on SL wash.
- **Loading:** skeletons in the inset grey that match the final layout (a data sheet loads as rows, a chart as its box). A refresh of what is on screen keeps the old values and shows a quiet floating "updating" note.
- **Empty:** never blank; a sentence and an action (pre-selected example house, "Try an example request").
- **Errors (`Missing`):** what failed, from which source, a ghost "Retry", and the endpoint under a details toggle for whoever reports it.
- **Layers:** `Dialog.tsx` is the one modal primitive: focus trap, Escape closes only the newest layer, focus returns where it came from, global shortcuts stay quiet while a modal is open. The notes drawer uses the same dismissal without a scrim.

### Charts
ECharts, modular, wrapped by `Chart.tsx`; every option is built from `tokens()`, so charts follow the theme and are rebuilt on theme and language changes.
- Text in the one face: values at label size in ink-2; ticks, axis names and source lines at caption size in muted ink; value-axis ticks pass through the interface's number format.
- Hairline axes, no ticks; horizontal gridlines only where a value must be read off the axis; no vertical gridlines.
- Lines 2 px, markers only on short series; area fill only under one highlighted series at 10–12 %. Bars round their free end only.
- Two series or fewer are labelled directly; more carry a `Legend` (and the production mix always does).
- Static vs dynamic is one comparison: the static series in pencil, the envelopes in green, on a shared axis.
- Tooltips float (elevated surface, float shadow, small corner, label size, tabular figures) and follow the pointer with no transition.
- Every chart has a table twin behind a quiet "Table" link (`ChartOrTable`) and a source line with its provenance mark.

### Maps
MapLibre on the Neighbourhood, request and Sweden screens; the opening screen uses the line map and loads no map.
- The national view turns every basemap label off, so no foreign toponym appears; zone labels SE1–SE4 are flat text on a small plate of the page colour, over zone fills from the balance scale.
- Houses on the request map: firm green, envelope amber, no solar grey, the selected house SL blue, with a docked key.
- Cables take the loading colour; houses the voltage or net-power colour; the night overlay over the map is one fixed tint whose opacity follows the sun.
- The map key is docked bottom-left, edged and not shadowed; in the lines view it becomes type on the ground. Controls are edged 40 px squares. Attribution (OpenStreetMap, OpenFreeMap) is always visible at caption size.

### Motion
| Token | Value | Use |
|---|---|---|
| `--ease` | `cubic-bezier(0.23, 1, 0.32, 1)` | everything that responds |
| `--ease-drawer` | `cubic-bezier(0.32, 0.72, 0, 1)` | the notes drawer |
| `--ease-in-out` | `cubic-bezier(0.77, 0, 0.175, 1)` | things that travel on screen |
| `--dur-micro` / `--dur-popover` / `--dur-panel` / `--dur-drawer` / `--dur-exit` | 120 / 160 / 200 / 240 / 140 ms | colour and press / popover / panels, toasts, the answer / drawer / exits |

- Colours ease over 120 ms, listed property by property, never `all`. Compact controls scale to 0.97 on press.
- Line-map lines and stations change colour in place over 120 ms. Meters ease their fill, but not during playback, where an hour is shorter than the transition.
- Dialogs opened from the keyboard, the palette and screen changes do not animate.
- The notes drawer slides in from 24 px over 240 ms; toasts rise 12 px with a transition, so several in a row retarget.
- A glossary popover grows from its word (scale 0.97, 160 ms) after a 350 ms delay; once one has been open, the next opens instantly and without its entrance.
- The request answer's parts rise in once, 8 px, 40 ms apart.
- Charts draw once in 200 ms and never animate on a theme or language change.
- `prefers-reduced-motion: reduce` keeps colour and opacity only: no animation, no transforms on the drawer and toasts, no smooth scrolling, no chart animation.

## Do's and Don'ts

### Do:
- **Do** read every colour, size, space, radius and duration from a role token; add a missing one to `tokens.css` first, as a primitive and a role, for both themes.
- **Do** take every figure from the API; the interface holds only units and labels.
- **Do** label what is real and what is synthetic on every screen: a filled 9 px square for real, a dashed one for synthetic, with the word, in the foot line, under the line map and beside each source.
- **Do** keep one `--text-display` figure per screen and one SL-blue primary action per view.
- **Do** draw static against dynamic as one comparison, pencil against green.
- **Do** give every status a mark and a word (circle, triangle, cross); colour never carries meaning alone.
- **Do** keep thin-space thousands, fixed decimals and the unit beside the value, through `lib/format.ts`.
- **Do** write every string in `lib/i18n.ts`, in English and Spanish.
- **Do** keep the line map schematic and say so: 45° bends, one ink weight, houses in order of distance.

### Don't:
- **Don't** colour chrome, headings, buttons or fields with green, amber or red; those belong to the data.
- **Don't** use SL blue for a data series.
- **Don't** colour the static series red, or draw voltage in red and green.
- **Don't** build a screen from boxed cards or a grid of identical tiles; use regions, blocks, one well, data sheets and protocols.
- **Don't** shadow anything that sits in the page; only floating layers take the float shadow.
- **Don't** use letter-spaced uppercase, kickers above headings, Title Case, two-tone headlines, a monospace or a second display face.
- **Don't** animate keyboard-opened dialogs, the palette or screen changes, and don't let anything pulse or loop.
- **Don't** show customers, pilots, testimonials, or the "patented fair capacity-sharing algorithm"; none exist.

### Voice
Short, factual, sentence case. Plain words first: a viewer who has never heard of hosting capacity can follow every screen, and the trade's words carry a glossary term where they appear. State what the data shows and let the reader conclude. No exclamation marks, no "powerful", "seamless", "revolutionary", no taglines. Say what is measured and what is assumed, every time, without hedging. An error says what failed, from where, and what to do.

### Accessibility floor
- WCAG 2.1 AA in both themes: text ≥ 4.5:1, large text and graphics ≥ 3:1. Every text and graphic pair in the tables above was measured and passes; the one known exception is the production mix's "Other", which is why that chart keeps its legend and table.
- Focus: a 2 px SL-blue outline at 2 px offset on every interactive element; a white ring inside the top bar.
- Hit targets 40 px (44 px on coarse pointers); smaller visible controls extend their hit area with a pseudo-element.
- A complete keyboard path: 1–6 for screens, Ctrl+K search, ? key sheet, N notes, F full screen; the year strip is a slider with arrow keys; tables sort from the keyboard; dialogs trap focus and return it.
- Every chart has a table twin and an `aria-label`; the line map and strip carry descriptive labels; skip link to the content.
- Readable projected in a lit room and at 200 % zoom.

### Review checklist
- [ ] No raw colour, size, spacing, radius or duration in the diff outside `tokens.css`
- [ ] Checked by day and at night, at 1536×730 and at 1440, 768 and 390 px
- [ ] Every number from the API, tabular, thin-space grouped, fixed decimals, unit beside it, stated once
- [ ] One display figure and one primary action per view
- [ ] Line colours only in the data; SL blue only on chrome, action, selection, links and focus
- [ ] Real and synthetic marked with their filled and dashed squares and sources
- [ ] Status shown as mark + word; contrast measured, not assumed
- [ ] No boxed cards, tile grids, kickers, uppercase labels or in-page shadows
- [ ] New strings in English and Spanish
- [ ] Motion: presses 0.97, colour 120 ms, nothing on keyboard dialogs or screen changes, reduced motion keeps colour and opacity only
- [ ] Keyboard path complete, focus visible, hit targets 40/44 px
- [ ] **Signature test**: the line map or strip, the SL top bar, the data sheet and the real/synthetic foot line are each where they belong
