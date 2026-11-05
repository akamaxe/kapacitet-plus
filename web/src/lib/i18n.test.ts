import { beforeAll, describe, expect, it } from "vitest";
import { DICTS, LANGS, isLang, loadLang, locale, translate } from "./i18n/core";

beforeAll(async () => {
  for (const lang of LANGS) await loadLang(lang);
});

describe("dictionaries", () => {
  it("every language loads", () => {
    for (const lang of LANGS) expect(DICTS[lang], lang).toBeTruthy();
  });

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
    expect(translate("sv", "no.such.key")).toBe("no.such.key");
  });

  it("keeps a figure and its unit on one line, in every language", () => {
    expect(translate("en", "apply.ans.tiny", { min: "0.1" })).toContain("0.1 %");
    expect(translate("sv", "apply.ans.tiny", { min: "0,1" })).toContain("0,1 %");
    for (const [lang, dict] of Object.entries(DICTS)) {
      const loose = Object.keys(dict).filter((k) => /\} (%|kW|kWh|kWp|SEK|kSEK|h|MB|GB)(?=$|[\s.,;:)])/.test(dict[k]));
      expect(loose, lang).toEqual([]);
    }
  });

  it("names each language's own locale", () => {
    expect(locale("en")).toBe("en-GB");
    expect(locale("sv")).toBe("sv-SE");
  });
});

describe("languages", () => {
  it("speaks English and Swedish only", () => {
    expect([...LANGS]).toEqual(["en", "sv"]);
  });

  it("does not know Spanish any more, so a stored 'es' opens in English", () => {
    // store.ts falls back to English for any value isLang refuses
    expect(isLang("es")).toBe(false);
    expect(isLang("sv")).toBe(true);
  });
});
