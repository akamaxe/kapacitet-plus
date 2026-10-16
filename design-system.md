# Kapacitet+ — Design System

Single source of truth for the visual language of Kapacitet+.
**Rule for any agent or developer working on this repository: never hard-code a colour, font size, spacing value or radius. Use the tokens defined here. If a value you need is missing, add it to this file first, then use it.**

Design intent: this is an engineering instrument for grid operators, not a sci-fi console. It should look closer to a well-made measurement tool than to a startup landing page. Restraint over decoration; numbers over slogans.

---

## 1. Colour

### 1.1 Principles

- **One accent.** Teal-green is the only accent. It marks the dynamic/envelope side of the product and primary actions. If everything is accented, nothing is.
- **Colour carries meaning.** Amber = limited or near a limit. Red = limit exceeded or rejected. Neutral grey-blue = the static/worst-case baseline. Never use these three decoratively.
- **Both themes are first class.** Dark is the default, light is a designed second palette, not an inversion.

### 1.2 Dark theme (default)

| Token | Value | Use |
|---|---|---|
| `--bg-base` | `#0E1317` | Page background |
| `--bg-surface` | `#171D22` | Panels, cards, side rail |
| `--bg-elevated` | `#1E252B` | Popovers, tooltips, dropdowns |
| `--bg-inset` | `#0A0E11` | Map canvas, code blocks, wells |
| `--border` | `#2A333A` | 1 px dividers and card outlines |
| `--border-strong` | `#67747C` | Focused or selected outlines (3.9:1, the floor for a focus ring) |
| `--text-primary` | `#E8EDF0` | Headings, values (15.8:1) |
| `--text-secondary` | `#A8B4BC` | Body copy, labels (8.8:1) |
| `--text-muted` | `#7A878F` | Captions, source notes (5.1:1) |
| `--accent` | `#2E9E8F` | Primary actions, dynamic/DOE series (5.7:1) |
| `--accent-hover` | `#39B5A4` | Hover and active state (7.4:1) |
| `--accent-quiet` | `#1C4A45` | Accent fills behind text, selected nav |
| `--on-accent` | `#0E1317` | Text on an accent fill (5.7:1) |
| `--warning` | `#D9A33A` | Limited / non-firm / nearing limit (8.2:1) |
| `--warning-quiet` | `#4A3A17` | Warning fills |
| `--danger` | `#E05C4B` | Above rating, rejected (5.2:1) |
| `--danger-quiet` | `#4A211B` | Danger fills |
| `--neutral-series` | `#8A99A4` | Static / worst-case series (6.4:1) |

All text ratios above are measured against `--bg-base` and pass WCAG 2.1 AA for normal text.

`--border-strong` and the light `--text-muted` were corrected on 2026-10-16 after measuring
them rather than assuming: the focus ring that section 7 builds from `--border-strong` came
out at 1.96:1 in dark and 1.70:1 in light, against the 3:1 that section 9 requires of a UI
border, and the light `--text-muted` reached 4.6:1 only against a white panel, not against
the page. The values in the tables are the corrected ones.

### 1.3 Light theme

| Token | Value | Use |
|---|---|---|
| `--bg-base` | `#F7F8F8` | Page background |
| `--bg-surface` | `#FFFFFF` | Panels and cards |
| `--bg-elevated` | `#FFFFFF` + shadow | Popovers |
| `--bg-inset` | `#EDF0F1` | Map canvas, wells |
| `--border` | `#DCE1E4` | Dividers |
| `--border-strong` | `#808D95` | Focus and selection (3.2:1) |
| `--text-primary` | `#151B20` | Headings and values (16.3:1) |
| `--text-secondary` | `#4C585F` | Body copy (6.9:1) |
| `--text-muted` | `#67727A` | Captions (4.6:1 on a panel, 4.6:1 on the page) |
| `--accent` | `#1F7D71` | Actions, dynamic series (5.0:1) |
| `--accent-hover` | `#176258` | Hover |
| `--accent-quiet` | `#DFF0ED` | Selected nav, accent fills |
| `--on-accent` | `#FFFFFF` | Text on accent fill |
| `--warning` | `#8A6314` | Warning text (5.4:1); fills use `#E8A62E` |
| `--danger` | `#B33F30` | Danger text (5.7:1); fills use `#E05C4B` |
| `--neutral-series` | `#5E6E79` | Static series (5.3:1) |

### 1.4 Data colours

**Two-series comparison (static vs dynamic)** — the core visual of the product:

- Static / worst case: `--neutral-series`
- Dynamic / with envelopes: `--accent`

Never colour the static series red. It is not wrong, it is conservative.

**Production vs consumption** (national view):

- Production: `#3E8FB0` (blue)
- Consumption: `#D9A33A` (amber)

**Production mix** (categorical, max 6, distinguishable under deuteranopia):

| Source | Dark | Light |
|---|---|---|
| Hydro | `#3E8FB0` | `#2F7694` |
| Nuclear | `#B07FD1` | `#8E5CB0` |
| Wind | `#52B3A4` | `#2E8D7F` |
| Solar | `#E0A93B` | `#B5801C` |
| Thermal | `#C8705F` | `#A2503F` |
| Other | `#8A99A4` | `#5E6E79` |

