/**
 * The output window: the lines the program wrote for what the person at it did (lib/log.ts), in
 * a dock above the status bar, as an engineering program has one. It is closed until asked for
 * (the status bar's last line, the O key, View > Output window) and never opens by itself.
 */
import { useLayoutEffect, useRef } from "react";
import { copyText } from "../lib/clipboard";
import { clock, dateTime } from "../lib/format";
import { useT } from "../lib/i18n";
import { logText, useLog } from "../lib/log";
import { useStore } from "../lib/store";
import { Icon } from "./Icon";
import { toast } from "./Toast";

export const OUTPUT_ID = "output-window";

export function OutputPanel() {
  const t = useT();
  const lang = useStore((s) => s.lang);
  const lines = useLog((s) => s.lines);
  const clear = useLog((s) => s.clear);
  const setOpen = useLog((s) => s.setOpen);
  const box = useRef<HTMLDivElement>(null);
  // the newest line stays in view, unless the reader has scrolled up to read an older one
  const atEnd = useRef(true);
  useLayoutEffect(() => {
    const el = box.current;
    if (el && atEnd.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  const copy = () => {
    copyText(logText(lines, (at) => dateTime(lang, at)))
      .then(() => toast.info(t("out.copied")))
      .catch(() => toast.error(t("pal.copyFailed")));
  };

  return (
    <section className="output" id={OUTPUT_ID} aria-labelledby="output-title">
      <header className="output-head">
        <h2 id="output-title">{t("out.title")}</h2>
        <span className="output-count num">{t("out.count", { n: lines.length })}</span>
        <button className="icon-btn" onClick={copy} disabled={lines.length === 0} aria-label={t("out.copy")} title={t("out.copy")}>
          <Icon name="copy" size={15} />
        </button>
        <button className="btn ghost small" onClick={clear} disabled={lines.length === 0}>{t("out.clear")}</button>
        <button className="icon-btn" onClick={() => setOpen(false)} aria-label={t("out.close")} title={`${t("out.close")} (O)`}>
          <Icon name="close" size={15} />
        </button>
      </header>
      {/* a log that reads itself out would say twice what a notice has just said */}
      <div
        className="output-lines"
        role="log"
        aria-live="off"
        aria-label={t("out.title")}
        tabIndex={0}
        ref={box}
        onScroll={(e) => {
          const el = e.currentTarget;
          atEnd.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
        }}
      >
        {lines.length === 0 && <p className="output-empty">{t("out.empty")}</p>}
        {lines.map((l) => (
          <div className={`out-line ${l.level}`} key={l.id}>
            <time dateTime={new Date(l.at).toISOString()} className="num">{clock(lang, l.at)}</time>
            {l.level === "error" && <Icon name="alert" size={14} />}
            {l.level === "ok" && <Icon name="check" size={14} />}
            <span>{l.level === "error" ? <span className="sr-only">{t("out.error")} </span> : null}{l.text}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

/** The status bar's part of it: the last line, which opens and closes the window. */
export function OutputLast() {
  const t = useT();
  const lang = useStore((s) => s.lang);
  const last = useLog((s) => s.lines[s.lines.length - 1]);
  const open = useLog((s) => s.open);
  const setOpen = useLog((s) => s.setOpen);
  return (
    <button className="sb-btn sb-output" onClick={() => setOpen(!open)} aria-expanded={open} aria-controls={open ? OUTPUT_ID : undefined}
      title={`${t(open ? "out.close" : "out.open")} (O)`}>
      <span className="sb-output-name">{t("out.title")}</span>
      {last && (
        <span className={`sb-output-last${last.level === "error" ? " error" : ""}`}>
          <span className="num">{clock(lang, last.at)}</span> {last.text}
        </span>
      )}
    </button>
  );
}
