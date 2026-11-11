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

## How to repeat it

```bash
PY=./.venv/Scripts/python.exe                              # ./.venv/bin/python on Linux and macOS
$PY tools/envelope_networks.py prepare <folder>            # the three networks and their profiles
$PY tools/envelope_networks.py all <folder> --workers 4    # the six cases, one after another
$PY tools/envelope_networks.py table <folder>              # the table below, from the cases' files
```

`<folder>` is outside the repository. A case takes its stages up where an earlier run left them.

## Results

Not computed yet.
