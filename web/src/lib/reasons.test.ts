import { describe, expect, it } from "vitest";
import { fmt, duration } from "./format";
import { tFor } from "./i18n/core";
import { describeRequest, reasonLine } from "./reasons";

const f = (v: number | null | undefined, d = 0) => fmt("en", v, d);
const t = tFor("en");

describe("reasonLine", () => {
  it("says the rule's reason with its figures in the reader's format", () => {
    const line = reasonLine(t, f, { code: "curtailment_p90", params: { value: 4.237, limit: 3 } });
    expect(line).toBe("Curtailment of 4.2 % in one year in ten, above the threshold of 3.0 %.");
  });

  it("counts hours whole, and translates a refusal the queue gave as a code", () => {
    expect(reasonLine(t, f, { code: "violations", params: { value: 3 } })).toContain("3 hours");
    const q = reasonLine(t, f, { code: "queue", params: { detail: "still_violates" } });
    expect(q).toContain(t("err.still_violates"));
    expect(q).not.toContain("still_violates");
  });
});

describe("duration", () => {
  it("picks the largest unit that keeps a wait readable", () => {
    expect(duration("en", 42)).toBe("42 s");
    expect(duration("en", 12 * 60)).toBe("12 min");
    expect(duration("en", 3.5 * 3600)).toBe("3.5 h");
    expect(duration("sv", 2 * 86400)).toBe("2,0 d");
    expect(duration("en", null)).toBe("–");
  });
});

describe("describeRequest", () => {
  it("lists what a request asks for in one line", () => {
    expect(describeRequest(t, f, { pv_kwp: 10, ev_kw: 11, hp: true, bat_kwh: 0 })).toBe(
      `10 kWp · 11 kW ${t("req.evShort")} · ${t("req.hpShort")}`,
    );
  });
});
