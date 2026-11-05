import { describe, expect, it } from "vitest";
import { REF, normalizeRef } from "./reference";

describe("a reference code", () => {
  it("is four groups of Crockford base32, or the two hex groups of an older code", () => {
    for (const ok of ["KP-7K3M-Q9TD-X2HB-5RWN", "KP-0000-0000-0000-0000", "KP-1A2B-3C4D"]) expect(REF.test(ok)).toBe(true);
    for (const bad of ["KP-7K3M-Q9TD-X2HB", "KP-UUUU-0000-0000-0000", "KP-1A2B-3C4G", "kp-1a2b-3c4d", "KP-0000-0000-0000-0000-0000", "nonsense"])
      expect(REF.test(bad)).toBe(false);
  });

  it("is read as typed: lower case, spaces round it, and the letters that look like digits", () => {
    expect(normalizeRef(" kp-7k3m-q9td-x2hb-5rwn ")).toBe("KP-7K3M-Q9TD-X2HB-5RWN");
    expect(normalizeRef("KP-OOOO-IIII-LLLL-0000")).toBe("KP-0000-1111-1111-0000");
    expect(REF.test(normalizeRef("kp-1a2b-3c4d"))).toBe(true);
  });
});
