# A study's inputs and what a quarter writes

A study (`kapacitet study init <dir>`) is a folder of its own, outside the code and outside any
git work tree. What the DSO hands over goes in `<dir>/inputs/`; `study.toml` says how it is
computed and published (every key, with its default, is written by `study init`).

Every CSV file below may use `,` or `;` between fields and a decimal point or comma, in UTF-8 with
or without a byte-order mark, with a header row. Column names are not case sensitive. A time is
ISO 8601 with its UTC offset (`2025-03-30T03:00:00+02:00`, or `Z`); a time without one is refused,
since it names two instants on the night the clocks go back. A blank value is "not given" and is
never read as zero.

## The network

| file | columns | read by |
| --- | --- | --- |
| `network/contract/<station>/` | `buses.csv`, `lines.csv`, `transformer.csv`, `customers.csv` and optionally `equipment.csv`: one folder per secondary substation, in the format of [network-export-contract.md](network-export-contract.md), with an `origin.json` saying what produced it (without one the network is labelled unverified) | `study import` |
| `network/cgmes/` | CGMES 2.4.15 files (EQ, TP, SSH and, for the map, GL; `.xml`, `.rdf` or `.zip`) instead of contract folders: one station per in-service transformer that steps down to 1 kV or less | `study import --cim` |
| `cim_customers.csv` | `energyconsumer_mrid`, `customer_id`, `fuse_a`; optionally `phase`, `footprint_m2`, `annual_kwh`. Required with CGMES: an EnergyConsumer in a station that it does not name is an error | `study import --cim` |
| `stations.csv` | `station_id`, `name`, `trafo_mrid` (CGMES: which transformer is which station), `feeder_id` (the station's feeder in `upstream.csv`), `hidden` (`true`, `ja`, `1` or `x`: never published), `zone` (the DSO's own area name, which a publication at `[disclosure] level = "zone"` groups by) | import, collect, publish |
| `equipment.csv` | what is already connected, per `customer_id`: PV kWp, AC rating and phase, battery, EV charger and its phases, heat pump, source and date ([network-export-contract.md](network-export-contract.md)). The existing PV is firm in every scenario | `study build` |

## Measurements

