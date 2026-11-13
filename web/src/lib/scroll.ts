/** The first ancestor of `el` that scrolls and has something to scroll, or null. */
function scrollerOf(el: HTMLElement): HTMLElement | null {
  let box: HTMLElement | null = el.parentElement;
  while (box) {
    const oy = getComputedStyle(box).overflowY;
    if ((oy === "auto" || oy === "scroll") && box.scrollHeight > box.clientHeight + 1) return box;
    box = box.parentElement;
  }
  return null;
}

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
  const box = scrollerOf(el);
  if (!box) return;
  const top = box.scrollTop + el.getBoundingClientRect().top - box.getBoundingClientRect().top - offset;
  box.scrollTo({ top: Math.max(0, top), behavior: still ? "auto" : "smooth" });
}

/**
 * Call `then` with the element of that id once it is in the page: a screen arrives after its
 * code and its sections after their data, so the place a link names is not there at once.
 * Gives up after `frames` frames; the function returned stops looking.
 */
export function whenThere(id: string, then: (el: HTMLElement) => void, frames = 300): () => void {
  let raf = 0;
  let seen = 0;
  const look = () => {
    const el = document.getElementById(id);
    if (el) then(el);
    else if (++seen < frames) raf = requestAnimationFrame(look);
  };
  raf = requestAnimationFrame(look);
  return () => cancelAnimationFrame(raf);
}

/**
 * Put `el` at the top of its scroller and keep it there while the page above it is still
 * arriving: a screen opened at one of its sections gets its charts and tables after the section
 * exists, and each one that lands would push the section out of view again. It jumps rather
 * than glides (it answers a menu), stops once the place has held for a moment or `forMs` have
 * passed, and gives way at once to the reader's own wheel, finger or key.
 */
export function settleOn(el: HTMLElement, offset = 16, forMs = 6000): () => void {
  const started = performance.now();
  let timer = 0;
  let calm = 0;
  let done = false;
  const yield_ = ["wheel", "touchstart", "keydown", "pointerdown"] as const;
  const stop = () => {
    if (done) return;
    done = true;
    window.clearTimeout(timer);
    for (const ev of yield_) window.removeEventListener(ev, stop, true);
  };
  for (const ev of yield_) window.addEventListener(ev, stop, { capture: true, passive: true });
  const aim = () => {
    if (done) return;
    if (!el.isConnected) return stop();
    const box = scrollerOf(el);
    if (box) {
      const off = el.getBoundingClientRect().top - box.getBoundingClientRect().top - offset;
      const room = box.scrollHeight - box.clientHeight - box.scrollTop;
      // as far as the page allows: the last section of a short page cannot reach the top
      const by = off > 0 ? Math.min(off, room) : off;
      if (Math.abs(by) > 1) {
        box.scrollTop += by;
        calm = 0;
      } else calm++;
    }
    if (calm >= 10 || performance.now() - started > forMs) return stop();
    timer = window.setTimeout(aim, 100);
  };
  aim();
  return stop;
}
