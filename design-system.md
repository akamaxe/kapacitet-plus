# Kapacitet+ — Design System

Single source of truth for the visual language of Kapacitet+.
**Rule for any agent or developer working on this repository: never hard-code a colour, font size, spacing value or radius. Use the tokens defined here. If a value you need is missing, add it to this file first, then use it.**

Design intent: **a network planning drawing for the people who connect customers to the grid.** Paper and ink, calm and exact. The person at the screen is a connection engineer at a Swedish DSO, at a desk, with a queue of applications: they have to decide whether a connection fits, on what terms, and leave evidence behind. The interface should feel like a well-made engineering sheet, not a control-room console and not a SaaS dashboard. Restraint over decoration; numbers over slogans; lines over boxes.

Three things must be true on every screen:

1. **It reads as a sheet of a drawing set.** A title block at the foot says which sheet this is, where, and on what data.
2. **Pencil and ink.** The worst-case study is drawn in pencil grey, the dynamic envelopes in station-green ink.
3. **What is surveyed is solid; what is assumed is dashed.** Real data carries a filled mark, synthetic data a dashed one, as on a drawing.

---

## 1. Colour

### 1.1 Principles

- **Colours come from the drawing office and the substation**, not from a UI kit: drafting paper, blue-black ink, pencil graphite, the green-grey paint of a nätstation kiosk, the amber of a midsummer noon (when curtailment happens) and the red of a revision mark.
- **One accent.** Station green is the only accent. It marks the dynamic/envelope side of the product, firm and approved status, and primary actions.
- **Colour carries meaning.** Amber = limited or near a limit. Red = limit exceeded or rejected. Pencil grey = the static/worst-case baseline. Never use these decoratively.
- **Primitives, then roles.** `tokens.css` names the primitives after this world (`--paper`, `--ink`, `--station`…) and the semantic tokens the components use (`--bg-base`, `--text-primary`, `--accent`…) point at them. A component never reads a primitive directly.
- **Paper is the default; night is designed, not inverted.** The planner works in daylight and the product is shown on projectors, where a light sheet reads best. Night keeps the same roles with lightness steps on graphite.

### 1.2 Paper (default)

| Primitive | Role token | Value | Use | Measured |
|---|---|---|---|---|
| `--paper` | `--bg-base` | `#F4F2EC` | the sheet the screen is drawn on | |
| `--sheet` | `--bg-surface` | `#FAF9F5` | a panel: one tonal step up, no shadow | |
| `--sheet-raised` | `--bg-elevated` | `#FFFFFF` | popovers, dialogs, tooltips (+ shadow, they float) | |
| `--well` | `--bg-inset` | `#ECE9E1` | wells, inputs, the dense band | |
| `--rule` | `--border` | ink at 12 % | hairlines and dividers | |
| `--rule-strong` | `--border-strong` | `#787F86` | focus ring, selection | 3.6:1 on paper, 3.3:1 on a well |
| `--ink` | `--text-primary` | `#1B2128` | headings, values | 14.5:1 |
| `--ink-2` | `--text-secondary` | `#48525B` | body copy, labels | 7.1:1 (6.6:1 on a well) |
| `--graphite` | `--text-muted` | `#5F666C` | captions, sources | 5.2:1 (4.8:1 on a well) |
| `--station` | `--accent` | `#2C6A55` | dynamic series, firm, primary action | 5.7:1 |
| `--station-deep` | `--accent-hover` | `#215443` | hover | 8.7:1 under white |
| `--station-wash` | `--accent-quiet` | `#DCE8E1` | selected fills behind text | station on it 5.1:1 |
| `--on-station` | `--on-accent` | `#FFFFFF` | text on station | 6.4:1 |
| `--sun` | `--warning` | `#8A5B12` | limited / non-firm text | 5.2:1 |
| `--sun-wash` | `--warning-quiet` | `#F4E7CB` | warning fills | sun on it 4.8:1 |
| `--sun-fill` | `--warning-fill` | `#D99A2B` | amber marks in charts and maps | graphic, labelled |
| `--revision` | `--danger` | `#A8382C` | above rating / rejected text | 5.7:1 |
| `--revision-wash` | `--danger-quiet` | `#F5DED9` | danger fills | revision on it 5.0:1 |
| `--revision-fill` | `--danger-fill` | `#D4533F` | red marks in charts and maps | 3.9:1 |
| `--pencil` | `--neutral-series` | `#7A8187` | the static series | 3.8:1 on a panel |

