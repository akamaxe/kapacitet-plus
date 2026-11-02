# Data licence

The code in this repository is MIT ([LICENSE](LICENSE)). The data are not in the repository: the
computed data are published separately, as the asset `kapacitet-data.zip` of the release
[`data`](https://github.com/akamaxe/kapacitet-plus/releases/tag/data), and that archive is made
available under the [Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/)
(ODbL).

It is a database derived from these sources, each under its own terms. `data/raw/manifest.json`
inside the archive records the URL, licence, time and SHA-256 of every download.

| Source | What it gives | Terms |
|---|---|---|
| OpenStreetMap | buildings, streets, addresses, the substation's position | © OpenStreetMap contributors, ODbL 1.0 |
| SMHI | weather (STRÅNG and metobs) | CC BY 4.0 |
| SCB | household sizes | CC0 |
| eSett | settlement volumes | eSett Open Data terms |
| Elpriset just nu | day-ahead prices | Elpriser tillhandahålls av Elpriset just nu.se |

Everything else in the archive is computed by this repository's pipeline. The household load
profiles were generated offline with `richardsonpy` (GPL-3.0, the CREST model); the archive holds
its output, not its code.

**Real and synthetic.** Real: the buildings, streets, weather, prices and national volumes.
Synthetic: the low-voltage network, the transformer and fuses, and every household's load, heat
pump, electric car and solar. The addresses come from OpenStreetMap; the households attached to
them are synthetic and describe no real occupant.

Whoever shares the archive, or a database derived from it, keeps it under ODbL with this
attribution.
