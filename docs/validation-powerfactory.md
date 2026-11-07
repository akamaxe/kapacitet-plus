# Cross-validation against PowerFactory

Every figure Kapacitet+ publishes rests on one power flow: the batched Z-bus fixed point in
`engine/powerflow.py`, reached through `NetModel.solve`. The validation stage already compares it
with pandapower's Newton-Raphson. Both of those use the same pandapower network description, so
they can share the same modelling error. This check rebuilds the network in DIgSILENT
PowerFactory, a commercial tool written independently, from the network data alone, and solves
the same hours there.

**Result (2026-11-07):** on the ten hours below, PowerFactory and Kapacitet+ differ by at most
**1.9e-7 pu** in bus voltage and **4.5e-4 percentage points** in transformer loading. The targets
were 0.002 pu and 1 percentage point. Both are met by more than three orders of magnitude.

## Method

`tools/pf_crossval.py` does the work in three steps. The first and last steps run in the
project's environment. The middle step runs inside PowerFactory's Python API, which has neither
pandapower nor Kapacitet+.

1. **`export`.** Writes the reference network as plain data, plus the hours, their injections and
   Kapacitet+'s answer for them. That answer comes from `NetModel.solve` as published:
   `get_model()` with the MV voltage and transformer tap from `config/assumptions.toml`.
   pandapower's Newton-Raphson answer goes in the same file, as a second reference.
2. **`run_in_powerfactory`.** Run through the `powerfactory` MCP server, in parallel mode
   (`/inMemory`), so the owner's PowerFactory database is never changed. It builds the network in
   a new project, applies each hour's injections and runs PowerFactory's balanced load flow. Then
   it reads the bus voltages (`m:u`), the transformer loading (`c:loading`) and the line loading
   (`c:loading`). The project exists only in that session's memory. No PowerFactory project file
   is written or committed.
3. **`compare`.** Computes the maximum and mean absolute differences over every bus, line and
   hour.

To reproduce, with `KAPACITET_DATA_DIR` set as for the rest of the project:

```bash
python tools/pf_crossval.py export          # -> data/processed/validation/powerfactory/case.json
# in PowerFactory (MCP run_python, parallel mode):
#   ns = runpy.run_path(r"<repo>\tools\pf_crossval.py", run_name="pf_crossval")
#   ns["run_in_powerfactory"](app, r"<dir>\case.json", r"<dir>\powerfactory.json")
python tools/pf_crossval.py compare         # -> comparison.json and the tables below
```

### The network, element by element

The network has 165 buses, 163 cables, one transformer and 120 customers. The geometry is the
real OpenStreetMap neighbourhood, but the cables, cabinets and transformer are synthetic (see the
README's data table). Each pandapower element maps to a PowerFactory element with the same data:

| pandapower | PowerFactory | Data carried over |
|---|---|---|
| `bus` | `ElmTerm` (busbar) | nominal voltage, 10 kV or 0.4 kV |
| `ext_grid` | `ElmXnet`, slack | voltage setpoint 1.00 pu (`mv_voltage_pu`), angle 0 |
| `line` (NAYY 4x150 SE, 4x50 SE) | `ElmLne` with `TypLne` | R', X', C' per km (C' = 261 nF/km), G' = 0, length, rated current, parallel count, derating |
| `trafo` (0.4 MVA 10/0.4 kV) | `ElmTr2` with `TypTr2` | 0.4 MVA, 10/0.4 kV, uk 4 %, copper losses 5.3 kW (uk_r 1.325 %), iron losses 0.95 kW, no-load current 0.2375 %, Dyn5, tap at the HV side with 2.5 % per step, positions -2 to +2, at position 0 |
| `load`, one per customer | `ElmLod` | P and Q of the hour, constant power |
| `sgen`, one per customer | `ElmGenstat` | P of the hour, Q = 0 |

The load flow settings were AC balanced (positive sequence) and constant-power loads (no voltage
dependence). Automatic tap changing and reactive limits were off. Cable resistance was taken at
20 °C, which is the type data. The node tolerance was 1e-5 kVA at every voltage level; the next
section explains why it was not left at the default.

### The hours

The comparison needs operating points that span the whole range, so it uses the most demanding
state the model can produce. That is reference year 2025, with every customer given solar PV, a
heat pump and an EV, and no curtailment. Envelopes would forbid most of this state: the peak
hours overload the transformer and the peak import hour falls below the voltage limit. It is not
a published state. It is a test of the solver well past the limits it guards. Load and PV come
from the synthetic profiles. The hours, in UTC at the start of the interval, are:

- the peak export hour, the largest net export of the year;
- the peak import hour, the largest net import of the year;
- eight more, drawn from the remaining hours with seed 7.

