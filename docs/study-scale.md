# What a study of many stations costs

A DSO has hundreds of secondary substations. A study builds each one in its own process, so the
cost of a study is the cost of a station times how many there are, divided by how many processes
the machine runs side by side. This page says how a study uses the machine, how to measure it and
what the measurement answers. Its figures are in one dated section at the end, which names the
machine and the commit they come from.

## How a study uses the machine

- **How many at once.** `study build` runs as many station processes as the free memory holds:
  the smaller of the memory available and the commit left, less what it keeps for the rest of the
  machine, divided by what one station process takes, and never more than the cores
  (`src/kapacitet/machine.py`). What a station takes is the largest peak a station of the same
  study recorded in the same mode, with a margin, or a default before any has: each station writes
  the most memory its process took in its record (`peak_memory_mb`). `--jobs` decides instead when
  it is given. The build prints the number and why, and `study cases`, `options`, `devplan` and
  `scenarios` follow the same rule.
- **One process per station.** Reusing a process for the next station would save its imports, at
  the risk of one station's caches or paths reaching the next. The imports are a few seconds; a
  station's results are worth more.
- **Sleep.** While a study builds, Windows is asked not to suspend. If the machine is suspended all
  the same (a lid closed, a battery that ran out), the build says when, and every station that was
  building then is marked with how long the machine was away (`suspended_s`): its seconds include it.
- **Disk.** The household profile pool is linked from the reference network, not copied: every
  station's folder counts it, and the disk keeps it once. The weather years are copied on purpose: a
  study that fetches weather of its own must never write through a link into the reference
  network's files.
- **The weakest point.** The five customers with the least linear headroom are searched with the
  nonlinear flow on their tightest hours; the lowest is confirmed on every hour, and each of the
  others is solved on every hour at that figure, so a weaker one is found and confirmed in turn.
  Confirming each of the five at its own figure gives the same customer and figure, with more
  rounds of searching again (`worst_point(confirm="all")`, kept to compare).

## The measurement

`tools/study_scale.py` makes a synthetic study of any size and puts a clock around the study's own
commands. Its stations cycle through thirteen of pandapower's LV benchmark networks (the Kerber
networks and three of Dickert's, 12 to 192 customers), each with its own fuses, roof areas and
place from a seed of its own. They are written through the export contract, as a DSO's folders
would be, and every station says it is synthetic.

```bash
PY=./.venv/Scripts/python.exe          # ./.venv/bin/python on Linux and macOS
STUDY=~/kapacitet-studies/scale-50     # outside the repository, and a short path on Windows

$PY tools/study_scale.py create $STUDY --stations 50
$PY tools/study_scale.py run $STUDY                      # quick mode: build, collect, publish
$PY tools/study_scale.py measure $STUDY --json scale-50-quick.json

$PY -m kapacitet study build $STUDY --force              # the same stations in full mode
$PY tools/study_scale.py measure $STUDY --json scale-50-full.json
```

`run` writes `scale.json` in the study's folder: the wall clock and the exit code of each command,
the mode, the number of processes, the machine, and the least free memory and commit it saw while
the build ran. `measure` reads each station's own record of its build, which
holds the seconds of every step and the most memory its process took, and the size of its folder,
and prints one row per station with the totals; hard links are counted apart, since the disk keeps
them once.

Run it on a machine that is doing nothing else, and say which machine. A figure taken while
another computation shares the cores is not a measurement, and this page should not carry one.

The weekly data suite (`.github/workflows/data-suite.yml`) builds five stations this way and writes
their table to the run's summary, so a slower build shows from one week to the next.

## What the result should be read for

- **Seconds per station in each mode,** against customers and buses: is a quarter's run of a few
  hundred stations a night, a weekend or a week on one machine?
- **Memory per station process:** the number of processes is what turns station time into wall
  clock, and what the build reserves for each is what it measured.
- **Disk per station,** and whether the profiles of every year need to be kept between runs.
- **The stations that fail,** and why: a benchmark network is cleaner than a DSO's export.

## Measured on 15 November 2026

On a laptop with an Intel processor of 12 cores and 16 GB of memory, Windows 11, Python 3.12, at
commit 3d66874 with the changes of task 2.7 of the technical review of 14 November, and with no
other computation running (a browser and an editor open: 5 to 6 GB of memory free before each
build).

- **Twenty stations in quick mode, with the defaults** (`tools/study_scale.py all <dir> --stations
  20`): the build chose 2 station processes at a time from 5.4 GB free, and took 891.5 s of wall
  clock; `collect` and `publish` took 2.0 and 2.1 s. The twenty stations have 1,572 customers, and
  their builds add up to 1,689 s, from 13.0 s (12 customers) to 272.3 s (191 customers). While it
  built, the least memory free was 4.0 GB and the least commit free 4.6 GB. A station process took
  at most 1,715 MB (191 customers); one of 12 customers, 1,097 MB. The stations' folders add up to
  3,013 MB, of which 786 MB are hard links to the reference household pool, kept on disk once.
- **One station of 26 customers in full mode** (eleven weather years and 21 adoption orders,
  `s001-kerber-land-ol`): 96 s of build before task 2.7 and 101 s after, with the same results; its
  process took at most 1,196 MB. The technical review measured 318 s for this station on the same
  laptop; that could not be reproduced on an idle machine.
- **Before a station computes anything:** importing the modules takes about 3 s
  (`python -X importtime`), and reading the eleven years of prices under 1 s. The review's 25 s and
  10 s for them were measured under cProfile, which slows imports and many small calls.
- **The weakest point:** on the thirteen networks in quick mode, the way a station now searches it
  and confirming each candidate at its own figure give the same customer and figure in all 39 cases
  (generation at both ends of the MV band, consumption at the low end), in 231 s against 532 s. The
  gain is where the transformer binds at the low end of the band (from 107 s to 24 s on
  `s010-kerber-vorstadt-trafo-2`); a small network with nothing to search again takes as long as
  before.
