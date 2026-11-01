import { describe, expect, it } from "vitest";
import { DICTS, translate } from "./i18n";

describe("dictionaries", () => {
  it("every language has exactly the English keys", () => {
    const en = Object.keys(DICTS.en).sort();
    for (const [lang, dict] of Object.entries(DICTS)) {
      expect(Object.keys(dict).sort(), lang).toEqual(en);
    }
  });

  it("no string is empty", () => {
    for (const [lang, dict] of Object.entries(DICTS)) {
      const empty = Object.entries(dict).filter(([, v]) => !v.trim()).map(([k]) => k);
      expect(empty, lang).toEqual([]);
    }
  });

  it("a translation keeps the placeholders of the English string", () => {
    const names = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
    for (const [lang, dict] of Object.entries(DICTS)) {
      if (lang === "en") continue;
      const off = Object.keys(dict).filter((k) => names(dict[k]).join() !== names(DICTS.en[k]).join());
      expect(off, lang).toEqual([]);
    }
  });
});

describe("translate", () => {
  it("fills placeholders and leaves an unknown one visible", () => {
    expect(translate("en", "pal.feeder", { n: 3 })).toBe("Feeder 3");
    expect(translate("en", "pal.feeder")).toBe("Feeder {n}");
  });

  it("falls back to English, then to the key itself", () => {
    expect(translate("xx", "pal.title")).toBe(DICTS.en["pal.title"]);
    expect(translate("es", "no.such.key")).toBe("no.such.key");
  });
});
