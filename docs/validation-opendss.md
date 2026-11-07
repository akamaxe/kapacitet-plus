# Cross-validation against OpenDSS

Every figure Kapacitet+ publishes rests on one power flow: the batched Z-bus fixed point in
`engine/powerflow.py`, reached through `NetModel.solve`. The validation stage compares it with
pandapower's Newton-Raphson, and [validation-powerfactory.md](validation-powerfactory.md) with
DIgSILENT PowerFactory. This check adds OpenDSS, EPRI's open-source distribution simulator. Like
PowerFactory, it was written independently of pandapower. Unlike PowerFactory, anyone can install
it, so the check also runs in CI on every push, on the tiny grid.

**Result (2026-11-07):** on the same ten hours as the PowerFactory check, OpenDSS and Kapacitet+
differ by at most **2.2e-5 pu** in bus voltage and **1.8e-3 percentage points** in transformer
loading, against targets of 0.002 pu and 1 percentage point. The whole difference comes from the
transformer's no-load branch, which the two tools place differently. With that branch removed from
both, they agree to **3.2e-8 pu** and **7.4e-5 percentage points**.

## Method

`tools/dss_crossval.py` reads the same `case.json` that `tools/pf_crossval.py export` writes:

- the reference network as plain data;
- the hours, with each customer's load, PV and reactive power;
- Kapacitet+'s answer for those hours, from `NetModel.solve` as published;
- pandapower's Newton-Raphson answer.

It builds the circuit in OpenDSS through `opendssdirect.py`, sets each hour's injections, solves a
snapshot power flow and reads the results:

- bus voltages, as the mean of the three phase magnitudes;
- transformer loading, as the larger of the HV and LV phase currents over their rated currents,
  the definition Kapacitet+ uses;
- cable loading, as the larger end current over the rated current × parallel count × derating.

| pandapower | OpenDSS | Data carried over |
|---|---|---|
| `ext_grid` | `Vsource`, 1e-6 Ω | the hour's MV voltage (1.00 pu, `mv_voltage_pu`), angle 0 |
| `bus` | bus with its kV base | 10 kV or 0.4 kV; the script checks every base against pandapower's |
| `line` | `Line`, `units=km` | R', X', C' per km, divided or multiplied by the parallel count; zero sequence set equal to positive |
| `trafo` | `Transformer`, two windings, delta/wye | rating, voltages, XHL = √(uk² − ukr²), ukr split equally between the windings, iron losses as `%noloadloss`, the rest of the no-load current as `%imag`, HV tap |
| `load`, one per customer | `Load`, model 1 | P and Q of the hour, constant power |
| `sgen`, one per customer | `Generator`, model 1 | P of the hour, Q = 0 |

Four settings make OpenDSS solve the same equations as pandapower:

1. **50 Hz.** OpenDSS defaults to 60 Hz, which would raise every cable's shunt susceptance by 20 %.
2. **Constant power across the whole range.** By default OpenDSS turns a load into a constant
   impedance below 0.95 pu and above 1.05 pu. The peak hours reach 0.89 and 1.06 pu, so both
   limits are widened to 0.5 and 1.5 pu, for loads and generators alike.
3. **A stiff source.** pandapower holds the MV bus at exactly the set voltage. The `Vsource` sits
   behind 1e-6 Ω, which moves it by about 1e-8 pu at the peak flows.
4. **A tight tolerance.** The voltage update must fall below 1e-10 pu (the default is 1e-4). It
   takes 8 to 12 iterations.

The flow is balanced: across all ten hours, the three phases of a bus differ by at most 1.1e-10 pu.

To reproduce, with the `validation` extra installed (`pip install -c constraints.txt -e
".[validation]"`) and `KAPACITET_DATA_DIR` set as for the rest of the project:

```bash
python tools/dss_crossval.py run    # exports case.json into data/processed/validation/opendss
                                    # if missing; -> opendss.json, comparison-opendss.json, tables
python tools/dss_crossval.py run --pf <dir>/powerfactory.json   # with PowerFactory side by side
```