| file | columns | read by |
| --- | --- | --- |
| `meters/*.csv` (or `.csv.gz`) | `customer_id` or `meter_id`, `start` (the quarter-hour's start), `kwh_import`; optionally `kwh_export` and `v_l1`, `v_l2`, `v_l3` (V). An hour counts only with all four of its quarter-hours; a wrong daylight-saving offset, a time off the 15-minute grid, a negative or unreadable value and conflicting copies are left out and counted | `study build`, `study validate` |
| `mv/<station>.csv` | `start`, `vm_pu` (the measured MV voltage at the station's transformer, per unit); optionally `trafo_p_kw` and `trafo_q_kvar` (the station meter). Readings within an hour are averaged to that hour. Without this file, the metered hours are replayed at both ends of the `[mv]` band | `study validate` |
| `dppower/snapshot.csv` | `start` (the hour of the snapshot), `mrid`, `kind` (`bus`, `line` or `transformer`), `vm_pu` (for a bus), `loading_pct` (for a line or the transformer): a load flow from dpPower, compared element by element with this model solving the same hour, matched by mRID to the contract's `bus_id` and `line_id` and the transformer's mRID | `study validate` |
| `dppower/static.csv` | `customer_id`, `gen_kw` and/or `load_kw`: the DSO's own static capacity per customer, compared with this model's (generation at the high end of the band, consumption at the low end) | `study validate` |

## The grid above the stations

| file | columns | read by |
| --- | --- | --- |
| `upstream.csv` | `level` (`feeder`, `substation` or `regional`), `id`, `parent_id` (a feeder's is a substation, a substation's a regional grid), `gen_headroom_kw`, `load_headroom_kw`, `source`, `as_of`: one row per node above the stations. A station's published value is the lowest of its own capacity and each level's headroom (`limited_by`); a headroom several stations share is not added up (`shared_by`); a blank headroom is "not entered", never guessed, and the chain is then marked incomplete | `study collect` |

The upstream caps apply after the computation, so a change of `upstream.csv` or `stations.csv`
builds no station again: `study collect` applies the current files.

## What a quarter writes: `runs/<quarter>/`

| step | files |
| --- | --- |
| `study collect --quarter 2026Q4` | `stations.parquet` (one row per station, the upstream caps applied), `inputs.json` (what each station's figures were computed from: the digests of its inputs, the upstream file, the method and the code) |
| `study review` | `review.csv` and `review.machine.csv`: the stations with the least margin first (the smaller published figure; a station without one comes first), with `flags` saying what to look at |
| the reviewer | `corrections.csv`: `station_id`, `field`, `value`, `reason`, `by`. A correction is an overlay shown beside the computed value, never in its place, and can only make a figure more cautious: `generation_kw` or `consumption_kw` lowered, `hidden` set to true, or a `note`. One that cannot apply is listed and not applied |
| `study sign --by "name, role"` | `signoff.json`: the sha256 of `study.toml`, the run's files and every station's results, sealed as a `publication` in the study's own chain (`evidence/seals.jsonl`) |
| `study publish` | `publication/`: `capacity.csv` (the `[output]` dialect) and `capacity.machine.csv` (RFC 4180), `capacity.geojson`, `grunder_for_berakning.md`, `validation_report.sv.md`, `manifest.json` (each file's sha256 and the sign-off); `disclosure_report.csv` beside it, which says which station is in which cell and is never published |
| `study diff --from 2026Q3 --to 2026Q4` | `changes.csv`: one row per station and figure that changed, the inputs that changed (`causes`) and a sentence in Swedish |

A publication is signed only while every file the signature covers is unchanged; otherwise every
file of it says UTKAST (draft). A study pinned to another method version than the code's, or with
a station computed by another, is not published. The conditional (envelope) column appears only
with `[method] approval_reference` set, with a value only for the stations whose envelopes
verified on every hour.

What may be shown is set in `[disclosure]`: the level (`station`, `grid` in SWEREF 99 TM squares
of `grid_m`, or `zone`) and `min_customers` (k, 5 by default). A cell with fewer than k customers
is merged upward (the station, then its square, doubled up to 64 × `grid_m`, then its zone, then
the whole study) until it holds k; one that cannot is not published. A merged cell shows the lowest
figure of its stations, never a sum. Hidden stations are left out and never count towards k.

The CSV dialects: `sv-excel` is `;` between fields, a decimal comma, UTF-8 with a byte-order mark
and CRLF, which Excel opens as it is on a Swedish computer; `rfc4180` is `,` and a decimal point,
without the mark. The `.machine.csv` copy is always RFC 4180, with the same columns.

## Connection cases: `cases/<batch>/`

`study cases <dir> cases.csv` checks a batch of cases, one row each:

| column | meaning |
| --- | --- |
| `case_id` | the DSO's case number: letters, digits, `-`, `_` and `.`; it names the case's files |
| `received_at` | when the application came in, ISO 8601 (a date is enough) |
| `connection_point_id` | the house's `customer_id` in the station contracts |
| `pv_kwp`, `pv_kw_ac`, `pv_phases` | the solar panels (kWp), the inverter (kW AC; blank: kWp over the DC/AC ratio) and its phases (1 or 3; blank: single-phase up to `pv_single_phase_max_kva`) |
| `ev_kw`, `ev_phases` | the charger and its phases (blank: the small one single-phase) |
| `hp` | a new heat pump (1/0, ja/nej, true/false) |
| `bat_kwh` | a home battery, with solar only |
| `decision_dso` | optional: what the DSO decided, for `--compare-decisions` |

Each station's cases are checked by a process of its own, across the station's MV band: the solar
at both ends of `[mv]`, the lower one counting, and the consumption at the low end, each against
the "existing" baseline the station's build precomputed on the same model at that end, or against
the station's synced queue. The proposal is `firm`, `nonfirm` or `review`, never a refusal.

| file | content |
| --- | --- |
| `verdicts.csv` (+ `.machine.csv`) | one row per case: the proposal, `reason_codes` and `reason_sv`, the curtailment on average and in one year in ten, `fuse_ok`, `phases_ok`, the MV end that binds, whether the signed rule covers it (`rule`), the assessment id, its seal's digest, the method version and the station's fingerprint; a case that cannot be checked has its `error` instead |
| `annex/<case>.md`, `.json` | the case's annex in Swedish (the reasons are the interface's own `req.reason.*` and `inbox.reason.*` from `sv.ts`) |
| `drafts/<case>/tidsplan.md` | the time plan, with `[cases] processing_days` |
| `drafts/<case>/status_3m.md` | the status three months after the application, for a case a person has to decide |
| `drafts/<case>/forstarkning.md` | when the static study rejects a part: the next standard transformer, checked again with it |
| `confusion.csv` | with `--compare-decisions`: proposals against `decision_dso` |

Running a batch again reuses each case's assessment (its id is a digest of the case, the station's
fingerprint and the queue it met). With `[cases] protection = "kapacitet"` the annex adds the
house's fault current from Kapacitet+'s own short-circuit check; by default (`"dppower"`) it says
the check stays in dpPower. `study sign-rule --by "name, role" --kinds pv,ev --max-pv-kwp 10
--valid-until 2027-06-30` writes `rule_signature.json`, sealed, and each row says whether that
signed rule covers the case.

`study sync <dir> --approved approved.csv [--pending pending.csv]` rebuilds every built station's
queue from the DSO's own list: the approved file has a case's columns plus `decision` (`firm` or
`nonfirm`) and `decided_at`. The connections are reserved in the order they were approved, at
each end of the band, and re-verified on every weather year; one that fails is kept and flagged in
`cases/sync-<name>/sync_report.csv`, and one taken off the list leaves the grid.

## The portfolio and the options: `runs/<quarter>/`

`study portfolio <dir> --run 2026Q4` and `study options <dir> --run 2026Q4` read a collected
quarter. Neither is published: they are for the DSO's own planning, and Kapacitet+ only proposes.
Both are internal to the DSO and list every station by name, including those the study hides
under its disclosure threshold (`hidden` is only a column); they never go into the published files.

| file | content |
| --- | --- |
| `portfolio.csv` (+ `.machine.csv`) | one row per station, the least margin per customer first (`rank`; a station without a figure on either side first, ties by station id). The margins are the published generation and consumption capacity, after the upstream caps and the reviewer's corrections, and each per customer. The risk: the generation capacity at each end of the MV band and the binding end, the hours today's grid already breaks a limit, the verification, and the metered customers' peak against Velander's estimate for the same customers. The trend: `pending_cases` (cases in `cases/*/verdicts.csv` without `decision_dso`, and pending rows of a sync report, less any case a sync report approved), the existing equipment and its `connected_on` dates, and the station's future scenarios when it has a `scenarios.json` |
| `portfolio_sv.md` | the same table in Swedish |
| `options.csv` (+ `.machine.csv`) | one row per station, the three options below, the `recommendation` (`reinforce`, `condition` or `wait`) or, without one, `why_none` |
| `options/<station>.sv.md` | a Swedish note per station |

The three options of a station:

- **Reinforce now:** the next of pandapower's standard transformers for the same voltages, with
  the station's capacities computed again on it by its own process (`kapacitet station options`),
  hourly over the build's weather years and orders: generation at both ends of the band and
  consumption at the low end. Its price is the DSO's own, `[costs] transformer_swap_sek` by the
  new unit's kVA; Ei's norm values are given beside it only as a labelled reference.
- **Connect with conditions:** the station's envelopes at the per-installation curtailment budget,
  only with `[method] approval_reference` and only when they verify on every hour, with their mean
  curtailed energy a year, valued at `[costs] curtailment_sek_per_kwh`.
- **Wait:** what deferring the swap `[costs] deferral_years` is worth (one regulatory period by
  default) at Ei's real rates in `config/assumptions.toml` (4.53 % for 2024–2027, and the
  preliminary 1.99 % and 4.57 % for 2028–2031), and the net after the curtailment meanwhile: the same
  arithmetic as the business case of the reference network (`engine/summaries.deferral_case`).

A price left out is written `not entered`, never guessed, and no option is recommended without
every input it rests on. The proposal is `wait` when nobody is waiting and both margins are above
zero; otherwise `condition` when the envelopes verify and the net at the rate in force is above
zero, and `reinforce` when it is not or when there are no verified envelopes; none when the bigger
transformer adds no capacity on either side. Each station's computation is kept in
`stations/<id>/options/<quarter>.json` and reused while the station's build and the method
approval are the same.
