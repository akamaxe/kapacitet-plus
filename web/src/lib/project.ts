import { useApi } from "./api";
import { DICTS, useT, type TFn } from "./i18n";

/**
 * What kind of network is open. The reference network stands on real buildings and streets; an
 * example network is made up from end to end and drawn schematically; an imported one is what
 * an operator's tables say. A sentence that is true of one is false of the others.
 */
export type ProjectKind = "reference" | "example" | "imported";

/** `/meta.dataset`, as far as the kind needs it. */
export interface OpenDataset {
  imported?: boolean;
  synthetic?: boolean;
}

export function kindOf(dataset: OpenDataset | null | undefined): ProjectKind {
  if (!dataset?.imported) return "reference";
  return dataset.synthetic ? "example" : "imported";
}

/** The kind of the project in use; the reference network until the server has answered. */
export function useProjectKind(): ProjectKind {
  return kindOf(useApi<any>("/meta").data?.dataset);
}

/** The ending of a sentence's own words for a kind of project: `twin.sub.onExample`. */
export const VARIANT: Record<Exclude<ProjectKind, "reference">, string> = {
  example: "onExample",
  imported: "onImported",
};

/**
 * The key to ask the dictionary for: the sentence's own words for the open project's kind when
 * the dictionary has them (`key.onExample`, `key.onImported`), and `key` otherwise: for the
 * reference network, and for a sentence that is true of any network. English ships with the
 * page and holds every key, so it answers for both languages.
 */
export function variantKey(key: string, kind: ProjectKind, has: (key: string) => boolean = (k) => k in DICTS.en): string {
  if (kind === "reference") return key;
  const own = `${key}.${VARIANT[kind]}`;
  return has(own) ? own : key;
}

/** `t`, in the words of the open project: see variantKey. */
export function useProjectT(): TFn {
  const t = useT();
  const kind = useProjectKind();
  return (key, vars) => t(variantKey(key, kind), vars);
}
