import { useStore } from "../store";
import { tFor, type TFn } from "./core";

export * from "./core";

/** The translate function of the active language. */
export function useT(): TFn {
  const lang = useStore((s) => s.lang);
  return tFor(lang);
}