| Hour (UTC, interval start) | Why | Net export (kW) | V min / max, Kapacitet+ (pu) | V min / max, PowerFactory (pu) | max abs dV (pu) | Trafo loading, Kapacitet+ / PowerFactory (%) |
|---|---|---:|---|---|---:|---|
| 2025-05-19 10:00 | peak export | 663.0 | 1.0000 / 1.0606 | 1.0000 / 1.0606 | 1.67e-07 | 159.39 / 159.39 |
| 2025-01-03 17:00 | peak import | -724.9 | 0.8903 / 1.0000 | 0.8903 / 1.0000 | 1.48e-07 | 202.83 / 202.83 |
| 2025-03-24 04:00 | drawn (seed 7) | -182.7 | 0.9757 / 1.0000 | 0.9757 / 1.0000 | 1.88e-07 | 48.25 / 48.25 |
| 2025-07-30 23:00 | drawn (seed 7) | -25.7 | 0.9966 / 1.0000 | 0.9966 / 1.0000 | 1.85e-07 | 6.88 / 6.88 |
| 2025-08-16 23:00 | drawn (seed 7) | -41.4 | 0.9937 / 1.0000 | 0.9937 / 1.0000 | 1.85e-07 | 10.94 / 10.94 |
| 2025-09-07 13:00 | drawn (seed 7) | 33.0 | 0.9890 / 1.0000 | 0.9890 / 1.0000 | 1.82e-07 | 17.96 / 17.96 |
| 2025-10-11 00:00 | drawn (seed 7) | -43.2 | 0.9945 / 1.0000 | 0.9945 / 1.0000 | 1.84e-07 | 11.41 / 11.41 |
| 2025-11-01 05:00 | drawn (seed 7) | -123.2 | 0.9843 / 1.0000 | 0.9843 / 1.0000 | 1.87e-07 | 32.37 / 32.37 |
| 2025-11-24 07:00 | drawn (seed 7) | -253.6 | 0.9656 / 1.0000 | 0.9656 / 1.0000 | 1.91e-07 | 67.53 / 67.53 |
| 2025-12-11 13:00 | drawn (seed 7) | -160.2 | 0.9801 / 1.0000 | 0.9801 / 1.0000 | 1.89e-07 | 42.16 / 42.16 |

In each hour, the maximum or minimum of 1.0000 pu is the MV bus, which is held at the slack voltage.

## Results

Absolute differences over 10 hours × 165 buses (voltages), 10 hours (transformer) and
10 hours × 163 cables (lines):

| Quantity | PowerFactory against | max abs diff | mean abs diff | Target |
|---|---|---:|---:|---:|
| Bus voltage (pu) | Kapacitet+ (Z-bus) | 1.91e-07 | 1.27e-07 | 0.002 |
| Transformer loading (percentage points) | Kapacitet+ (Z-bus) | 4.51e-04 | 4.18e-04 | 1.0 |
| Line loading (percentage points) | Kapacitet+ (Z-bus) | 2.14e-04 | 2.19e-05 | — |
| Bus voltage (pu) | pandapower NR | 1.91e-07 | 1.27e-07 | 0.002 |
| Transformer loading (percentage points) | pandapower NR | 4.51e-04 | 4.18e-04 | 1.0 |
| Line loading (percentage points) | pandapower NR | 2.14e-04 | 2.19e-05 | — |

Kapacitet+ and pandapower's Newton-Raphson agree to the eighth decimal that `case.json` stores,
so the two rows are the same. Every one of PowerFactory's ten load flows converged.

### Reading

- **Same model, same answer.** The worst bus is 1.9e-7 pu away. That is about 0.08 mV on a
  400 V bus, four orders of magnitude below the 0.002 pu target. It is also far below the
  `v_margin_pu` safety margin the envelopes keep from the voltage limits. The agreement holds at
  both extremes: 1.06 pu at the peak export hour and 0.89 pu at the peak import hour, with the
  transformer at 159 % and 203 %.
- **What is left is a constant offset, not a solver error.** PowerFactory sees a transformer
  inflow 1.8 W higher than Kapacitet+ in every hour, at 27 kW and at 771 kW alike. That is 0.2 %
  of the 950 W iron losses. The difference does not grow with the flow, so it comes from how the
  two tools turn the no-load data (iron losses and no-load current) into a magnetising
  admittance, not from the load flow. It explains both residuals: about 1.8e-7 pu at the LV
  busbar, carried down every feeder, and 4e-4 percentage points of transformer loading.
- **PowerFactory's default tolerance would have hidden this.** On a first run with PowerFactory's
  default node tolerances (1 kVA on HV and MV buses, 0.01 kVA on LV buses), the differences were
  6.4e-5 pu and 0.045 percentage points (mean 9.1e-6 pu and 0.010 percentage points). They came
  and went from hour to hour: three hours showed 1.6e-5 to 6.4e-5 pu, while the others sat at
  the 1e-7 offset. PowerFactory reported convergence after two Newton steps, but one of those
  hours drew 0.13 kW less from the transformer than its own load and losses add up to. Tightening
  the tolerance to 1e-5 kVA at every level removed all of it, which shows those errors were
  PowerFactory stopping early. They were still inside both targets. `run_in_powerfactory(...,
  tol_kva=None)` repeats that run, and `compare --pf powerfactory-defaults.json` reads it.

### What this does not cover

- **Balanced, positive-sequence flow only.** The three-phase model (`powerflow3ph.py`) is used by
  the request's phase check, never by a published figure. PowerFactory's unbalanced load flow
  (`iopt_net = 1`) could check it the same way, but this run does not.
- **One network, one transformer tap, one MV voltage** (1.00 pu, the published value). The MV band
  sensitivity (`mv_band.json`) and the study networks run through the same `NetModel.solve`, so
  they are covered only to the extent that the solver is the same.
- **The model, not the network.** Both tools solve the same synthetic cables and transformer.
  This check shows that Kapacitet+ solves its network correctly. It cannot show that the network
  matches the real one in Smedslätten. Only the distribution company's data can show that.

## Versions

| Component | Version |
|---|---|
| Kapacitet+ | 0.1.0, branch head 5318c3c plus this script |
| pandapower | 3.5.6 (numpy 2.4.6), in `.venv` |
| DIgSILENT PowerFactory | 2024 SP2 (24.0.2), Python API on Python 3.12.15, parallel mode (`/inMemory`) |
| Network and hours | reference year 2025; data copy of 2026-11-07 |
