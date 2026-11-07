"""Property tests of the issued schedule (engine/delivery.py, engine/csipaus.py): no interval of a
schedule, hourly or quarter-hourly, ever carries a limit above its hour's verified envelope.

Hypothesis draws the year (a leap year and not), the day (the days the clocks change included),
the envelopes around that day (any value, zero, and values a hair either side of a rounding
boundary) and the export the house would make, hourly, per quarter-hour or none. Every row's limit
must be at most the envelope of the hour it falls in, compared exactly, with no tolerance: the
envelope is what the power flow verified, and a limit rounded up past it was never verified. The
same holds for the watts of the CSIP-AUS XML the inverter would receive.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import numpy as np
import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings
from hypothesis import strategies as st

from conftest import examples

CSIP = "{https://csipaus.org/ns/v1.2}"
NS = "{urn:ieee:std:2030.5:ns}"

envelope = st.one_of(
    st.floats(0.0, 60.0),
    st.just(0.0),
    st.floats(0.0, 0.002),
    # a hair either side of a watt boundary: where rounding to three decimals moves the figure most
    st.tuples(st.integers(0, 60_000), st.sampled_from((0.0004999, 0.0005, 0.0005001, 0.000999))).map(
        lambda t: t[0] / 1000.0 + t[1]),
)


@st.composite
def days(draw):
    from kapacitet.timeindex import year_index

    year = draw(st.sampled_from((2024, 2025)))
    H = len(year_index(year))
    # bias towards the days the clocks change (last Sundays of March and October) and the ends
    special = {2024: (0, 89, 90, 300, 301, 365), 2025: (0, 87, 88, 297, 298, 364)}[year]
    day = draw(st.one_of(st.sampled_from(special), st.integers(0, H // 24 - 1)))
    base = draw(envelope)
    limits = np.full(H, base)
    lo, hi = max(0, day * 24 - 2), min(H, day * 24 + 27)
    limits[lo:hi] = draw(st.lists(envelope, min_size=hi - lo, max_size=hi - lo))
    kind = draw(st.sampled_from(("none", "hourly", "quarterly")))
    desired = None if kind == "none" else np.full(H * (4 if kind == "quarterly" else 1), draw(st.floats(0.0, 30.0)))
    return year, day, limits, desired


@settings(max_examples=examples(60))
@given(case=days(), resolution=st.sampled_from((15, 60)))
def test_no_interval_of_a_schedule_carries_a_limit_above_its_hours_envelope(case, resolution):
    from kapacitet.engine.csipaus import der_control_list
    from kapacitet.engine.delivery import schedule, within_envelope

    year, day, limits, desired = case
    rows = schedule(limits, desired, year, day, resolution)
    if resolution == 60:
        assert [r["hour"] for r in rows] == list(range(day * 24, day * 24 + 24))
    else:
        assert len(rows) in (92, 96, 100), "a delivery day has 23, 24 or 25 hours"
        assert all(r["hour"] == r["quarter"] // 4 for r in rows)
        assert all(b["start_epoch"] - a["start_epoch"] == 900 for a, b in zip(rows, rows[1:]))
    above = [(r["hour"], r["limit_kw"], float(limits[r["hour"]])) for r in rows
             if not r["limit_kw"] <= limits[r["hour"]]]
    assert not above, f"intervals above their hour's verified envelope: {above[:3]}"
    assert within_envelope(rows, limits)["ok"]

    xml = ET.fromstring(der_control_list(1, rows, issued_epoch=0))
    for r, ev in zip(rows, xml.iter(f"{NS}DERControl")):
        lim = ev.find(f"{NS}DERControlBase/{CSIP}opModExpLimW")
        watts = int(lim.find(f"{NS}value").text) * 10 ** int(lim.find(f"{NS}multiplier").text)
        assert watts <= 1000.0 * limits[r["hour"]], "the inverter would receive more than the verified envelope"
