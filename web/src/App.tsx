import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { SheetIndex, TitleBlock } from "./components/Shell";
import { Icon } from "./components/Icon";
import { useT } from "./lib/i18n";
import { Loading } from "./components/bits";
import { useDismissable } from "./components/Dialog";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Palette } from "./components/Palette";
import { Shortcuts } from "./components/Shortcuts";
import { Toasts } from "./components/Toast";
import { NOTES } from "./lib/notes";
import { SCREENS, scrollPositions, useStore } from "./lib/store";

const National = lazy(() => import("./screens/National"));
const Twin = lazy(() => import("./screens/Twin"));
const Compare = lazy(() => import("./screens/Compare"));
const Request = lazy(() => import("./screens/Request"));
const Scenarios = lazy(() => import("./screens/Scenarios"));
const Method = lazy(() => import("./screens/Method"));

function Notes() {
  const t = useT();
  const screen = useStore((s) => s.screen);
  const lang = useStore((s) => s.lang);
  const open = useStore((s) => s.notes);
  const close = useCallback(() => useStore.getState().toggleNotes(), []);
  // not modal: the drawer sits beside the screen it annotates, so Tab leaves it
  const panel = useDismissable(open, close, false);
  if (!open) return null;
  return (
    <aside className="notes" aria-label={t("ui.notes")} ref={panel}>
      <div className="block-head">
        <h4>{t("ui.notesOf", { n: SCREENS.indexOf(screen) + 1, total: SCREENS.length })}</h4>
        <button className="icon-btn" aria-label={t("shell.close")} onClick={close}>
          <Icon name="close" size={16} />
        </button>
      </div>
      <ul>
        {NOTES[lang][screen].map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
    </aside>
  );
}

export default function App() {
  const t = useT();
  const screen = useStore((s) => s.screen);
  const [palette, setPalette] = useState(false);
  const [keys, setKeys] = useState(false);
  const [attempt, setAttempt] = useState(0);

  // Keys: 1–6 jump to a screen, Ctrl+→/← step, ⌘K or / opens the palette, ? the key
  // sheet, N the notes, F fullscreen. The sheet in Shortcuts.tsx lists the same set.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      // closest() only exists on elements: a key event whose target is the document
      // itself would otherwise throw and take the whole handler with it.
      const typing = !!target?.closest?.("input, select, textarea, canvas, [contenteditable='true']");
      if (e.altKey) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        setPalette(true);
        e.preventDefault();
        return;
      }
      if (typing) return;
      const st = useStore.getState();
      const i = SCREENS.indexOf(st.screen);
      const digit = Number(e.key);
      if (digit >= 1 && digit <= SCREENS.length && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
        st.setScreen(SCREENS[digit - 1]);
        e.preventDefault();
      } else if (e.key === "/" ) {
        setPalette(true);
        e.preventDefault();
      } else if (e.key === "?") {
        setKeys(true);
        e.preventDefault();
      } else if (e.key === "PageDown" || (e.ctrlKey && e.key === "ArrowRight")) {
        st.setScreen(SCREENS[Math.min(SCREENS.length - 1, i + 1)]);
        e.preventDefault();
      } else if (e.key === "PageUp" || (e.ctrlKey && e.key === "ArrowLeft")) {
        st.setScreen(SCREENS[Math.max(0, i - 1)]);
        e.preventDefault();
      } else if (e.key.toLowerCase() === "n" && !e.ctrlKey && !e.metaKey) {
        st.toggleNotes();
      } else if (e.key.toLowerCase() === "f" && !e.ctrlKey && !e.metaKey) {
        if (!document.fullscreenElement) document.documentElement.requestFullscreen?.().catch(() => undefined);
        else document.exitFullscreen?.().catch(() => undefined);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Each screen owns its own scroller above 1100 px, so there is no single position to
  // keep: remember one per screen and put it back when the screen comes round again.
  const previous = useRef(screen);
  useLayoutEffect(() => {
    const leaving = previous.current;
    if (leaving !== screen) {
      scrollPositions.set(leaving, lastScroll.current);
      previous.current = screen;
      lastScroll.current = 0;
    }
    // The screen is lazy, so its scroller does not exist yet when this runs. Try for a
    // few frames and give up rather than hold a handle on a screen the viewer has left.
    const want = scrollPositions.get(screen) ?? 0;
    let tries = 0;
    let raf = 0;
    const put = () => {
      const el = document.querySelector<HTMLElement>("main .page");
      // The element appears before its charts and tables have height, so an early
      // assignment clamps to nothing. Keep asking until the position sticks.
      if (el) el.scrollTop = want;
      const settled = el && (want === 0 || Math.abs(el.scrollTop - want) < 2);
      if (!settled && ++tries < 60) raf = requestAnimationFrame(put);
    };
    put();
    return () => cancelAnimationFrame(raf);
  }, [screen, attempt]);

  const lastScroll = useRef(0);
  useEffect(() => {
    const onScroll = (e: Event) => {
      const t = e.target as HTMLElement;
      if (t?.classList?.contains("page")) lastScroll.current = t.scrollTop;
    };
    document.addEventListener("scroll", onScroll, true);
    return () => document.removeEventListener("scroll", onScroll, true);
  }, []);

  return (
    <div className="app">
      <a className="skip" href="#main" onClick={(e) => { e.preventDefault(); document.getElementById("main")?.focus(); }}>{t("shell.skip")}</a>
      <SheetIndex onSearch={() => setPalette(true)} />
      <main id="main" tabIndex={-1} aria-label={t(`tab.${screen}`)}>
        <ErrorBoundary where={t(`tab.${screen}`)} onReset={() => setAttempt((a) => a + 1)} key={`${screen}-${attempt}`}>
          <Suspense fallback={<Loading />}>
            {screen === "national" && <National />}
            {screen === "twin" && <Twin />}
            {screen === "compare" && <Compare />}
            {screen === "request" && <Request />}
            {screen === "scenarios" && <Scenarios />}
            {screen === "method" && <Method />}
          </Suspense>
        </ErrorBoundary>
      </main>
      <TitleBlock onKeys={() => setKeys(true)} />
      <Notes />
      <Palette open={palette} onClose={() => setPalette(false)} />
      <Shortcuts open={keys} onClose={() => setKeys(false)} />
      <Toasts />
    </div>
  );
}
