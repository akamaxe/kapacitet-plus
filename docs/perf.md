# What the opening screen costs a first visit

Measured with `node web/scripts/perf.mjs` against `python -m kapacitet serve --port 8130` on this
machine's computed data: the comparison screen (`#/compare`, where the interface opens), in the
system's Chrome at 1536 × 730 with an empty cache, the median of three visits. Sizes are bytes over
the wire (compressed), in kB. The JSON behind each row is in `web/perf/`.

| | requests | total | script | API | map tiles and fonts (fetch) | largest paint | long tasks |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline, before this round (`baseline-compare.json`, 1 Nov) | 32 | 1 048 | 602 | 133 | 270 | 1 136 ms | 5, 575 ms |
| Before the change (`before-m12.json`, 2 Nov) | 41 | 1 061 | 605 | 144 | 270 | 712 ms | 3, 285 ms |
| After the change (`after-m12.json`, 2 Nov) | 46 | 1 023 | 608 | 102 | 270 | 204 ms | 1, 135 ms |

## With the workbench frame (13 Nov 2026)

The interface now opens on the start window, and a project's screens stand inside a frame (a menu
bar, a project tree, an output window, a status bar). Measured the same way, on port 8160, on a
copy of the published data:

| | requests | total | script | API | map tiles and fonts (fetch) | largest paint | long tasks |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| The start window, `/` (`frame-start.json`) | 31 | 261 | 158 | 63 | 0 | 372 ms | 0 |
| The comparison screen, `#/compare` (`frame-compare.json`) | 62 | 1 154 | 655 | 132 | 317 | 528 ms | 3, 374 ms |

What one sees on entering costs a quarter of what the comparison screen does: the start window
draws no map and no chart, so neither library is downloaded until a project is opened. The
comparison screen was not measured on the same day without the frame, so its difference from the
row of 2 November is everything added to the interface since then (the Operation screen's parts,
sign-in, the settings, the frame), not the frame alone. The frame's own code is in the
application's chunk (45 kB, 14 kB compressed); its dialogs load when the first one opens, and the
logo is two image files of 8 kB each, of which a page shows one.

## What changed

- **The chart and the map load beside the words, not before them.** The comparison screen no longer
  imports the chart library (`components/LazyChart.tsx`) or the map (`NetworkMap`, lazy) at the top:
  its headline, figures and tables render first, behind skeletons of the same height, and the two
  libraries (about 200 and 270 kB) arrive in parallel. The pure helper the option builders need,
  `axisStyle`, moved to `lib/chartStyle.ts` so importing it does not pull the library in. The total
  barely moves, and there are more requests (the libraries are now chunks of their own); what moves
  is when the screen can be read.
- **Less data.** `/network` writes coordinates to six decimals (about 0.1 m); the heatmaps, a third
  of `/showcase`, come from `/showcase/heatmaps` when their section is within a screen and a half.
- **Two preloads.** The HTML preloads the latin cut of the web font and the comparison screen's
  chunk, which the browser would otherwise find only after the stylesheet and the application had
  run (`preloadOpening` in `web/vite.config.ts`).
- **Revalidation.** The API's JSON carries a weak ETag computed from its bytes, so a repeat visit
  gets `304 Not Modified` for what it already holds. It does not change a first visit.

The binary format of the map states is untouched: `tests/test_state_encoding.py` and the Vitest
decoder pin it from both sides.

## Budgets

- **CI** (`web/e2e/ci/budget.spec.ts`): the entry chunk stays under 80 kB compressed (66 kB when the
  budget was set, 69 kB with the frame), and a screen with no chart or map, the setup screen of a
  fresh clone, downloads neither library.
- **Local** (`web/e2e/local/budget.spec.ts`): the comparison screen's largest paint stays under
  1 500 ms (204 ms when the budget was set, 528 ms with the frame on a machine that was also
  serving the tests), and the heatmaps are requested only once their section nears.

Numbers vary between runs and machines; the budgets are set well above the measured values so a
regression shows and noise does not.
