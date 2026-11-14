import { describe, expect, it } from "vitest";
import { bindingLimit, curveMarks, hourlyVerdict, staticNoteKey, uncontrolledPasses, type Verification } from "./verdicts";

// The reference network: without control the envelope deployment overloads the transformer 148 h
// while the voltage stays below its limit (results/showcase.json, 6 Nov 2026).
const REFERENCE: Verification = {
  uncontrolled_violation_hours: 148, uncontrolled_overvoltage_hours: 0, uncontrolled_trafo_overload_hours: 148,
  uncontrolled_line_overload_hours: 0, uncontrolled_max_voltage_pu: 1.048, uncontrolled_max_trafo_loading_pct: 126.2,
};
// Twelve houses on short feeders: every house fits, the transformer peaks at 18.9 % (the review's case)
const ALL_FIT: Verification = {
  uncontrolled_violation_hours: 0, uncontrolled_overvoltage_hours: 0, uncontrolled_trafo_overload_hours: 0,
  uncontrolled_line_overload_hours: 0, uncontrolled_max_voltage_pu: 1.031, uncontrolled_max_trafo_loading_pct: 18.9,
};

describe("the verification of the same houses without control", () => {
  it("passes with 0 h and fails with any hour above a limit", () => {
    expect(uncontrolledPasses(ALL_FIT)).toBe(true);
    expect(uncontrolledPasses(REFERENCE)).toBe(false);
  });

  it("names the limit broken in the most hours, and none when none is", () => {
    expect(bindingLimit(REFERENCE)).toEqual({ key: "cmp.binding.trafo", vars: { h: 148, v: 1.048 } });
    expect(bindingLimit(ALL_FIT)).toEqual({ key: "cmp.binding.none", vars: { v: 1.031, load: 18.9 } });
    expect(bindingLimit({ ...REFERENCE, uncontrolled_overvoltage_hours: 300, uncontrolled_max_voltage_pu: 1.12 }).key).toBe("cmp.binding.voltage");
    expect(bindingLimit({ ...REFERENCE, uncontrolled_trafo_overload_hours: 2, uncontrolled_line_overload_hours: 40 }).key).toBe("cmp.binding.line");
  });

  it("says less for results that do not break the hours down by limit", () => {
    expect(bindingLimit({ uncontrolled_violation_hours: 7, uncontrolled_max_voltage_pu: 1.05 }))
      .toEqual({ key: "cmp.binding.some", vars: { h: 7, v: 1.05 } });
    expect(bindingLimit({ uncontrolled_violation_hours: 0, uncontrolled_max_voltage_pu: 1.01 }).key).toBe("cmp.binding.noneV");
  });
});

describe("the note on a request's static baseline", () => {
  it("calls the grid full only when the static study leaves a house out", () => {
    expect(staticNoteKey(50, 120)).toBe("req.base.staticNote");
    expect(staticNoteKey(12, 12)).toBe("req.base.staticNoteAll");
    expect(staticNoteKey(12, null)).toBe("req.base.staticNote");
  });
});

describe("the marks of the curtailment curve", () => {
  it("gives equal medians one label", () => {
    const one = curveMarks([{ x: 94, name: "Static" }, { x: 94, name: "Hourly" }, { x: 94, name: "Envelopes" }], 10);
    expect(one).toEqual([{ x: 94, name: "Static = Hourly = Envelopes", position: "insideEndTop" }]);
  });

  it("sends a close mark to the other end of its line, and leaves distant ones at the top", () => {
    const marks = curveMarks([{ x: 609, name: "Hourly" }, { x: 413, name: "Static" }, { x: 620, name: "Envelopes" }], 500);
    expect(marks.map((m) => [m.name, m.position])).toEqual([
      ["Static", "insideEndTop"], ["Hourly", "insideEndTop"], ["Envelopes", "insideStartTop"],
    ]);
  });
});

describe("the hourly study's verdict on a request", () => {
  it("connects firm only when no hour of any weather year is held back, for any device", () => {
    const quiet = [{ year: 2015, hours_limited: 0 }, { year: 2016, hours_limited: 0, ev_hours_limited: 0 }];
    expect(hourlyVerdict(quiet)).toEqual({ fits: true, hours: 0, years: 2, worst: null });
    const charger = [{ year: 2015, hours_limited: 0, ev_hours_limited: 3 }, { year: 2016, hours_limited: 0 }];
    expect(hourlyVerdict(charger)).toEqual({ fits: false, hours: 3, years: 2, worst: { year: 2015, hours: 3 } });
  });

  it("sums the devices and the years, and names the worst year", () => {
    const rows = [
      { year: 2015, hours_limited: 10, hp_hours_limited: 2 },
      { year: 2016, hours_limited: 30 },
      { year: 2017, hours_limited: 0 },
    ];
    expect(hourlyVerdict(rows)).toEqual({ fits: false, hours: 42, years: 3, worst: { year: 2016, hours: 30 } });
  });

  it("never connects an answer without years", () => {
    expect(hourlyVerdict(undefined).fits).toBe(false);
    expect(hourlyVerdict([]).fits).toBe(false);
  });
});
