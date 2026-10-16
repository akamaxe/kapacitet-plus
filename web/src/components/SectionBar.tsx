/**
 * A screen's own sections, stuck to the top of its scroller (design-system.md §3).
 *
 * Two long screens were a stack of identical cards with no way to tell where you were in
 * them or to get back to a part you had seen. This is the console's answer: the sections
 * on the left, the basis of the screen's figures on the right.
 *
 * The buttons are buttons, not links: `#capacity` in the address bar is a screen name to
 * this application's router, so an anchor would navigate away from the screen it belongs to.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useT } from "../lib/i18n";

export interface Section {
  id: string;
  label: string;
}

export function SectionBar({ sections, basis }: { sections: Section[]; basis?: ReactNode }) {
  const t = useT();
  const bar = useRef<HTMLDivElement>(null);
  const [here, setHere] = useState<string | undefined>(sections[0]?.id);

  // The array is rebuilt on every render, so the effect keys off the ids themselves —
  // otherwise it would tear the scroll listener down and set it up again each time.
  const ids = sections.map((s) => s.id).join(" ");
  useEffect(() => {
    const page = bar.current?.closest<HTMLElement>(".page");
    if (!page) return;
    const names = ids.split(" ");
    let raf = 0;
    const read = () => {
      raf = 0;
      // the line just under the sticky bar: the section crossing it is the one being read
      const line = page.getBoundingClientRect().top + (bar.current?.offsetHeight ?? 0) + 12;
      let cur = names[0];
      for (const id of names) {
        const el = document.getElementById(id);
        if (el && el.getBoundingClientRect().top <= line) cur = id;
      }
      // the last section is often too short to ever reach that line
      if (page.scrollTop + page.clientHeight >= page.scrollHeight - 4) cur = names[names.length - 1];
      setHere(cur);
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(read);
    };
    page.addEventListener("scroll", onScroll, { passive: true });
    read();
    return () => {
      page.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(raf);
    };
  }, [ids]);

  const go = (id: string) => {
    const el = document.getElementById(id);
    const page = bar.current?.closest<HTMLElement>(".page");
    if (!el || !page) return;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    // scrollIntoView moves every scrollable ancestor, the window included, which lifts
    // the whole frame off the top of the screen. Only this screen's scroller should move.
    const top = page.scrollTop + el.getBoundingClientRect().top - page.getBoundingClientRect().top
      - (bar.current?.offsetHeight ?? 0);
    page.scrollTo({ top: Math.max(0, top), behavior: still ? "auto" : "smooth" });
  };

  return (
    <div className="screen-bar" ref={bar}>
      <nav className="screen-sections" aria-label={t("shell.sections")}>
        {sections.map((s) => (
          <button key={s.id} onClick={() => go(s.id)} aria-current={here === s.id ? "true" : undefined}>
            {s.label}
          </button>
        ))}
      </nav>
      {basis ? <div className="screen-basis">{basis}</div> : null}
    </div>
  );
}
