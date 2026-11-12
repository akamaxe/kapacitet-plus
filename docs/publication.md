# Publishing the capacity: the embeddable map and the publication API

A study's figures reach the public through `kapacitet study publish`, run after `study sign`. It
writes `runs/<run>/publication/` (a run is a quarter such as `2026Q4` or a month such as `2026-11`):

| file | what it is |
| --- | --- |
| `capacity.csv`, `capacity.machine.csv` | one row per published cell, in the `[output]` dialect and in RFC 4180 |
| `capacity.geojson` | the same cells on the map, WGS84 |
| `grunder_for_berakning.md` | the basis of calculation, in Swedish, every value from the data |
| `validation_report.sv.md` | the model against the DSO's measurements, and each station's verdict against the DSO's own criteria (`[validation]`): one outside them is not published |
| `embed/index.html`, `embed/embed.js`, `embed/embed.css` | the embeddable map |
| `manifest.json` | what was published, from what, each file's sha256, the sign-off and its public key |

`runs/<run>/disclosure_report.csv`, which says which station is in which cell, stays beside the
folder and is never published. Every file of a publication that is not signed, or that changed
after it was signed, says UTKAST.

The two Swedish texts of a draft also say that no native speaker has reviewed their language
(`kapacitet/language.py`). Nobody has: read them before you sign, because the sentence leaves with
the signature, which is where the DSO takes the text as its own.

## What a publication can still give away

A cell holds at least `min_customers` customers, and a small cell's figures are rounded down
(`[disclosure] round_kw`, `round_below_customers`; the row's `rounded_to_kw` says to what). Neither
hides a change. Publications are public and follow one another, so anyone can subtract one from
the next: when a cell's figure drops, something was connected there since the last run, and in a
cell of a few houses the neighbours may know which. Rounding makes that difference coarser (a
connection smaller than the step often leaves the figure where it was), and merging cells makes it
less attributable; neither removes it. A DSO that cannot accept it publishes at a coarser level
(`level = "grid"` or `"zone"`), with a higher `min_customers`, or less often. The defaults here are
this project's, not the result of a disclosure analysis of any DSO's network.

## The embeddable map

The page in `embed/` draws the published cells of `../capacity.geojson` as SVG in a Web Mercator
projection fitted to their extent, coloured on a sequential ramp by the available capacity for new
generation, with a switch to consumption (and to the three-phase columns when the publication has
them). The legend's thresholds come from the data; a cell without a value (not computed, or a
finding) is hatched. A cell opens its figures in the panel by click, or by Tab and Enter: generation,
consumption, the conditional and three-phase columns when they exist, the cell id, the period and
the method version. The map zooms and pans with its buttons, the mouse wheel, dragging, and `+`,
`-` and the arrow keys. A cell that is a zone or the whole study has no place on the map and is
listed in the table.

The header shows the period, the method version and the role of whoever signed (only the part of
`--by "name, role"` after the comma, never the name). While the publication is a draft, UTKAST is
written across the map. The table of every cell is in the page itself: a browser without script
shows it at once, a screen reader reads it, and "Visa som tabell" shows it to everyone else.
Swedish is the default; `?lang=en` gives English.

The page has no library, no CDN, no web font and no inline script or style, so it runs under the
strictest policy:

```
Content-Security-Policy: default-src 'self'
```

It reads only `../capacity.geojson` and `../manifest.json` and makes no request outside its
folder, unless the study names a base map.

### A base map

```toml
[publication]
map_tiles = "https://tiles.example/{z}/{x}/{y}.png"
map_attribution = "© the tile provider"
```

With `map_tiles`, `embed.js` draws that raster tile layer under the cells and the map credits
`map_attribution`. `study check` (and every other command) refuses a template that is not https,
that lacks `{z}`, `{x}` or `{y}`, or that comes without its attribution. The tiles are requested by
the visitor's browser from that host, so the hosting policy must allow it:
`Content-Security-Policy: default-src 'self'; img-src 'self' https://tiles.example`. Use a tile
service whose terms allow it; OpenStreetMap's own tile servers do not allow heavy use.

### Hosting it

Copy the whole `publication/` folder to the website, keeping `embed/` inside it (the page reads its
data as `../`), and embed the page:

```html
<iframe src="https://www.example-dso.se/kapacitet/2026Q4/publication/embed/index.html"
        title="Ledig kapacitet i elnätet" width="100%" height="760" loading="lazy"
        style="border:0"></iframe>
```

Add `?lang=en` to the address for the English page. Serve the folder with the policy above, and
do not send `X-Frame-Options: DENY` (or `frame-ancestors 'none'`) for it, or the iframe stays empty.
Publish a new run in a new folder, or replace the old one whole: the page and its data belong
together.

## The publication API

A partner that wants the data, or a site that prefers to frame the map from Kapacitet+ itself, can
read the signed publications from the server. It is off by default; turn it on with

```
KAPACITET_PUBLICATIONS=1
KAPACITET_STUDY_DIR=D:\kapacitet-studies     # the folder that holds the studies
```

and restart `kapacitet serve`. `kapacitet instance check` reports the setting and how many studies
have a signed publication.

| route | answer |
| --- | --- |
| `GET /api/v1/publications` | each study with a signed publication: `id`, `dso`, `latest_run`, `signed_at`, `published_at`, `method_version`, `period`, `files`, `embed` (the page's address) and `runs` (every signed run, the latest first) |
| `GET /api/v1/publications/{study}/{run}/{file}` | one file: `run` is `2026Q4`, `2026-11` or `latest` (the latest signed run in time), `file` is `manifest.json` or a file the manifest lists, `embed/index.html` among them |

The routes are reads, need no sign-in (the publications are public by design) and count against a
rate limit of 120 a minute per address. A publication is served only while it is signed: its
manifest says `signerad`, the sign-off it names is the one in `runs/<run>/signoff.json` and still
verifies in the study's seal chain, the manifest belongs to that study, and every file is what the manifest lists. The state served is the one the last `study publish` wrote: after recomputing a signed run, publish again. A draft answers 404
`no_publication`; a file outside the publication (`..`), one the manifest does not list and
`disclosure_report.csv` answer 404 `publication_file`. With the variable off, every route answers
404 `publications_off`, which says how to turn it on.

Each file carries its sha256 from the manifest as a strong `ETag` (the manifest itself, its own
sha256), and a request with `If-None-Match` naming it is answered 304. The page is sent with
`Content-Security-Policy: default-src 'self'` (and the tile host when the study names one), so

```html
<iframe src="https://kapacitet.example-dso.se/api/v1/publications/nb-2026/latest/embed/index.html"
        title="Ledig kapacitet i elnätet" width="100%" height="760" loading="lazy" style="border:0"></iframe>
```

always shows the latest signed run. Browsers reading the JSON from another site need that site in
`KAPACITET_CORS_ORIGINS`, as for every other route.
