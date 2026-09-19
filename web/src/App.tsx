import { lazy, Suspense, useEffect } from "react";
import { TopBar } from "./components/TopBar";
import { Loading } from "./components/bits";
import { NOTES } from "./lib/notes";
import { SCREENS, useStore } from "./lib/store";

const National = lazy(() => import("./screens/National"));
const Twin = lazy(() => import("./screens/Twin"));
const Compare = lazy(() => import("./screens/Compare"));
const Request = lazy(() => import("./screens/Request"));
const Scenarios = lazy(() => import("./screens/Scenarios"));
const Method = lazy(() => import("./screens/Method"));

function Notes() {
  const screen = useStore((s) => s.screen);
  const lang = useStore((s) => s.lang);
  const open = useStore((s) => s.notes);
  if (!open) return null;
  return (
    <aside className="notes" aria-label="Presenter notes">
      <h4>{SCREENS.indexOf(screen) + 1} / {SCREENS.length}</h4>
      <ul>
        {NOTES[lang][screen].map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
    </aside>
  );
}

export default function App() {
  const screen = useStore((s) => s.screen);

  // Presentation keys: PageDown/PageUp or Ctrl+→/← move between screens, N toggles notes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target && (target.tagName === "INPUT" || target.tagName === "SELECT" || target.tagName === "CANVAS")) return;
      const st = useStore.getState();
      const i = SCREENS.indexOf(st.screen);
      if (e.key === "PageDown" || (e.ctrlKey && e.key === "ArrowRight")) {
        st.setScreen(SCREENS[Math.min(SCREENS.length - 1, i + 1)]);
        e.preventDefault();
      } else if (e.key === "PageUp" || (e.ctrlKey && e.key === "ArrowLeft")) {
        st.setScreen(SCREENS[Math.max(0, i - 1)]);
        e.preventDefault();
      } else if (e.key.toLowerCase() === "n" && !e.ctrlKey && !e.metaKey) {
        st.toggleNotes();
      } else if (e.key === "f" && !e.ctrlKey) {
        if (!document.fullscreenElement) document.documentElement.requestFullscreen?.().catch(() => undefined);
        else document.exitFullscreen?.().catch(() => undefined);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="app">
      <a className="skip" href="#main">Skip to content</a>
      <TopBar />
      <main id="main">
        <Suspense fallback={<Loading />}>
          {screen === "national" && <National />}
          {screen === "twin" && <Twin />}
          {screen === "compare" && <Compare />}
          {screen === "request" && <Request />}
          {screen === "scenarios" && <Scenarios />}
          {screen === "method" && <Method />}
        </Suspense>
      </main>
      <Notes />
    </div>
  );
}
