import type { Screen } from "./store";

/**
 * How many talking points the presenter drawer (N) has per screen. The text lives in i18n.ts
 * as `note.<screen>.<n>`, so it is translated like every other string and its figures (the
 * year, the weather years, the solver's accuracy) are filled in from the API.
 */
export const NOTE_COUNT: Record<Screen, number> = {
  national: 3,
  twin: 3,
  compare: 4,
  request: 3,
  report: 2,
  scenarios: 3,
  method: 4,
};