**Cable and transformer loading** (sequential, 0 → 100 %+):

| Stop | Colour |
|---|---|
| 0 % | `#24424A` (dark) / `#D7E6E3` (light) |
| 50 % | `#2E9E8F` |
| 85 % | `#D9A33A` |
| > 100 % | `#E05C4B` (flat, no gradient — exceeding the rating is binary) |

**Voltage** (diverging around 1.00 pu — blue/amber, never red/green):

| Stop | Colour |
|---|---|
| ≤ 0.94 pu | `#4A7FC1` |
| 1.00 pu | `#D4D9DC` (dark) / `#6B777E` (light) |
| ≥ 1.06 pu | `#D9772F` |

**Status**:

| State | Token |
|---|---|
| Firm / approved / within limits | `--accent` |
| Non-firm / limited / curtailed | `--warning` |
| Rejected / above rating | `--danger` |
| Real data badge | Filled `--accent` dot |
| Synthetic data badge | Hollow `--text-muted` dot |

---

## 2. Typography

**Families**

- UI and prose: **IBM Plex Sans**
- Numbers, units, timestamps, source IDs, keyboard hints: **IBM Plex Mono**

Monospace is for data only. It is never used for headings, buttons, navigation or decorative labels.

**Always on for numeric content:**

```css
font-variant-numeric: tabular-nums;
```

Without it, values jitter as the hour slider moves.

**Scale** (rem, 16 px base):

| Token | Size | Line height | Use |
|---|---|---|---|
| `--text-display` | 3.0 rem | 1.05 | The one hero number per screen |
| `--text-h1` | 1.75 rem | 1.2 | Screen title |
| `--text-h2` | 1.25 rem | 1.3 | Panel title |
| `--text-h3` | 1.0 rem | 1.4 | Sub-heading, card title |
| `--text-body` | 0.9375 rem | 1.55 | Body copy |
| `--text-small` | 0.8125 rem | 1.45 | Labels, axis ticks |
| `--text-caption` | 0.75 rem | 1.4 | Sources, footnotes |

**Weights:** 400 body, 500 labels and buttons, 600 headings. Never 700+; heavy weights read as marketing.

**Rules**

- Letter-spaced uppercase is allowed in exactly **one** place: the sidebar section label. Nowhere else.
- No two-tone headlines (a second line in the accent colour). One colour per heading.
- Maximum 68 characters per line of prose.
- Sentence case for every heading, button and label. No Title Case.

---

## 3. Spacing, radius, elevation

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

- Card padding: `--space-5`. Dense data panels: `--space-4`. Consistent across every panel — mismatched padding is the fastest way to look assembled rather than designed.
- Panel gap in a grid: `--space-4`.
- Radius: `--radius-sm` 4 px (inputs, badges, segmented controls), `--radius-md` 8 px (cards, panels), `--radius-lg` 12 px (dialogs, the command palette). Nothing is fully rounded except status dots and the real/synthetic chips.
- Elevation: borders do the work. One shadow only, for things that float above the page — popovers, dialogs, the command palette, map overlays: `0 8px 24px rgba(0,0,0,0.35)` dark, `0 6px 20px rgba(21,27,32,0.12)` light. A panel that sits *in* the page never has a shadow; give it a border. **No glows.** No `box-shadow` in an accent colour, and no decorative gradient behind the page or a card.

**Chrome bands.** The frame's three fixed rows are tokens, because a sticky region inside a
screen has to know how much of the viewport is already spoken for:

| Token | Value | What |
|---|---|---|
| `--topbar-h` | 72 px | breadcrumb and global actions |
| `--toolbar-h` | 48 px | a screen's own sticky controls |
| `--statusbar-h` | 32 px | model, reference year, shortcut hint |

**Stacking.** Four named steps, so nothing is layered by trial and error:

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
3. Axis lines 1 px `--border`; ticks in `--text-muted`, `--text-small`, monospace.
4. The unit goes in the axis label once (`TWh`, `kW`, `kr/kWh`), never repeated on every tick.
5. Bars: 2 px gap minimum, `--radius-sm` on the top corners only, no gradients.
6. Lines: 2 px stroke, no point markers unless there are fewer than 24 points. No area fills under lines except for a single highlighted series, at 12 % opacity.
7. Every chart carries a source line underneath in `--text-caption` `--text-muted`, with the real-data or synthetic badge.
8. Tooltips: `--bg-elevated`, 1 px `--border`, `--radius-sm`, `--space-3` padding. Values in monospace, aligned.
9. The static-vs-dynamic comparison is **one** chart, not four cards: two bars on a shared axis with the multiplier annotated between them.

---

## 5. Maps

1. Basemap labels are **off** outside Sweden. No foreign toponyms, ever.
2. National view: Sweden at full opacity, surrounding countries at 30 % with no labels.
3. Zone labels (SE1–SE4) are flat text on the map, no boxes and no pin shapes.
4. Legends are docked in a corner panel with the same padding as any card, not floating boxes with their own styling.
5. One interactive element at a time is highlighted; selection uses `--border-strong`, not a glow.
6. Attribution (OpenStreetMap, OpenFreeMap) is always visible in `--text-caption`.

