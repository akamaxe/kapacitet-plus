# Where do envelopes win? The same scorecard on other networks

On the reference network (Smedslätten) the envelopes that can be issued do not connect more solar
than an hourly study without any control: the day-ahead ones give less, and the ones re-issued
during the day about the same (README, *What the forecast is worth*). That network
is one case, and a particular one: only the transformer ever limits it, and the voltage never does.
An export limit per house does little when what fills up is a transformer the whole neighbourhood
shares. It may do more where the voltage at the end of a long feeder is what limits.

This document asks that question on three other networks. **The method and the decision rule below
were written and committed before any case was computed**; the results were added afterwards, in a
later commit, so the history of this file shows the order. One thing ran before that commit: a
smoke run of the script on one case (the Landnetz at 1.06 pu, one weather year and four adoption
orders instead of eleven and twenty), to see that the chain works. The rule's text was not
touched after it, and no smoke run is reported.

## What is computed

Nothing new. The stages and the scorecard are the ones the reference network is published with
(`engine/run.py`, `engine/run_extra.py`, `engine/intraday.py`), run by `tools/envelope_networks.py`.

- **Networks.** The three of `kapacitet study demo`, pandapower's low-voltage benchmark networks:
  the Dickert network with middle feeders (60 customers), Kerber's *Dorfnetz* (58) and Kerber's
  *Landnetz* on cable (27). The fuses, roof areas and coordinates are drawn from a fixed seed
  (`study/demo.py`); weather, household load, heat pumps and EV charging are built as for any
  imported network.
- **MV voltage.** Each network at 1.00 pu, the value the reference network is published at, and at
  1.06 pu, the high end of the planning band, where a study's generation capacity is usually set
  by the voltage. That makes six cases.
- **Per case:** 20 adoption orders over the 11 weather years (2015–2025), with the same 3 %
  curtailment budget:
  1. the static study, the hourly study without control, and the envelopes with perfect foresight
     on both bases of the budget (`run.capacity`, as `hosting_summary.json`);
  2. the envelopes issued the day before, without a forecast (`run_extra.foresight`, as
     `foresight.json`);
  3. the envelopes re-issued during the day, in both variants, with the scorecard
     (`run_extra.intraday`, as `intraday.json`).
- **Which limit binds.** For each adoption order, the limit the next adopter after the hourly
  study's count breaks (voltage, cable or transformer), by `engine/station.limit_of`.

Every figure is the median over the 20 adoption orders. A case writes nothing unless its envelopes
verify in every hour, as on the reference network (`doe.require_verified`).

## The decision rule

A **case** is one network at one MV voltage. The rule is applied to each case on its own.

A case **passes** when at least one issuable basis meets all three:

1. it gives **at least 10 % more capacity than the hourly study without control** of the same
   case (the median over the same adoption orders and weather years);
2. with the budget held **per installation**: no installation loses more than 3 % of its own solar
   energy over the weather years;
3. with **0 hours above a grid limit** in the full nonlinear replay of every year.

The issuable bases are the envelopes issued the day before (`foresight.json`,
`installation.median_kw.climatological`) and the ones re-issued during the day, either variant
(`intraday.json`, `variants.<variant>.installation.median_kw`). The perfect-foresight envelopes are
not issuable and cannot make a case pass: they are reported as the ceiling. Where that ceiling is
itself less than 10 % above the hourly study, the case has no room to pass, and it is reported so.

What follows from the result:

- **If a case passes,** that kind of network, at that MV voltage, is where conditional agreements
  with envelopes are worth offering, and the result says which limit binds there.
- **If no case passes,** the envelopes leave the product's pitch. The code stays where it is.

The product's own scorecard (at least 15 % over the hourly study, every agreement within the
budget, no hour above a limit) is reported beside the rule, as `intraday.json` gives it. The
thresholds are not changed after the results are known.

## What this does not show

- The networks are benchmark networks, not a DSO's. The household load is the same synthetic pool
  as on the reference network, and the weather is Bromma's.
- The MV voltage is held at one value over the whole year in each case; the intraday replica's
  assumptions about meter readings are the ones stated in the README.
- Three networks at two voltages are six points, not a law: a pass or a fail here says where to
  look with real data, not what a DSO will find.
- pandapower draws one thing at random that the run below did not fix: in the two Kerber networks
  each house's service cable is one of two types, at even odds. The demo fixes that draw since
  (`importer/templates.seeded`), so `prepare` now builds the same networks every time, and they are
  not the draw behind these tables: a repeat runs on service cables that differ house by house.
  The Dickert network has no such draw.

## How to repeat it

```bash
PY=./.venv/Scripts/python.exe                              # ./.venv/bin/python on Linux and macOS
$PY tools/envelope_networks.py prepare <folder>            # the three networks and their profiles
$PY tools/envelope_networks.py all <folder> --workers 4    # the six cases, one after another
$PY tools/envelope_networks.py table <folder>              # the table below, from the cases' files
```

`<folder>` is outside the repository. A case takes its stages up where an earlier run left them.

## Results

