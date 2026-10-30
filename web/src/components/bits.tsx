import { useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../lib/api";
import { Icon } from "./Icon";
import { useT } from "../lib/i18n";
import { useFmt } from "../lib/format";

type Provenance = "real" | "synthetic" | "reference";

/**
 * The provenance mark of design-system.md §3.6: surveyed data is drawn solid, assumed data
 * dashed, a reference hollow — the drafting conventions, set as type rather than as a badge.
 */
export function Mark({ kind }: { kind: Provenance }) {
  return <span className={`mark ${kind}`} aria-hidden="true" />;
}

/** A mark with its word. Kept under its old name so every screen that labels data still does. */
export function Chip({ kind }: { kind: Provenance }) {
  const t = useT();
  const label = kind === "real" ? t("ui.real") : kind === "synthetic" ? t("ui.synthetic") : t("ui.reference");
  return <span className={`chip ${kind}`}><Mark kind={kind} />{label}</span>;
}

const ICONS: Record<string, ReactNode> = {
  good: (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden>
      <circle cx="7" cy="7" r="6.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M4.2 7.2l1.9 1.9 3.8-4" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  warning: (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden>
      <path d="M7 1.4l6 11H1z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M7 5.4v3M7 10.3v.1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  ),
  serious: (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden>
      <rect x="1.25" y="1.25" width="11.5" height="11.5" rx="2" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M7 4v3.6M7 9.9v.1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  ),
  critical: (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden>
      <circle cx="7" cy="7" r="6.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M4.8 4.8l4.4 4.4M9.2 4.8l-4.4 4.4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  ),
};

/** Status is always icon + label, never colour alone. */
export function Status({ kind, children }: { kind: "good" | "warning" | "serious" | "critical"; children: ReactNode }) {
  return (
    <span className={`status ${kind}`}>
      {ICONS[kind]}
      <span>{children}</span>
    </span>
  );
}

/**
 * One reading of a data sheet (design-system.md §3.3): the label on the left, the value in
 * mono with its unit on the right, the note under it. A group of these sits in `.tiles`,
 * which draws rows parted by hairlines rather than a grid of boxes.
 */
export function Tile({ label, value, unit, note, mark, tone }: {
  label: ReactNode; value: ReactNode; unit?: string; note?: ReactNode;
  /** where the reading comes from, drawn before its label */
  mark?: Provenance;
  /** a reading that is itself the bad news takes the danger ink; nothing else is coloured */
  tone?: "danger";
}) {
  return (
    <div className="tile">
      <div className="t-label">{mark ? <Mark kind={mark} /> : null}{label}</div>
      <div className={`t-value${tone ? ` ${tone}` : ""}`}>
        {value}
        {unit ? <small>{unit}</small> : null}
      </div>
      {note ? <div className="t-note">{note}</div> : null}
    </div>
  );
}

/**
 * A verification protocol (design-system.md §3.3): one row per check, with its verdict,
 * what was measured, the measured value and the criterion it was held against.
 */
export function Protocol({ rows }: {
  /** `ok: null` is a measurement with no pass mark of its own, such as an agreement with a reference tool */
  rows: { ok: boolean | null; label: string; value: ReactNode; unit?: string; criterion?: ReactNode }[];
}) {
  const t = useT();
  return (
    <table className="protocol">
      <tbody>
        {rows.map((r) => (
          <tr key={r.label}>
            <td className="p-mark">
              {r.ok === null
                ? <span className="status measured">{t("ui.measured")}</span>
                : <Status kind={r.ok ? "good" : "critical"}>{r.ok ? t("ui.pass") : t("ui.fail")}</Status>}
            </td>
            <th scope="row">{r.label}</th>
            <td className="p-value num">{r.value}{r.unit ? <small>{r.unit}</small> : null}</td>
            <td className="p-criterion">{r.criterion}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * The one number a screen is about (§11), and the only thing on it at this size.
 *
 * `label` says what the number is, `note` what it is relative to. It is set in the mono
 * face because it is a reading off an instrument, not a headline.
 */
export function Hero({ value, unit, label, note }: { value: ReactNode; unit?: string; label: string; note?: ReactNode }) {
  return (
    <div className="hero-stat">
      <div className="hero-value">
        {value}
        {unit ? <small>{unit}</small> : null}
      </div>
      <div className="hero-label">{label}</div>
      {note ? <div className="hero-note">{note}</div> : null}
    </div>
  );
}

/** A loading as a scale with ticks at 50 and 85 % of the limit, and the limit itself. */
export function Meter({ label, value, max = 100, limit = 100, unit = "%", digits = 0 }: {
  label: string; value: number; max?: number; limit?: number; unit?: string; digits?: number;
}) {
  const f = useFmt();
  const share = Math.max(0, Math.min(1, value / max));
  const over = value > limit;
  // the same thresholds the line map colours a cable by: 85 % of the limit, then the limit
  const near = !over && value >= limit * 0.85;
  const at = (x: number) => `${(x / max) * 100}%`;
  return (
    <div className="meter">
      <div className="meter-head">
        <span>{label}</span>
        <strong>{f(value, digits)}<small>{unit}</small></strong>
      </div>
      <div className="meter-track" role="meter" aria-valuenow={value} aria-valuemax={max} aria-label={label}>
        {/* scaled rather than resized, so an hour step animates on the compositor */}
        <div className={`meter-fill${over ? " critical" : near ? " near" : ""}`} style={{ transform: `scaleX(${share})` }} />
        <span className="meter-tick" style={{ left: at(limit * 0.5) }} />
        <span className="meter-tick" style={{ left: at(limit * 0.85) }} />
        <span className="meter-limit" style={{ left: at(limit) }} />
      </div>
    </div>
  );
}

/** A chart with a table-view twin (the accessible equivalent). */
export function ChartOrTable({ chart, table }: { chart: ReactNode; table: ReactNode }) {
  const t = useT();
  const [asTable, setAsTable] = useState(false);
  return (
    <div className="stack">
      {asTable ? <div className="table-scroll">{table}</div> : chart}
      <div>
        <button className="table-toggle" aria-pressed={asTable} onClick={() => setAsTable(!asTable)}>
          {asTable ? t("ui.chart") : t("ui.table")}
        </button>
      </div>
    </div>
  );
}

export function Legend({ items }: { items: { color: string; label: string; line?: boolean }[] }) {
  return (
    <div className="legend">
      {items.map((it) => (
        <span key={it.label}>
          <i className={it.line ? "line" : ""} style={{ background: it.color }} />
          {it.label}
        </span>
      ))}
    </div>
  );
}

export function Loading() {
  const t = useT();
  return <div className="empty" role="status"><span className="caption-muted">{t("ui.loading")}</span></div>;
}

/**
 * What failed, which source, and what to do about it (§7). Pass the queries the screen
 * depends on and the first one that broke names itself; with none, it still says the
 * general thing rather than nothing.
 */
export function Missing({ of }: { of?: { isError?: boolean; error?: unknown }[] }) {
  const t = useT();
  const client = useQueryClient();
  const failed = of?.map((q) => q.error).find((e): e is ApiError => e instanceof ApiError);
  const gone = failed?.status === 0;
  return (
    <div className="empty" role="alert">
      <h2>{t("ui.unavailable")}</h2>
      <p className="caption">{gone ? t("ui.offline") : t("ui.missing")}</p>
      <button className="btn ghost" onClick={() => void client.refetchQueries({ type: "active" })}>
        <Icon name="reset" size={16} />{t("ui.retry")}
      </button>
      {/* the technical detail is there for whoever reports it, not as the message */}
      {failed && (
        <details className="error-detail">
          <summary>{t("ui.details")}</summary>
          <p className="caption-muted mono">{failed.endpoint}{failed.status ? ` · ${failed.status}` : ""}</p>
        </details>
      )}
    </div>
  );
}
