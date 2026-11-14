import { describe, expect, it } from "vitest";
import { houseStatus } from "./houses";

// A house shows how the envelope deployment connects it where the envelopes are on, and how the
// hourly study's deployment does where they are off.
describe("a house's status", () => {
  it("follows the envelope deployment where the envelopes are on", () => {
    expect(houseStatus({ pv_status: "firm", hourly_status: "hourly" }, true)).toBe("firm");
    expect(houseStatus({ pv_status: "nonfirm" }, true)).toBe("nonfirm");
    expect(houseStatus({ pv_status: "none", hourly_status: "static" }, true)).toBe("none");
  });

  it("follows the hourly study where they are off, and says no solar until it is computed", () => {
    expect(houseStatus({ pv_status: "firm", hourly_status: "static" }, false)).toBe("static");
    expect(houseStatus({ pv_status: "nonfirm", hourly_status: "hourly" }, false)).toBe("hourly");
    expect(houseStatus({ pv_status: "nonfirm", hourly_status: "none" }, false)).toBe("none");
    expect(houseStatus({ pv_status: "firm", hourly_status: null }, false)).toBe("none");
  });
});