For the three-way table below, PowerFactory solved the same `case.json` through the
`powerfactory` MCP server in parallel (in-memory) mode, with `run_in_powerfactory` from
`tools/pf_crossval.py`. Its results were identical to those in validation-powerfactory.md.

`tests/test_dss_crossval.py` (marked `needs_opendss`, skipped without the package) runs the same
check on the tiny grid: 30 buses, a 0.1 MVA transformer, 20 customers. The stated tolerances are
1e-4 pu and 0.01 percentage points as modelled, and 1e-7 pu and 1e-4 percentage points with the
no-load branch removed. The test also checks that the mean voltage difference is p0 × ukr / 2
within 30 % (see the reading below). The tiny grid currently shows 8.0e-5 pu (mean 6.1e-5,
against 6.3e-5 expected) and 2.2e-8 pu without the branch. On Windows, pytest's fault handler
prints `Windows fatal exception: code 0xe0465043` while the OpenDSS library loads. That is the
library raising and catching its own exception, and the test still passes.

## The hours

These are the same as in the PowerFactory check: reference year 2025, every customer with PV, a
heat pump and an EV, and no curtailment. The hours are the peak export hour, the peak import hour
and eight drawn with seed 7. This is a stress test well past the limits the envelopes guard, not a
published state.

| Hour (UTC, interval start) | Why | Net export (kW) | V min / max, Kapacitet+ (pu) | V min / max, OpenDSS (pu) | max abs dV (pu) | Trafo loading, Kapacitet+ / OpenDSS (%) |
|---|---|---:|---|---|---:|---|
| 2025-05-19 10:00 | peak export | 663.0 | 1.0000 / 1.0606 | 1.0000 / 1.0606 | 1.18e-05 | 159.39 / 159.39 |
| 2025-01-03 17:00 | peak import | -724.9 | 0.8903 / 1.0000 | 0.8903 / 1.0000 | 2.16e-05 | 202.83 / 202.83 |
| 2025-03-24 04:00 | drawn (seed 7) | -182.7 | 0.9757 / 1.0000 | 0.9756 / 1.0000 | 1.70e-05 | 48.25 / 48.25 |
| 2025-07-30 23:00 | drawn (seed 7) | -25.7 | 0.9966 / 1.0000 | 0.9965 / 1.0000 | 1.59e-05 | 6.88 / 6.88 |
| 2025-08-16 23:00 | drawn (seed 7) | -41.4 | 0.9937 / 1.0000 | 0.9937 / 1.0000 | 1.60e-05 | 10.94 / 10.94 |
| 2025-09-07 13:00 | drawn (seed 7) | 33.0 | 0.9890 / 1.0000 | 0.9890 / 1.0000 | 1.55e-05 | 17.96 / 17.96 |
| 2025-10-11 00:00 | drawn (seed 7) | -43.2 | 0.9945 / 1.0000 | 0.9944 / 1.0000 | 1.60e-05 | 11.41 / 11.41 |
| 2025-11-01 05:00 | drawn (seed 7) | -123.2 | 0.9843 / 1.0000 | 0.9843 / 1.0000 | 1.66e-05 | 32.37 / 32.37 |
| 2025-11-24 07:00 | drawn (seed 7) | -253.6 | 0.9656 / 1.0000 | 0.9656 / 1.0000 | 1.75e-05 | 67.53 / 67.53 |
| 2025-12-11 13:00 | drawn (seed 7) | -160.2 | 0.9801 / 1.0000 | 0.9801 / 1.0000 | 1.68e-05 | 42.16 / 42.16 |

Transformer inflow, OpenDSS minus Kapacitet+ (W), per hour: −2.6, −16.9, −4.3, −0.6, −0.9, −5.7,
−1.0, −2.8, −6.1, −3.7.

## Results

These are absolute differences over 10 hours × 165 buses (voltages), 10 hours (transformer) and
10 hours × 163 cables (lines).