### 1.3 Night

| Primitive | Value | Measured |
|---|---|---|
| `--paper` / `--sheet` / `--sheet-raised` / `--well` | `#121417` / `#181B1F` / `#1F2328` / `#0D0F11` | lightness steps on one graphite hue |
| `--rule` | white at 8 % | |
| `--rule-strong` | `#6E737A` | 3.9:1 |
| `--ink` / `--ink-2` / `--graphite` | `#E9E6DF` / `#B4B0A8` / `#8E8B85` | 14.8:1 / 8.5:1 / 5.4:1 (4.7:1 on a raised sheet) |
| `--station` / `--station-deep` / `--station-wash` / `--on-station` | `#6DB497` / `#88C7AC` / `#1D3A30` / `#0D0F11` | 7.6:1; station on its wash 5.1:1 |
| `--sun` / `--sun-wash` | `#E0A74A` / `#3D3017` | 8.6:1; 6.0:1 |
| `--revision` / `--revision-wash` | `#E2685A` / `#351A17` | 5.6:1; 4.9:1 |
| `--pencil` | `#6F757B` | 3.7:1 on a panel |

All ratios are WCAG 2.1, measured on the page (`--paper`) unless stated, on 2026-10-28. In both themes the static and dynamic series differ in lightness, not only in hue (paper: pencil 0.22 against station 0.12 relative luminance; night: 0.18 against 0.38), so a greyscale print still tells them apart.

### 1.4 Data colours

**Two-series comparison (static vs dynamic)** — the core visual of the product:

- Static / worst case: `--neutral-series` (pencil)
- Dynamic / with envelopes: `--accent` (station ink)

Never colour the static series red. It is not wrong, it is conservative.

**Production vs consumption** (national view): `--data-production` blue (`#24508A` paper, `#5B9BE0` night) and `--data-consumption` amber (`#B07818` paper, `#E0A93B` night).

**Production mix** (categorical, max 6). Searched with the dataviz validator for colour-vision separation; the previous set reached only ΔE 4.2 (paper) and 3.1 (night):

| Source | Paper | Night |
|---|---|---|
| Hydro | `#24508A` | `#5B9BE0` |
| Nuclear | `#8E5CB0` | `#D46FA8` |
| Wind | `#2E8D7F` | `#7FD0B8` |
| Solar | `#B07818` | `#E0A93B` |
| Thermal | `#8B3A28` | `#C05A3A` |
| Other | `#B4B9BE` | `#4A5258` |

Worst all-pairs separation under protanopia/deuteranopia: ΔE 9.4 (paper) and 9.6 (night); normal vision 17.4 and 15.0. "Other" is pale (paper) or dark (night) on purpose, so it parts from the rest by lightness; it sits below 3:1, so the chart keeps its legend and table view.

**Cable and transformer loading** (sequential, 0 → 100 %+):

| Stop | Paper | Night |
|---|---|---|
| 0 % | `#A7B5AE` | `#2C3A36` |
| 50 % | `#4E8F74` | `#4E9B7E` |
| 85 % | `#D99A2B` | `#E0A74A` |
| > 100 % | `#C8432F` | `#E2685A` |

Above the rating the colour is flat, not a gradient: exceeding the rating is binary.

**Voltage** (diverging around 1.00 pu — blue/amber, never red/green):

| Stop | Paper | Night |
|---|---|---|
| ≤ 0.94 pu | `#3D6FB0` | `#5B8BD0` |
| 1.00 pu | `#7B8389` | `#CFCBC3` |
| ≥ 1.06 pu | `#D2782F` | `#E08A45` |

**Status**:

| State | Token |
|---|---|
| Firm / approved / within limits | `--accent` |
| Non-firm / limited / curtailed | `--warning` |
| Rejected / above rating | `--danger` |
| Real data | filled square mark, see 3.6 |
| Synthetic data | dashed square mark, see 3.6 |

---

## 2. Typography

**Families**

- Interface and prose: **Schibsted Grotesk** — a Scandinavian editorial grotesk, chosen because it reads like printed matter rather than like a UI kit. Bundled from `@fontsource-variable/schibsted-grotesk` (OFL-1.1).
- Readings — numbers, units, timestamps, source IDs, keyboard hints: **IBM Plex Mono**.

