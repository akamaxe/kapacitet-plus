import { useStore } from "./store";
import { locale } from "./i18n/core";

/** Narrow no-break space: thousands separator, Swedish convention (DESIGN.md). */
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

type FmtFn = (value: number | null | undefined, digits?: number) => string;
const FMT = new Map<string, FmtFn>();

/** One formatting function per language, so a component's memos keep their identity. */
export function fmtFor(lang: string): FmtFn {
  let f = FMT.get(lang);
  if (!f) {
    f = (value, digits = 0) => fmt(lang, value, digits);
    FMT.set(lang, f);
  }
  return f;
}

export function useFmt() {
  const lang = useStore((s) => s.lang);
  return fmtFor(lang);
}

const MONTHS = {
  en: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
  es: ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"],
  sv: ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"],
};

export function monthNames(lang: string): string[] {
  return MONTHS[lang as keyof typeof MONTHS] ?? MONTHS.en;
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
  return `${d.day} ${monthNames(lang)[d.month]} ${year}, ${hh}:00`;
}

/** Hours in a calendar year of the study (8 760, or 8 784 in a leap year). */
export function hoursInYear(year: number): number {
  return (Date.UTC(year + 1, 0, 1) - Date.UTC(year, 0, 1)) / 3600_000;
}

/** Day of year (0-based) on which each month starts, for that year's calendar. */
export function monthStarts(year: number): number[] {
  return Array.from({ length: 12 }, (_, m) => (Date.UTC(year, m, 1) - Date.UTC(year, 0, 1)) / 86_400_000);
}

/** The median of a list: the mean of the two middle values when the count is even. */
export function median(xs: number[]): number {
  if (!xs.length) return NaN;
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/**
 * A share to one decimal, as every percentage on the request answer and its record; a non-zero
 * share too small for that says so instead of "0.0".
 */
export function pct1(f: (v: number | null | undefined, d?: number) => string, v: number | null | undefined): string {
  return v != null && v > 0 && v < 0.05 ? `< ${f(0.1, 1)}` : f(v, 1);
}

/** A value rounded for a chart's data (not for display: that goes through `fmt`). */
export function round(value: number, digits = 0): number {
  const k = 10 ** digits;
  return Math.round(value * k) / k;
}

const dateFormatters = new Map<string, Intl.DateTimeFormat>();

/**
 * A date as every screen writes it (DESIGN.md: one date format): "1 Nov 2026", and with
 * its time "1 Nov 2026, 14:03". Anything that is not a date reads "–".
 */
export function dateTime(lang: string, value: string | number | Date, withTime = true): string {
  const key = `${lang}:${withTime}`;
  let df = dateFormatters.get(key);
  if (!df) {
    df = new Intl.DateTimeFormat(locale(lang), withTime ? { dateStyle: "medium", timeStyle: "short" } : { dateStyle: "medium" });
    dateFormatters.set(key, df);
  }
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? "–" : df.format(d);
}

/** The time of day alone, "14:03", for a reading taken today. */
export function clock(lang: string, value: string | number | Date): string {
  const key = `${lang}:clock`;
  let df = dateFormatters.get(key);
  if (!df) {
    df = new Intl.DateTimeFormat(locale(lang), { hour: "2-digit", minute: "2-digit" });
    dateFormatters.set(key, df);
  }
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? "–" : df.format(d);
}
