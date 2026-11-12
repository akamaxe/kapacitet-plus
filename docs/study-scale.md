# What a study of many stations costs

A DSO has hundreds of secondary substations. A study builds each one in its own process, so the
cost of a study is the cost of a station times how many there are, divided by how many processes
the machine runs side by side. **That cost has not been measured beyond three stations.** This
page says how to measure it and what the measurement has to answer; it holds no figure of its own
until the measurement is run.

## What is known

- The study demo has three stations (27, 58 and 60 customers). Its build in quick mode, with two
  station processes at a time, has been run several times on one laptop, on a machine that was
  doing other work each time.
- Quick mode is one weather year and five adoption orders. The full mode is every weather year
  (eleven for the reference site) and 21 orders. The profiles of every year are computed in both.
- The time of a station does not follow its customer count: in those runs the station of 27
  customers took longer than the one of 60.
- Nothing in the tests or in CI measures the time or the disk of a study.

## What is not known

1. How long a station takes in full mode, and how that grows with its customers and its buses.
2. How many station processes a machine runs side by side before memory, not cores, is the limit.
3. What `collect`, `review` and `publish` cost with hundreds of rows. They read one small file per
   station, so they should be minutes; nobody has timed them.
4. How much disk a station keeps, and how much of it the next run needs.

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
$PY tools/study_scale.py run $STUDY --jobs 6             # quick mode: build, collect, publish
$PY tools/study_scale.py measure $STUDY --json scale-50-quick.json

$PY -m kapacitet study build $STUDY --force --jobs 6     # the same stations in full mode
$PY tools/study_scale.py measure $STUDY --json scale-50-full.json
```

`run` writes `scale.json` in the study's folder: the wall clock and the exit code of each command,
the mode, the number of processes and the machine. `measure` reads each station's own record of
its build, which holds the seconds of every step, and the size of its folder, and prints one row
per station with the totals.

Run it on a machine that is doing nothing else, and say which machine. A figure taken while
another computation shares the cores is not a measurement, and this page should not carry one.

## What the result should be read for

- **Seconds per station in each mode,** against customers and buses: is a quarter's run of a few
  hundred stations a night, a weekend or a week on one machine?
- **Memory per station process:** the number of processes is what turns station time into wall
  clock. The reference network's own computation runs six processes in 16 GB.
- **Disk per station,** and whether the profiles of every year need to be kept between runs.
- **The stations that fail,** and why: a benchmark network is cleaner than a DSO's export.

## Status

On 12 November 2026 the script was run with three stations in quick mode, to see that it works
end to end (create, build, collect, publish, measure). The machine was running another computation
at the time, so its timings are not reported here. The run of 50 stations has not been made.
