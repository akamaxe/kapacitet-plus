/**
 * Which parts of the product this installation shows (src/kapacitet/features.py): the envelopes,
 * the Sweden screen, the 2030 scenarios and the applicant's portal are off unless the server was
 * started with them (KAPACITET_FEATURES). A screen of a part that is off is in no menu, tree,
 * search or key, and a link to it opens the capacity screen instead. The capacity screen keeps its
 * old name, "Static vs envelopes", only where the envelopes are on, and Operation, which also holds the
 * portal's applications, is "Applications" where only the portal is on.
 */
import { useApi } from "./api";
import type { TFn } from "./i18n";
import { SCREENS, type Screen } from "./store";

export type Feature = "envelopes" | "national" | "scenarios" | "portal";
export type Features = Record<Feature, boolean>;

export const NONE: Features = { envelopes: false, national: false, scenarios: false, portal: false };

/** The parts /health or /meta says are on; nothing is on until the server has said. Pure. */
export function featuresOf(answer: { features?: Partial<Features> } | null | undefined): Features {
  return { ...NONE, ...(answer?.features ?? {}) };
}

/** The parts each screen belongs to, shown when any of them is on; a screen not named here is always shown. */
export const SCREEN_FEATURE: Partial<Record<Screen, Feature[]>> = {
  national: ["national"],
  // the applications of the portal are decided in Operation, so it is there with either part
  operation: ["envelopes", "portal"],
  report: ["envelopes"],
  scenarios: ["scenarios"],
};

/** The screens this installation shows, in their order (the keys 1, 2, … follow it). Pure. */
export function visibleScreens(f: Features): Screen[] {
  return SCREENS.filter((s) => {
    const parts = SCREEN_FEATURE[s];
    return !parts || parts.some((p) => f[p]);
  });
}

/** A screen's name where this installation shows it. Pure. */
export function screenLabel(t: TFn, s: Screen, f: Features): string {
  if (s === "compare" && !f.envelopes) return t("tab.capacity");
  if (s === "operation" && !f.envelopes) return t("tab.applications");
  return t(`tab.${s}`);
}

/** The parts the server in use shows, from its /health (asked once, shared with the frame). */
export function useFeatures(): Features {
  return featuresOf(useApi<any>("/health").data);
}

/** Whether the server has said which parts it shows yet. */
export function useFeaturesKnown(): boolean {
  return !!useApi<any>("/health").data;
}