---

## 6. Numbers and units

- The unit is always shown, in `--text-secondary` at `--text-small`, next to the value.
- Thousands separated by a thin space (`1 234 kW`), matching Swedish convention.
- Decimals: power to 0 or 1 place, percentages to 1 place, prices to 2 places. Never more precision than the data supports.
- Dates: `19 May 2025, 11:00`. Times are local Swedish time and this is stated once in the footer.
- Every external data block carries an "updated" timestamp.

---

## 7. States and interaction

- **Empty states are never blank.** The connection-request screen pre-selects an example house or offers "Try an example request".
- **Loading**: skeletons that match the final layout, no spinners over existing content.
- **Errors**: say what failed, which source, and what the user can do. Never a bare "Something went wrong".
- **Focus**: 2 px `--border-strong` outline with 2 px offset on every interactive element. Keyboard navigation must be complete.
- **Hit targets**: 40 × 40 px minimum.
- **Motion**: 120 ms for micro-interactions, 180 ms for panels, `cubic-bezier(0.2, 0, 0, 1)`. Nothing pulses, nothing loops. Honour `prefers-reduced-motion: reduce`.

---

## 8. Voice

- Short, factual, in sentence case.
- State what the data shows; let the reader draw the conclusion.
- No exclamation marks, no "powerful", "seamless", "revolutionary", "unlock".
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

Create `src/styles/tokens.css` with this structure and import it once, at the root. Components reference variables only.

The block below is the core set. `tokens.css` also defines the line heights (`--lh-display`
… `--lh-caption`), the data scales of section 1.4 (`--cat-1` … `--cat-6`, `--load-0/50/85`,
`--load-over`, `--volt-low/mid/high`, `--data-production`, `--data-consumption`), the
semantic fills (`--warning-fill`, `--danger-fill`), and `--night`, the RGB triple the map's
daylight overlay multiplies. Those are tokens like any other: a value missing from here is
still added to this file first.

```css
:root {
  /* spacing */
  --space-1: 0.25rem; --space-2: 0.5rem;  --space-3: 0.75rem; --space-4: 1rem;
  --space-5: 1.5rem;  --space-6: 2rem;    --space-7: 3rem;    --space-8: 4rem;
  /* radius */
  --radius-sm: 4px; --radius-md: 8px; --radius-lg: 12px;
  /* type */
  --font-sans: "IBM Plex Sans", system-ui, sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, monospace;
  --text-display: 3rem;   --text-h1: 1.75rem;  --text-h2: 1.25rem;
  --text-h3: 1rem;        --text-body: 0.9375rem;
  --text-small: 0.8125rem;--text-caption: 0.75rem;
  /* motion */
  --ease: cubic-bezier(0.2, 0, 0, 1);
  --dur-micro: 120ms; --dur-panel: 180ms;
  /* chrome bands */
  --topbar-h: 72px; --toolbar-h: 48px; --statusbar-h: 32px;
  /* stacking */
  --z-sticky: 3; --z-overlay: 40; --z-dialog: 50; --z-toast: 60;
}

:root[data-theme="dark"] {
  --bg-base: #0E1317; --bg-surface: #171D22; --bg-elevated: #1E252B; --bg-inset: #0A0E11;
  --border: #2A333A; --border-strong: #67747C;
  --text-primary: #E8EDF0; --text-secondary: #A8B4BC; --text-muted: #7A878F;
  --accent: #2E9E8F; --accent-hover: #39B5A4; --accent-quiet: #1C4A45; --on-accent: #0E1317;
  --warning: #D9A33A; --warning-quiet: #4A3A17;
  --danger: #E05C4B;  --danger-quiet: #4A211B;
  --neutral-series: #8A99A4;
}

:root[data-theme="light"] {
  --bg-base: #F7F8F8; --bg-surface: #FFFFFF; --bg-elevated: #FFFFFF; --bg-inset: #EDF0F1;
  --border: #DCE1E4; --border-strong: #808D95;
  --text-primary: #151B20; --text-secondary: #4C585F; --text-muted: #67727A;
  --accent: #1F7D71; --accent-hover: #176258; --accent-quiet: #DFF0ED; --on-accent: #FFFFFF;
  --warning: #8A6314; --warning-quiet: #FBF0D8;
  --danger: #B33F30;  --danger-quiet: #FBE4E0;
  --neutral-series: #5E6E79;
}
```

---

## 11. Review checklist

Before any screen is considered finished:

- [ ] No hard-coded colours, sizes or spacing anywhere in the diff
- [ ] Works in both themes
- [ ] Every number uses tabular figures and shows its unit
- [ ] One hero number per screen; nothing else competes with it
- [ ] Uppercase letter-spaced text appears only in the sidebar section label
- [ ] Every data block says whether it is real or synthetic, with a source
- [ ] Contrast measured, not assumed
- [ ] Keyboard path complete, focus visible
- [ ] No glows, no pulsing, no gradients on data marks
- [ ] Screenshot taken in both themes at 1280 px and 1920 px and compared with the previous version
