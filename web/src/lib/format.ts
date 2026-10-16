import { useStore } from "./store";
import { locale } from "./i18n";

/** Narrow no-break space: thousands separator, Swedish convention (design-system.md 6). */
const THIN = " ";

export function fmt(lang: string, value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "–";
  const out = new Intl.NumberFormat(locale(lang), {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
  // Intl gives a comma in English and a dot in Spanish; both become a thin space so a
  // value reads the same whichever language the interface is in.
  return out.replace(/[,.  ](?=\d{3})/g, THIN);
}

export function useFmt() {
  const lang = useStore((s) => s.lang);
  return (value: number | null | undefined, digits = 0) => fmt(lang, value, digits);
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
