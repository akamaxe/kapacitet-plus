import { useEffect, useState } from "react";

/** Whether the window matches a media query now, and as it changes. */
export function useMedia(query: string): boolean {
  const [on, setOn] = useState(() => window.matchMedia?.(query).matches ?? false);
  useEffect(() => {
    const m = window.matchMedia?.(query);
    if (!m) return;
    const read = () => setOn(m.matches);
    read();
    m.addEventListener("change", read);
    return () => m.removeEventListener("change", read);
  }, [query]);
  return on;
}

/** From this width the project tree stands open beside the screen; under it the screen needs the room. */
export const TREE_BESIDE = "(min-width: 1280px)";
/** From this width the folded tree's rail of icons shows; under it the tree is only a drawer. */
export const TREE_RAIL = "(min-width: 1100px)";
