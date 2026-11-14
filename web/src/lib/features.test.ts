import { describe, expect, it } from "vitest";
import { tFor } from "./i18n";
import { NONE, featuresOf, screenLabel, visibleScreens } from "./features";

// The envelopes, the Sweden screen, the 2030 scenarios and the portal are switched on per
// installation (src/kapacitet/features.py); a screen of a part that is off is shown nowhere.
const t = tFor("en");

describe("the parts an installation shows", () => {
  it("are none until the server says, and what it says after", () => {
    expect(featuresOf(undefined)).toEqual(NONE);
    expect(featuresOf({ features: { envelopes: true } })).toEqual({ ...NONE, envelopes: true });
  });

  it("leave four screens when every part is off, and all eight when every part is on", () => {
    expect(visibleScreens(NONE)).toEqual(["twin", "compare", "request", "method"]);
    expect(visibleScreens({ envelopes: true, national: true, scenarios: true, portal: true }))
      .toEqual(["national", "twin", "compare", "request", "operation", "report", "scenarios", "method"]);
    expect(visibleScreens({ ...NONE, envelopes: true })).toEqual(["twin", "compare", "request", "operation", "report", "method"]);
  });

  it("keep Operation for the portal's applications, named for them, where only the portal is on", () => {
    expect(visibleScreens({ ...NONE, portal: true })).toEqual(["twin", "compare", "request", "operation", "method"]);
    expect(screenLabel(t, "operation", { ...NONE, portal: true })).toBe("Applications");
    expect(screenLabel(t, "operation", { ...NONE, envelopes: true, portal: true })).toBe("Operation");
  });

  it("call the capacity screen by its envelopes only where they are on", () => {
    expect(screenLabel(t, "compare", NONE)).toBe("Capacity");
    expect(screenLabel(t, "compare", { ...NONE, envelopes: true })).toBe("Static vs envelopes");
    expect(screenLabel(t, "twin", NONE)).toBe("Neighbourhood");
  });
});
