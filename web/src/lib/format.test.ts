import { describe, expect, it } from "vitest";
import { clock, dateTime, fmt, hourToDate, hoursInYear, median, monthStarts, pct1 } from "./format";

const THIN = " "; // the thousands separator every language shares (DESIGN.md)

describe("fmt", () => {
  it("groups thousands with a narrow space and keeps each language's decimal mark", () => {
    expect(fmt("en", 1234.5, 1)).toBe(`1${THIN}234.5`);
    expect(fmt("sv", 1234.5, 1)).toBe(`1${THIN}234,5`);
    expect(fmt("sv", 1234)).toBe(`1${THIN}234`);
  });

  it("writes Swedish with a decimal comma and a true minus sign", () => {
    expect(fmt("sv", 1234.5, 1)).toBe(`1${THIN}234,5`);
    expect(fmt("sv", -2.5, 1)).toBe("\u22122,5");
  });

  it("writes a dash for a value that is not a number", () => {
    expect(fmt("en", null)).toBe("–");
    expect(fmt("en", undefined)).toBe("–");
    expect(fmt("en", Number.NaN)).toBe("–");
    expect(fmt("en", Infinity)).toBe("–");
  });
});

describe("pct1", () => {
  const f = (v: number | null | undefined, d = 0) => fmt("en", v, d);

  it("says a share too small for one decimal is small instead of zero", () => {
    expect(pct1(f, 0.04)).toBe("< 0.1");
    expect(pct1(f, 0)).toBe("0.0");
    expect(pct1(f, 2.66)).toBe("2.7");
  });
});

describe("the study calendar", () => {
  it("counts the hours of each year", () => {
    expect(hoursInYear(2025)).toBe(8760);
    expect(hoursInYear(2024)).toBe(8784);
  });

  it("knows where each month starts", () => {
    expect(monthStarts(2025).slice(0, 3)).toEqual([0, 31, 59]);
    expect(monthStarts(2024)[2]).toBe(60);
  });

  it("reads an hour index as a date on the UTC+1 clock", () => {
    expect(hourToDate(2025, 0)).toEqual({ month: 0, day: 1, hour: 0, doy: 0 });
    expect(hourToDate(2025, 24 * 31 + 13)).toEqual({ month: 1, day: 1, hour: 13, doy: 31 });
  });
});

describe("dates", () => {
  // vitest.config.ts reads dates in UTC, so these are the same everywhere
  it("writes one date format, with or without its time", () => {
    expect(dateTime("en", "2026-11-01T14:03:00Z")).toBe("1 Nov 2026, 14:03");
    expect(dateTime("en", "2026-11-01T14:03:00Z", false)).toBe("1 Nov 2026");
    expect(clock("en", "2026-11-01T14:03:00Z")).toBe("14:03");
    expect(dateTime("sv", "2026-11-01T14:03:00Z", false)).toBe("1 nov. 2026");
  });

  it("writes a dash for anything that is not a date", () => {
    expect(dateTime("en", "not a date")).toBe("–");
    expect(clock("sv", "nope")).toBe("–");
  });
});

describe("median", () => {
  it("takes the middle value, or the mean of the middle two", () => {
    expect(median([3, 1, 2])).toBe(2);
    expect(median([4, 1, 3, 2])).toBe(2.5);
    expect(median([])).toBeNaN();
  });
});
