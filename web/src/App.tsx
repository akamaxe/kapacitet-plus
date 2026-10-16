import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { SideNav, StatusBar, TopBar } from "./components/Shell";
import { Icon } from "./components/Icon";
import { useT } from "./lib/i18n";
import { Loading } from "./components/bits";
import { useDismissable } from "./components/Dialog";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Palette } from "./components/Palette";
import { Shortcuts } from "./components/Shortcuts";
import { Toasts } from "./components/Toast";
import { NOTES } from "./lib/notes";
import { SCREENS, useStore } from "./lib/store";

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

  return (
    <div className="app">
      <a className="skip" href="#main" onClick={(e) => { e.preventDefault(); document.getElementById("main")?.focus(); }}>{t("shell.skip")}</a>
      <SideNav onSearch={() => setPalette(true)} />
      <TopBar onSearch={() => setPalette(true)} />
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
      <StatusBar onKeys={() => setKeys(true)} />
      <Notes />
      <Palette open={palette} onClose={() => setPalette(false)} />
      <Shortcuts open={keys} onClose={() => setKeys(false)} />
      <Toasts />
    </div>
  );
}
