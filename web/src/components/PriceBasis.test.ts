import { describe, expect, it } from "vitest";
import { rangeParams, yearSpan } from "./PriceBasis";

describe("yearSpan", () => {
  it("writes runs of years", () => {
    expect(yearSpan([2023, 2024, 2025])).toBe("2023–2025");
    expect(yearSpan([2025, 2015, 2016, 2017, 2019])).toBe("2015–2017, 2019, 2025");
    expect(yearSpan([2024])).toBe("2024");
    expect(yearSpan([])).toBe("–");
  });
});

describe("rangeParams", () => {
  it("formats min, max and p10-p90, or gives null without a range", () => {
    const s = { central: 5, range: { min: 1, max: 9, p10: 2, p90: 8 } };
    expect(rangeParams(s, (v) => `${v}!`)).toEqual({ min: "1!", max: "9!", p10: "2!", p90: "8!" });
    expect(rangeParams(undefined, String)).toBeNull();
  });
});
