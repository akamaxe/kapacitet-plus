import { useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../lib/api";
import { Icon } from "./Icon";
import { useT } from "../lib/i18n";

export function Chip({ kind }: { kind: "real" | "synthetic" | "reference" }) {
  const t = useT();
  const label = kind === "real" ? t("ui.real") : kind === "synthetic" ? t("ui.synthetic") : t("ui.reference");
  return <span className={`chip ${kind}`}>{label}</span>;
}

const ICONS: Record<string, ReactNode> = {
  good: (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden>
      <circle cx="8" cy="8" r="7" fill="currentColor" />
      <path d="M4.6 8.2l2.2 2.2 4.6-4.8" stroke="var(--on-accent)" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  warning: (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden>
      <path d="M8 1.5l7 12.5H1z" fill="currentColor" />
      <path d="M8 6v3.6M8 11.6v.1" stroke="var(--on-accent)" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  ),
  serious: (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden>
      <rect x="1.5" y="1.5" width="13" height="13" rx="3" fill="currentColor" />
      <path d="M8 4.5v4.2M8 11.2v.1" stroke="var(--on-accent)" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  ),
  critical: (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden>
      <circle cx="8" cy="8" r="7" fill="currentColor" />
      <path d="M5.4 5.4l5.2 5.2M10.6 5.4l-5.2 5.2" stroke="var(--on-accent)" strokeWidth="1.8" strokeLinecap="round" />
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

export function Tile({ label, value, unit, note }: { label: string; value: ReactNode; unit?: string; note?: ReactNode }) {
  return (
    <div className="tile">
      <div className="t-label">{label}</div>
      <div className="t-value">
        {value}
        {unit ? <small>{unit}</small> : null}
      </div>
      {note ? <div className="t-note">{note}</div> : null}
    </div>
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

export function Meter({ label, value, max = 100, limit = 100, unit = "%", digits = 0 }: {
  label: string; value: number; max?: number; limit?: number; unit?: string; digits?: number;
}) {
  const w = Math.max(0, Math.min(1, value / max)) * 100;
  const over = value > limit;
  return (
    <div className="meter">
      <div className="meter-head">
        <span>{label}</span>
        <strong className="num">{value.toFixed(digits)} {unit}</strong>
      </div>
      <div className="meter-track" role="meter" aria-valuenow={value} aria-valuemax={max} aria-label={label}>
        <div className={`meter-fill ${over ? "critical" : ""}`} style={{ width: `${w}%` }} />
        <div className="meter-limit" style={{ left: `calc(${(limit / max) * 100}% - 1px)` }} />
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
  return <div className="empty" role="status"><span className="loading-orbit"><Icon name="grid" size={30} /></span><span>{t("ui.loading")}</span></div>;
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
      <span className="loading-orbit"><Icon name="alert" size={28} /></span>
      <h2>{t("ui.unavailable")}</h2>
      <p className="caption">{gone ? t("ui.offline") : t("ui.missing")}</p>
      {failed && <p className="caption-muted mono">{failed.endpoint}{failed.status ? ` → ${failed.status}` : ""}</p>}
      <button className="btn ghost" onClick={() => void client.refetchQueries({ type: "active" })}>
        <Icon name="reset" size={16} />{t("ui.retry")}
      </button>
    </div>
  );
}