| Quantity | OpenDSS against | max abs diff | mean abs diff | Target |
|---|---|---:|---:|---:|
| Bus voltage (pu) | Kapacitet+ (Z-bus) | 2.16e-05 | 1.62e-05 | 0.002 |
| Transformer loading (percentage points) | Kapacitet+ (Z-bus) | 1.77e-03 | 3.85e-04 | 1.0 |
| Line loading (percentage points) | Kapacitet+ (Z-bus) | 2.20e-03 | 6.34e-05 | — |
| Transformer inflow (kW) | Kapacitet+ (Z-bus) | 1.69e-02 | 4.47e-03 | — |
| Bus voltage (pu), no-load branch removed from both | pandapower NR | 3.17e-08 | 2.05e-08 | — |
| Transformer loading (percentage points), no-load branch removed | pandapower NR | 7.44e-05 | 2.60e-05 | — |
| Line loading (percentage points), no-load branch removed | pandapower NR | 3.23e-06 | 8.45e-08 | — |

pandapower's Newton-Raphson gives the same rows as Kapacitet+ to the eighth decimal, as in the
PowerFactory check. All ten OpenDSS solutions converged.

### Three solvers side by side

These are the maximum absolute differences over the same ten hours. All three tools solved every
hour.

| Quantity | OpenDSS vs Kapacitet+ | PowerFactory vs Kapacitet+ | OpenDSS vs PowerFactory |
|---|---:|---:|---:|
| Bus voltage (pu) | 2.16e-05 | 1.91e-07 | 2.15e-05 |
| Transformer loading (percentage points) | 1.77e-03 | 4.51e-04 | 2.22e-03 |
| Line loading (percentage points) | 2.20e-03 | 2.14e-04 | 2.05e-03 |
| Transformer inflow (kW) | 1.69e-02 | 1.80e-03 | 1.85e-02 |

### Reading

- **Three independent tools give the same answer, within 2.2e-5 pu.** That is under 0.01 V on a
  400 V bus, two orders of magnitude below the 0.002 pu target. The agreement holds at both
  extremes: 1.06 pu at peak export and 0.89 pu at peak import, with the transformer at 159 % and
  203 %.
- **OpenDSS is the one that differs, and the reason is the transformer's no-load branch.**
  PowerFactory and Kapacitet+ agree to 1.9e-7 pu, and OpenDSS sits about 2e-5 pu below both.
  Removing the iron losses and the no-load current from both OpenDSS and pandapower closes the
  gap to 3.2e-8 pu, so the rest of the model (cables, leakage impedance, tap, loads, the solver)
  matches. pandapower's T model puts the magnetising branch in the middle of the leakage
  impedance, and OpenDSS does not. The size of the offset fits that difference: it equals the
  iron-loss current, p0 = 0.95 kW / 400 kVA = 0.24 %, flowing through half the leakage resistance
  (ukr / 2 = 0.66 %). That gives 1.6e-5 pu, the mean difference measured here. The tiny grid,
  where p0 is 0.95 %, shows the same: 6.3e-5 pu expected, 6.1e-5 pu measured. The offset is a
  difference between the two transformer models, not a power-flow error.
- **The offset does not move any published figure.** It is about 450 times smaller than the
  0.01 pu `v_margin_pu` safety margin the envelopes keep from the voltage limits. The transformer-loading
  difference is at most 0.002 percentage points, against an overload criterion set in whole
  percent.

### What this does not cover

The limits listed in validation-powerfactory.md apply here as well:

- balanced, positive-sequence flow only, so the three-phase model used by the phase check is not
  tested;
- one network, one tap and one MV voltage;
- the model, not the real network.

The CI test covers the tiny grid, not the reference network, because CI has no data.

## Versions

| Component | Version |
|---|---|
| Kapacitet+ | 0.1.0, branch head 40ad865 plus this script |
| pandapower | 3.5.6 (numpy 2.4.6), in `.venv` |
| OpenDSS | opendssdirect.py 0.9.4 on DSS-Python 0.15.7 and DSS C-API 0.14.5 (based on OpenDSS SVN 3723), the `validation` extra; all BSD-licensed |
| DIgSILENT PowerFactory | 2024 SP2 (24.0.2), Python API on Python 3.12.15, parallel mode (`/inMemory`) |
| Network and hours | reference year 2025; data copy of 2026-11-07 |
