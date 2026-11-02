import en from "./en";

export type Dict = Record<string, string>;
export type Lang = "en" | "es" | "sv";

/** The interface's languages, in the order the language control shows them. */
export const LANGS: readonly Lang[] = ["en", "es", "sv"];

/** Each language named in itself, as a language control writes it. */
export const LANG_NAMES: Record<Lang, string> = { en: "English", es: "Español", sv: "Svenska" };

export function isLang(value: unknown): value is Lang {
  return typeof value === "string" && (LANGS as readonly string[]).includes(value);
}

/** A placeholder and the unit after it ("{mean} %", "{h} h"): the space between them never breaks. */
const UNIT_AFTER_PLACEHOLDER = /\} (%|kWp|kWh|kW|kVA|MVA|MWh|kSEK|SEK|pu|h)(?=$|[\s.,;:)\/·–—])/g;

/** The dictionary with every figure bound to its unit by a no-break space, so no line starts with "%". */
function bindUnits(dict: Dict): Dict {
  const out: Dict = {};
  for (const [k, v] of Object.entries(dict)) out[k] = v.replace(UNIT_AFTER_PLACEHOLDER, "}\u00a0$1");
  return out;
}

/**
 * The dictionaries loaded so far. English ships with the page; Spanish and Swedish are their own
 * chunks, loaded before the interface switches to them (store.setLang, and main.tsx for the
 * stored language), so no screen ever flashes in the wrong language.
 */
export const DICTS: Record<string, Dict> = { en: bindUnits(en) };

const LOADERS: Record<string, () => Promise<{ default: Dict }>> = {
  es: () => import("./es"),
  sv: () => import("./sv"),
};

/** Load a language's dictionary once; resolves when `translate` can use it. */
export async function loadLang(lang: Lang): Promise<void> {
  if (DICTS[lang]) return;
  const load = LOADERS[lang];
  if (load) DICTS[lang] = bindUnits((await load()).default);
}

export function translate(lang: string, key: string, vars?: Record<string, string | number>): string {
  const raw = DICTS[lang]?.[key] ?? DICTS.en[key] ?? key;
  if (!vars) return raw;
  return raw.replace(/\{(\w+)\}/g, (_, k) => (vars[k] !== undefined ? String(vars[k]) : `{${k}}`));
}

export type TFn = (key: string, vars?: Record<string, string | number>) => string;
const T = new Map<string, TFn>();

/** One translate function per language, so a component's memos keep their identity. */
export function tFor(lang: string): TFn {
  let t = T.get(lang);
  if (!t) {
    t = (key, vars) => translate(lang, key, vars);
    T.set(lang, t);
  }
  return t;
}

export function locale(lang: string): string {
  return lang === "es" ? "es-ES" : lang === "sv" ? "sv-SE" : "en-GB";
}