Monospace is for readings only. It is never used for headings, buttons, navigation or labels.

**Always on for numeric content:**

```css
font-variant-numeric: tabular-nums;
```

Without it, values jitter as the hour slider moves.

**Scale** — ratio 1.25 on a 15 px body (rem, 16 px base):

| Token | Size | Line height | Tracking | Use |
|---|---|---|---|---|
| `--text-display` | 3.0 rem (48) | 1.05 | `--track-display` −0.03 em | the one hero reading per screen |
| `--text-h1` | 1.875 rem (30) | 1.15 | `--track-h1` −0.022 em | screen title |
| `--text-h2` | 1.4375 rem (23) | 1.25 | `--track-h2` −0.015 em | region heading |
| `--text-h3` | 1.1875 rem (19) | 1.35 | `--track-h3` −0.01 em | panel or table title |
| `--text-body` | 0.9375 rem (15) | 1.55 | 0 | body copy |
| `--text-small` | 0.8125 rem (13) | 1.45 | 0 | labels, axis ticks |
| `--text-caption` | 0.75 rem (12) | 1.4 | 0 | sources, footnotes, the title block |

**Weights:** 400 body, 500 labels and buttons, 600 headings. Never 700+; heavy weights read as marketing.

**Hierarchy is three levers at once** — size, weight and ink. A label is `--text-small`/500/`--ink-2`; a value beside it is mono/500/`--ink`; its note is `--text-caption`/400/`--graphite`. If two levels differ by size alone, the step is too weak.

**Rules**

- Heading ladder: `h1` is the screen title, `h2` a region heading, `h3` the title of a panel or of a table inside it. One `--text-display` reading per screen, and nothing else at that size.
- Headings use `text-wrap: balance`; prose and captions `text-wrap: pretty`.
- Letter-spaced uppercase is not used anywhere.
- No two-tone headlines. One colour per heading.
- Maximum 68 characters per line of prose.
- Sentence case for every heading, button and label. No Title Case.
- Chart text uses the same scale: `--text-caption` for ticks and source lines, `--text-small` for labels, `--text-body` for values on the chart, `--text-h3` for the one annotated figure. Nothing in between.

---

## 3. Spacing, surfaces and the sheet

Base unit 4 px. Every margin, padding and gap is a multiple.

| Token | Value |
|---|---|
| `--space-1` | 4 px |
| `--space-2` | 8 px |
| `--space-3` | 12 px |
| `--space-4` | 16 px |
| `--space-5` | 24 px |
| `--space-6` | 32 px |
| `--space-7` | 48 px |
| `--space-8` | 64 px |

### 3.1 Depth: lines, not boxes

**Hairlines do the work, as on a drawing.** `--border` is ink at 12 % (white at 8 % at night): it defines an edge without drawing attention, and disappears when you are not looking for it. Panels are a tonal step (`--bg-surface` on `--bg-base`), not a card with a heavy outline.

**Only what floats casts a shadow**: popovers, dialogs, the command palette, tooltips, map overlays. `--shadow-popover` is a 1 px ring plus one soft depth. A panel that sits *in* the page never has a shadow. No glows, no accent-coloured shadows, no decorative gradients.

**Radius**: `--radius-sm` 3 px (inputs, buttons, marks, segmented controls), `--radius-md` 6 px (panels, the map legend), `--radius-lg` 10 px (dialogs, the command palette). Nested corners are concentric: outer = inner + padding.

### 3.2 Three weights inside a screen

A long screen is a document, not a tray of cards. Each region declares which of three weights it carries, and a screen that uses only one of them reads as assembled by machine:

| Weight | How it looks | What it carries |
|---|---|---|
| Lead — `.region` | no surface and no border: it sits on the paper, parted from the next by one hairline and `--space-6` | the screen's own divisions. The default |
| Panel — `.block` | `--bg-surface`, a hairline, `--radius-md`, `--space-5` of padding | a self-contained instrument inside a region |
| Well — `.well` | `--bg-inset`, a hairline above and below, no radius, across the whole content column | one dense band per screen: a long table, a year of hours |

Two panels of equal weight side by side is the card stack again: change one of the weights, or make it a single panel. A region's heading is `h2`, a panel's or a well's is `h3`.

### 3.3 Readings, not tiles

