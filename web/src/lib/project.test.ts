import { describe, expect, it } from "vitest";
import en from "./i18n/en";
import sv from "./i18n/sv";
import { VARIANT, kindOf, variantKey } from "./project";

// A sentence about the reference network ("real buildings from OpenStreetMap") is false of an
// example network and of an imported one. Each such sentence has its own words for both, and
// the screen asks for the ones that fit the project in use.

describe("the kind of the open project", () => {
  it("is the reference network until the server says otherwise", () => {
    expect(kindOf(undefined)).toBe("reference");
    expect(kindOf(null)).toBe("reference");
    expect(kindOf({})).toBe("reference");
    expect(kindOf({ imported: false, synthetic: false })).toBe("reference");
  });

  it("tells an example network from an operator's", () => {
    expect(kindOf({ imported: true, synthetic: true })).toBe("example");
    expect(kindOf({ imported: true, synthetic: false })).toBe("imported");
    expect(kindOf({ imported: true })).toBe("imported");
  });
});

describe("the words of the open project", () => {
  const has = (k: string) => ["twin.sub", "twin.sub.onExample", "twin.sub.onImported", "twin.title"].includes(k);

  it("keeps the reference network's words for the reference network", () => {
    expect(variantKey("twin.sub", "reference", has)).toBe("twin.sub");
  });

  it("takes the kind's own words where the dictionary has them", () => {
    expect(variantKey("twin.sub", "example", has)).toBe("twin.sub.onExample");
    expect(variantKey("twin.sub", "imported", has)).toBe("twin.sub.onImported");
  });

  it("keeps a sentence that is true of any network", () => {
    expect(variantKey("twin.title", "example", has)).toBe("twin.title");
    expect(variantKey("twin.title", "imported", has)).toBe("twin.title");
  });

  it("reads the dictionary the page ships with", () => {
    expect(variantKey("twin.sub", "example")).toBe("twin.sub.onExample");
    expect(variantKey("twin.title", "example")).toBe("twin.title");
  });

  it("has both kinds' words for every sentence that has one, in both languages", () => {
    const endings = Object.values(VARIANT).map((v) => `.${v}`);
    for (const [name, dict] of [["en", en], ["sv", sv]] as const) {
      const bases = new Set<string>();
      for (const key of Object.keys(dict)) {
        const ending = endings.find((e) => key.endsWith(e));
        if (ending) bases.add(key.slice(0, -ending.length));
      }
      expect(bases.size, name).toBeGreaterThan(0);
      for (const base of bases) {
        expect(dict[base], `${name}: ${base}`).toBeTruthy();
        for (const ending of endings) expect(dict[`${base}${ending}`], `${name}: ${base}${ending}`).toBeTruthy();
      }
    }
  });

  it("never says OpenStreetMap or a place's name of a network that is not the reference one", () => {
    const endings = Object.values(VARIANT).map((v) => `.${v}`);
    for (const dict of [en, sv]) {
      for (const [key, text] of Object.entries(dict)) {
        if (endings.some((e) => key.endsWith(e))) expect(text, key).not.toMatch(/OpenStreetMap|Smedslätten|Bromma/);
      }
    }
  });
});