Computed on 12 November 2026 from commit `289c8cc`, the one that added the script, with pandapower
3.5.6: six cases, each over 20 adoption orders and the 11 weather years. The summary the
tables are made from is [`envelopes-by-network.json`](envelopes-by-network.json). The run was cut
once, when the machine's session ended, and taken up again: a case's stages are separate files, so
nothing computed before the cut was computed twice or changed.

Capacity in kW AC with the 3 % budget held per installation (the rule's basis); the last three columns are against the hourly study.

| Network | MV (pu) | Houses | Limit that binds (orders) | Static | Hourly study | Day before | Re-issued, largest change | Re-issued, forecast | Perfect foresight | Best issuable | Ceiling | Rule |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Dickert, middle feeders | 1.00 | 60 | transformer 20 | 256.4 | 311.5 | 280.9 | 281.7 | 291.7 | 366.2 | -6.3 % | +17.6 % | does not pass |
| Dickert, middle feeders | 1.06 | 60 | voltage 20 | 156.3 | 199.0 | 179.8 | 179.8 | 186.3 | 266.4 | -6.4 % | +33.9 % | does not pass |
| Kerber Dorfnetz | 1.00 | 58 | cable 20 | 414.9 | 479.0 | 447.3 | 451.7 | 463.6 | 493.4 | -3.2 % | +3.0 % | does not pass (no room) |
| Kerber Dorfnetz | 1.06 | 58 | voltage 20 | 158.1 | 196.2 | 176.8 | 177.0 | 187.3 | 247.3 | -4.5 % | +26.0 % | does not pass |
| Kerber Landnetz, cable | 1.00 | 27 | transformer 20 | 102.7 | 122.8 | 112.9 | 113.6 | 115.5 | 147.5 | -5.9 % | +20.1 % | does not pass |
| Kerber Landnetz, cable | 1.06 | 27 | voltage 20 | 62.6 | 77.5 | 72.9 | 73.4 | 74.1 | 100.0 | -4.4 % | +29.1 % | does not pass |

The same with the budget held for the installations together (aggregate), the hours above a limit in the replay of every issuable basis (both budgets), and the product's own scorecard.

| Network | MV (pu) | Day before | Re-issued, largest change | Re-issued, forecast | Perfect foresight | Hours above a limit | Scorecard, largest change | Scorecard, forecast |
|---|---|---|---|---|---|---|---|---|
| Dickert, middle feeders | 1.00 | 282.9 | 285.5 | 295.7 | 383.1 | 0 | fail / fail | fail / fail |
| Dickert, middle feeders | 1.06 | 184.9 | 187.7 | 197.9 | 277.7 | 0 | fail / fail | fail / fail |
| Kerber Dorfnetz | 1.00 | 452.9 | 458.0 | 475.0 | 493.4 | 0 | fail / fail | fail / fail |
| Kerber Dorfnetz | 1.06 | 182.9 | 187.3 | 193.3 | 263.1 | 0 | fail / fail | fail / fail |
| Kerber Landnetz, cable | 1.00 | 113.1 | 113.6 | 116.2 | 152.7 | 0 | fail / fail | fail / fail |
| Kerber Landnetz, cable | 1.06 | 73.4 | 73.6 | 74.5 | 103.9 | 0 | fail / fail | fail / fail |

### The rule, applied

**No case passes.** In all six, what can be issued connects less solar than the hourly study
without control, not 10 % more:

- With the budget held per installation, the best issuable basis is in every case the re-issue
  during the day with the quantile forecast, and it gives between 3.2 % and 6.4 % less than
  the hourly study. The envelopes issued the day before give between 6.0 % and 9.9 % less.
- Holding the budget for the installations together does not change the sign: the best issuable
  basis is still below the hourly study in all six cases, by 0.6 % to 5.4 %.
- Every issuable basis has 0 hours above a limit in the replay of every year. The envelopes are
  safe; they are smaller than what the hourly study connects without them.
- The product's own scorecard fails in every case, in both variants and on both budgets.

### Where the voltage limits

The question was whether envelopes win where the voltage, and not the transformer, is what limits.
At 1.06 pu the voltage binds in all 20 adoption orders of all three networks, and there the room
exists: with perfect foresight the envelopes would connect 26.0 %, 29.1 % and 33.9 % more than the
hourly study. What can be issued does not reach it: it stays 4.4 % to 6.4 % below. At 1.00 pu
the transformer binds in two networks (ceilings of 17.6 % and 20.1 %) and a cable in the third,
where even perfect foresight adds only 3.0 %: that case had no room to pass.

So the kind of limit is not what decides. In every case the distance between the ceiling and what
can be issued is the forecast. Our reading: an envelope issued in advance has to hold for the
sunniest and least loaded hour the planner cannot rule out, and that margin costs more capacity
than the control gives back.

### What follows

By the rule above, written before the cases were computed, **the envelopes leave the product's
pitch, and the code stays where it is.** What changes in the demo's screens and texts is a separate
decision, and it is not made in this document.

The tables also show what does hold in every case: the hourly study itself. Without any control it
connects between 15.4 % and 27.3 % more than the static study. That figure needs no
forecast, no agreement and no inverter that obeys a limit.

What the result does not settle: whether a better forecast closes the gap (the ceiling says what
there is to gain, 17.6 % to 33.9 % in the five cases with room), and what a DSO's own networks
and measured MV voltage would give. Both need data this repository does not have.