A group of figures is a **data sheet**: one row per reading, the label on the left in `--ink-2`, the value on the right in mono with its unit, the note under it in `--graphite`, rows parted by hairlines. Never a grid of identical boxes. Two or three columns of rows are allowed when the panel is wide.

A set of checks is a **protocol**: one row per check, a status mark (✓ / ✕) with its word, what was measured, the measured value and the criterion it was held against.

### 3.4 The frame

- **The sheet index** — one top bar, `--topbar-h` tall: the wordmark and the site on the left, the six sheets as text links in the middle (the current one underlined in ink), and the quiet global tools on the right (search, language, theme, full screen). No sidebar, no icons in the index, no keyboard badges.
- **The title block** — the foot of every screen, `--statusbar-h` tall, cells parted by hairlines as on a drawing: sheet number and name, site, reference year, the real/synthetic legend, the course, and the key-sheet button.

| Token | Value | What |
|---|---|---|
| `--topbar-h` | 56 px | the sheet index |
| `--toolbar-h` | 44 px | a screen's own sticky section bar |
| `--statusbar-h` | 36 px | the title block |

### 3.5 The screen bar

A screen with more than three regions carries a sticky bar at the top of its scroller — `--toolbar-h` tall, at `--z-sticky` — with its sections on the left as text links (the current one underlined) and the basis of its figures on the right. A control that belongs to one region stays in that region's head row and never moves to the bar.

### 3.6 Provenance marks

What is surveyed is drawn solid; what is assumed is drawn dashed — the drafting convention for "not measured".

| Mark | Meaning |
|---|---|
| ■ filled square, `--ink-2` | real data (OpenStreetMap buildings, SMHI weather, spot prices, eSett) |
| ⬚ dashed square, `--ink-2` | synthetic data (the cable network, household behaviour) |
| ○ hollow circle | a reference (regulation, literature) |

A mark is typography — 10 px, followed by its word — never a pill or a badge.

### 3.7 Stacking

| Token | Value | What |
|---|---|---|
| `--z-sticky` | 3 | a toolbar or table header stuck inside a screen |
| `--z-overlay` | 40 | map overlays, the presenter drawer |
| `--z-dialog` | 50 | dialogs and the command palette |
| `--z-toast` | 60 | toasts, which must clear a dialog |

---

## 4. Charts

1. **Two series or fewer: no legend.** Label the series directly at the end of the line or inside the bar.
2. No vertical gridlines. Horizontal gridlines at `--border`, 1 px, and only when reading values off the axis matters.
3. Axis lines 1 px `--border`; ticks in `--text-muted`, `--text-caption`, monospace.
4. The unit goes in the axis label once (`TWh`, `kW`, `kr/kWh`), never repeated on every tick.
5. Bars: 2 px gap minimum, `--radius-sm` on the free end only, no gradients.
6. Lines: 2 px stroke, no point markers unless there are fewer than 24 points. No area fills under lines except for a single highlighted series, at 12 % opacity.
7. Every chart carries a source line underneath in `--text-caption` `--text-muted`, with its provenance mark.
8. Tooltips: `--bg-elevated`, `--shadow-popover`, `--radius-sm`, `--space-3` padding. A tooltip is mostly words ("Static study: needs reinforcement"), so it is set in the text face at `--text-small` with tabular figures; monospace for whole sentences reads as a terminal.
9. The static-vs-dynamic comparison is **one** chart, not four cards: two bars on a shared axis, pencil and ink. A figure said in the heading is not said again on the chart.
10. Text sizes come from section 2's chart scale; nothing in between.
11. Motion: a chart draws once, in 200 ms with `--ease`; it never animates on a theme or language change.

---

## 5. Maps

1. Basemap labels are **off** outside Sweden. No foreign toponyms, ever.
2. National view: Sweden at full opacity, surrounding countries at 30 % with no labels.
3. Zone labels (SE1–SE4) are flat text on the map, no boxes and no pin shapes.
4. Legends are docked in a corner panel with the same padding as any panel, not floating boxes with their own styling.
5. One interactive element at a time is highlighted; selection uses `--border-strong`, not a glow.
6. Attribution (OpenStreetMap, OpenFreeMap) is always visible in `--text-caption`.
7. **The network in plan.** The preview of the grid on Static vs DOE is a plan view — north up, no tilt — with the cables in ink, the houses as outlined footprints and the substation as the transformer symbol. The 3D view belongs to the Neighbourhood sheet, where it is explored.

