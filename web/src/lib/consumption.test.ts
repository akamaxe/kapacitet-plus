import { describe, expect, it } from "vitest";
import { consumptionFigures, type ConsumptionDoc } from "./consumption";

// The reference network's consumption capacity (results/consumption.json): 24.5 new chargers of
// 11 kW by the hourly study against 3 by the static convention, a constant 11 kW in every hour.
const spread = (median: number, p10: number, p90: number, min: number, max: number) => ({ median, p10, p90, min, max });
const REFERENCE: ConsumptionDoc = {
  years: [2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025],
  n_orders: 20,
  customers: 120,
  baseline_violation_hours: { 2015: 0, 2025: 0 },
  baseline_violates: false,
  devices: {
    ev: { device_kw: 11, existing: 18, candidates: 102, adopters: spread(24.5, 19.8, 30.3, 18, 34) },
    hp: { device_kw: null, existing: 36, candidates: 84, adopters: spread(8, 7, 9, 6, 10) },
    flat: { device_kw: 11, existing: 0, candidates: 120, adopters: spread(3, 3, 3, 3, 3) },
  },
  nodal_kw: { timeseries: spread(38.48, 37.85, 39.7, 37.21, 40.27), static: spread(38.48, 37.85, 39.7, 37.21, 40.27) },
};

describe("the consumption section's figures", () => {
  it("are the document's own, with the ratio of the two conventions", () => {
    const k = consumptionFigures(REFERENCE);
    expect(k.ev.adopters.median).toBe(24.5);
    expect(k.flat.adopters.median).toBe(3);
    expect(k.ratio).toBeCloseTo(24.5 / 3, 9);
    expect(k.span).toBe("2015–2025");
    expect(k.baselineHours).toBe(0);
  });

  it("say no ratio when the static convention takes nothing, and count a grid already broken", () => {
    const none = { ...REFERENCE.devices.flat, adopters: spread(0, 0, 0, 0, 0) };
    const broken = consumptionFigures({
      ...REFERENCE, years: [2025], baseline_violates: true, baseline_violation_hours: { 2025: 7 },
      devices: { ...REFERENCE.devices, flat: none },
    });
    expect(broken.ratio).toBeNull();
    expect(broken.baselineHours).toBe(7);
    expect(broken.span).toBe("2025");
  });
});
