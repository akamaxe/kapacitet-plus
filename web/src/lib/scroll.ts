/**
 * Bring `el` into view inside whichever ancestor actually scrolls, and only that one.
 *
 * Above 1100 px a screen's panel is its own scroller; below it the panel grows and `main`
 * scrolls instead. `scrollIntoView` would move every scrollable ancestor up to the window and
 * lift the whole frame off screen, so this walks up to the first ancestor that can scroll and
 * moves just that.
 */
export function scrollToInScroller(el: HTMLElement, offset = 16): void {
  const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  let box: HTMLElement | null = el.parentElement;
  while (box) {
    const oy = getComputedStyle(box).overflowY;
    if ((oy === "auto" || oy === "scroll") && box.scrollHeight > box.clientHeight + 1) break;
    box = box.parentElement;
  }
  if (!box) return;
  const top = box.scrollTop + el.getBoundingClientRect().top - box.getBoundingClientRect().top - offset;
  box.scrollTo({ top: Math.max(0, top), behavior: still ? "auto" : "smooth" });
}
