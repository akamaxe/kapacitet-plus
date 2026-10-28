import { useStore } from "./store";
import { locale } from "./i18n";

/** Narrow no-break space: thousands separator, Swedish convention (design-system.md 6). */
const THIN = " ";

const formatters = new Map<string, Intl.NumberFormat>();

/** One formatter per language and precision, kept for the session (js-cache-function-results). */
function formatter(lang: string, digits: number): Intl.NumberFormat {
  const key = `${lang}:${digits}`;
  let nf = formatters.get(key);
  if (!nf) {
    // "always": Spanish otherwise leaves four-digit numbers ungrouped, and the two
    // languages have to read the same
    nf = new Intl.NumberFormat(locale(lang), {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
      useGrouping: "always",
    } as unknown as Intl.NumberFormatOptions); // "always" is ES2023; the lib here is ES2022
    formatters.set(key, nf);
  }
  return nf;
}

export function fmt(lang: string, value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "–";
  // Intl groups thousands with a comma in English and a dot in Spanish; every group
  // separator becomes a thin space, so a value reads the same in either language and the
  // decimal mark is never touched.
  return formatter(lang, digits)
    .formatToParts(value)
    .map((part) => (part.type === "group" ? THIN : part.value))
    .join("");
}

/** One formatting function per language, so a component's memos keep their identity. */
const FMT: Record<string, (value: number | null | undefined, digits?: number) => string> = {
  en: (value, digits = 0) => fmt("en", value, digits),
  es: (value, digits = 0) => fmt("es", value, digits),
};

export function useFmt() {
  const lang = useStore((s) => s.lang);
  return FMT[lang] ?? FMT.en;
}

const MONTHS = {
  en: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
  es: ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"],
};

export function monthNames(lang: string): string[] {
  return lang === "es" ? MONTHS.es : MONTHS.en;
}

/** Hour index of the study year (UTC+1 standard time) → local date parts. */
export function hourToDate(year: number, hour: number): { month: number; day: number; hour: number; doy: number } {
  const start = Date.UTC(year, 0, 1, 0, 0, 0);
  const d = new Date(start + hour * 3600_000);
  return { month: d.getUTCMonth(), day: d.getUTCDate(), hour: d.getUTCHours(), doy: Math.floor(hour / 24) };
}

export function hourLabel(lang: string, year: number, hour: number): string {
  const d = hourToDate(year, hour);
  const hh = String(d.hour).padStart(2, "0");
  return lang === "es"
    ? `${d.day} ${monthNames(lang)[d.month]} ${year}, ${hh}:00`
    : `${d.day} ${monthNames(lang)[d.month]} ${year}, ${hh}:00`;
}