---

## 6. Numbers and units

- The unit is always shown, in `--text-secondary` at `--text-small`, next to the value.
- Thousands separated by a thin space (`1 234 kW`), matching Swedish convention.
- Decimals: power to 0 or 1 place, percentages to 1 place, prices to 2 places. Never more precision than the data supports.
- Dates: `19 May 2025, 11:00`. Times are local Swedish time and this is stated once in the footer.
- Every external data block carries an "updated" timestamp.
- A figure is stated once per view. If the heading says it, the chart does not repeat it.

---

## 7. States and interaction

- **Empty states are never blank.** The connection-request screen pre-selects an example house or offers "Try an example request". No icon in a box: a sentence and an action.
- **Loading**: skeletons that match the final layout — a data sheet loads as rows, not as tiles. No spinners over existing content.
- **Errors**: say what failed, which source, and what the user can do. Never a bare "Something went wrong".
- **Focus**: 2 px `--border-strong` outline with 2 px offset on every interactive element. Keyboard navigation must be complete.
- **Hit targets**: 40 × 40 px minimum; a smaller visible control extends its hit area with a pseudo-element.
- **Every control has its states**: default, hover, active (pressed: `scale(0.98)`), focus, disabled.
- **Motion**: `--dur-micro` 120 ms for micro-interactions, `--dur-panel` 200 ms for panels, `--ease` `cubic-bezier(0.23, 1, 0.32, 1)` (ease-out). Only `transform`, `opacity` and colours are animated; nothing enters from `scale(0)`; nothing pulses, nothing loops. Actions repeated all day (keyboard shortcuts, the command palette) do not animate. Honour `prefers-reduced-motion: reduce`.

---

## 8. Voice

- Short, factual, in sentence case.
- State what the data shows; let the reader draw the conclusion.
- No exclamation marks, no "powerful", "seamless", "revolutionary", "unlock", no taglines.
- Label what is real and what is synthetic, every time, without hedging.
- Placeholder results are marked as such until the simulations produce them.

---

## 9. Accessibility floor

- Text contrast ≥ 4.5:1; large text and UI borders ≥ 3:1. Every value in section 1 has been measured.
- Colour is never the only carrier of meaning: status also has a label or an icon.
- Charts are readable in greyscale (the static/dynamic pair differs in lightness, not only hue).
- Every screen has a visible heading structure and works at 200 % zoom.

---

## 10. Token file

`src/styles/tokens.css` holds, in this order:

1. The shared scales: spacing, radius, type (sizes, line heights, tracking), motion, chrome bands, stacking.
2. The **paper** primitives on `:root` and `:root[data-theme="light"]`, and the **night** primitives on `:root[data-theme="dark"]` and, when no theme is chosen, under `prefers-color-scheme: dark`.
3. The **role** tokens, declared once on `:root`, each pointing at a primitive: `--bg-base: var(--paper)`, `--text-primary: var(--ink)`, `--accent: var(--station)`, `--neutral-series: var(--pencil)`, and so on. Components, charts (through `lib/colors.ts`) and maps read the role tokens only.

The loading and voltage stops stay plain `#rrggbb`, because `lib/colors.ts` interpolates them.

---

## 11. Review checklist

Before any screen is considered finished:

- [ ] No hard-coded colours, sizes or spacing anywhere in the diff
- [ ] Works on paper and at night
- [ ] Every number uses tabular figures and shows its unit, and is stated once
- [ ] One hero reading per screen; nothing else competes with it
- [ ] No letter-spaced uppercase, no taglines, no icons in boxes, no grids of identical tiles
- [ ] Every data block says whether it is real or synthetic, with its mark and source
- [ ] Contrast measured, not assumed
- [ ] Keyboard path complete, focus visible
- [ ] No glows, no pulsing, no gradients on data marks
- [ ] **Swap test**: with Schibsted Grotesk swapped for Inter and the sheet index for a sidebar, would the screen lose its character? It should.
- [ ] **Squint test**: blurred, the hierarchy still reads and nothing jumps out
- [ ] **Signature test**: the title block, the provenance marks, pencil-and-ink, the plan view and the protocol are each visible where they belong
- [ ] Screenshot taken on paper and at night at 1280 px and at the screen's full width, and compared with the previous version
