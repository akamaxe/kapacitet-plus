import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Icon } from "./Icon";
import { RoleNote } from "./RoleNote";
import { Term } from "./Term";
import { toast } from "./Toast";
import { apiWrite, useApi } from "../lib/api";
import { useCan } from "../lib/auth";
import { responseRefusal } from "../lib/errors";
import { dateTime, useFmt } from "../lib/format";
import { useT, type TFn } from "../lib/i18n";
import { scrollToInScroller } from "../lib/scroll";
import { useStore } from "../lib/store";

/** One setting's control as the model defines it (GET /settings `schema`, dso.schema). */
export interface FieldSpec {
  kind: "number" | "integer" | "choice" | "boolean" | "text" | "time" | "object";
  nullable: boolean;
  default: unknown;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  max_length?: number;
  pattern?: string;
  options?: string[];
}

export interface SettingsReply {
  settings: Record<string, any>;
  schema: Record<string, FieldSpec>;
  /** the latest sealed change: when, and under which role (none with sign-in off) */
  changed?: { at: string; role: string | null } | null;
}

/** Where a setting that has its own control is changed: a block on this screen, or another screen. */
type Where = { block: string; label: string } | { screen: string; label: string };

interface Row {
  key: string;
  /** a glossary entry for its name */
  term?: string;
  /** changed here */
  edit?: boolean;
  /** changed elsewhere, and linked */
  where?: Where;
}

interface Group {
  id: "rule" | "envelopes" | "market" | "portal" | "ei";
  rows: Row[];
}

// The groups and their order; every field of dso.DsoSettings is in exactly one (a test checks it).
// No value, bound or unit is written here: they come from the settings reply.
export const GROUPS: Group[] = [
  { id: "rule", rows: [
    { key: "auto_decisions", where: { block: "inbox-rule", label: "inbox.rule.title" } },
    { key: "rule_signature", where: { block: "inbox-rule", label: "inbox.rule.title" } },
    { key: "inbox_baseline", edit: true },
    { key: "auto_curtailment_mean_pct", edit: true, term: "curtailment" },
    { key: "auto_curtailment_p90_pct", edit: true, term: "curtailment" },
    { key: "auto_ev_unserved_pct", edit: true },
  ] },
  { id: "envelopes", rows: [
    { key: "allocation_policy", where: { screen: "report", label: "rep.rule" } },
    { key: "agreed_level_pct", edit: true, term: "curtailment" },
    { key: "import_window_days", term: "planningCeiling", where: { block: "op-import", label: "op.imp.title" } },
    { key: "deferral_years", edit: true },
  ] },
  { id: "market", rows: [
    { key: "market_result_local", edit: true },
    { key: "redispatch_compensation_sek", edit: true },
  ] },
  { id: "portal", rows: [
    { key: "brand_name", where: { block: "portal-brand", label: "brand.title" } },
    { key: "brand_contact", where: { block: "portal-brand", label: "brand.title" } },
    { key: "brand_accent", where: { block: "portal-brand", label: "brand.title" } },
  ] },
  { id: "ei", rows: [
    { key: "market_alternative_statement", where: { screen: "report", label: "rep.sec.ei" } },
    { key: "method_approval_reference", where: { screen: "report", label: "rep.sec.ei" } },
    { key: "review_statement", where: { screen: "report", label: "rep.sec.ei" } },
  ] },
];

// what a saved change reads again: the settings, the inbox's thresholds, the shadow's verdict against
// the agreed level, the business case's horizon and the redispatch report's compensation
const AFFECTED = ["/settings", "/inbox", "/operation/shadow", "/business", "/redispatch"];

/** The digits a value is shown with: those of its control's step. */
function digitsOf(spec: FieldSpec): number {
  const step = spec.step ?? 1;
  return step >= 1 ? 0 : Math.min(3, Math.ceil(-Math.log10(step) - 1e-9));
}

/** A bound is printed whole when it is whole, else with its step's digits. */
function boundDigits(v: number, spec: FieldSpec): number {
  return Number.isInteger(v) ? 0 : digitsOf(spec);
}

/** A draft as the value it stands for: a number, a time or a choice; null for an empty nullable number. */
function parse(spec: FieldSpec, draft: string): { ok: boolean; value: unknown } {
  if (spec.kind === "number" || spec.kind === "integer") {
    if (draft.trim() === "") return { ok: spec.nullable, value: null };
    const n = Number(draft);
    const ok = Number.isFinite(n) && (spec.kind !== "integer" || Number.isInteger(n))
      && (spec.min === undefined || n >= spec.min) && (spec.max === undefined || n <= spec.max);
    return { ok, value: n };
  }
  if (spec.kind === "time") return { ok: new RegExp(spec.pattern ?? "^\\d{2}:\\d{2}$").test(draft), value: draft };
  if (spec.kind === "choice") return { ok: (spec.options ?? []).includes(draft), value: draft };
  return { ok: true, value: draft };
}

// above this, a bound is a guard against nonsense rather than a limit worth reading (the redispatch
// compensation's), so the control does not print it
const BOUND_SHOWN = 1e6;

const draftOf = (v: unknown) => (v === null || v === undefined ? "" : String(v));

/** Operation's Settings section, read from GET /settings. */
export function SettingsSection() {
  const t = useT();
  const q = useApi<SettingsReply>("/settings");
  if (q.isLoading) return <p className="caption-muted">{t("set.loading")}</p>;
  if (!q.data?.schema) return <p className="caption-muted">{t("set.unavailable")}</p>;
  return <SettingsView reply={q.data} />;
}

/**
 * Every DSO setting, grouped by what it governs, with its value, unit, meaning and, when the value is
 * not the default, the default. The six with no other screen are changed here, a group at a time; the
 * rest keep their own control and link to it.
 */
export function SettingsView({ reply }: { reply: SettingsReply }) {
  const t = useT();
  const lang = useStore((s) => s.lang);
  const ch = reply.changed;
  return (
    <div className="stack settings">
      <p className="caption-muted" data-testid="settings-changed">
        {ch ? (ch.role ? t("set.changedRole", { when: dateTime(lang, ch.at), role: t(`auth.role.${ch.role}`) })
          : t("set.changed", { when: dateTime(lang, ch.at) }))
          : t("set.neverChanged")}
      </p>
      <div className="settings-groups">
        {GROUPS.map((g) => <SettingsGroup key={g.id} group={g} reply={reply} />)}
      </div>
      <RoleNote action="settings" />
    </div>
  );
}

function SettingsGroup({ group, reply }: { group: Group; reply: SettingsReply }) {
  const t = useT();
  const f = useFmt();
  const client = useQueryClient();
  const canSet = useCan("settings");
  const editable = group.rows.filter((r) => r.edit && reply.schema[r.key]);
  const current = reply.settings;
  const initial = useMemo(() => Object.fromEntries(editable.map((r) => [r.key, draftOf(current[r.key])])),
    // the drafts start again whenever a saved value comes back
    [editable.map((r) => draftOf(current[r.key])).join("|")]);
  const [drafts, setDrafts] = useState<Record<string, string>>(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setDrafts(initial), [initial]);

  const parsed = Object.fromEntries(editable.map((r) => [r.key, parse(reply.schema[r.key], drafts[r.key] ?? "")]));
  const valid = editable.every((r) => parsed[r.key].ok);
  // only what changed is sent; an emptied threshold is sent as null, which sets it back to the agreed level
  const change: Record<string, unknown> = {};
  for (const r of editable) {
    const p = parsed[r.key];
    if (p.ok && p.value !== (current[r.key] ?? null)) change[r.key] = p.value;
  }
  const dirty = Object.keys(change).length > 0;

  const save = async () => {
    setSaving(true);
    setError(null);
    const r = await apiWrite("/settings", change, "PUT").catch(() => null);
    setSaving(false);
    if (!r?.ok) {
      setError(r ? await responseRefusal(t, r) : t("ui.offline"));
      return;
    }
    await client.invalidateQueries({ predicate: (q) => AFFECTED.some((p) => String(q.queryKey[0] ?? "").startsWith(p)) });
    toast.info(t("set.saved", { group: t(`set.group.${group.id}`) }));
  };

  const affects = [
    ...(["auto_curtailment_mean_pct", "auto_curtailment_p90_pct", "auto_ev_unserved_pct"].some((k) => k in change) ? ["set.affects.thresholds"] : []),
    ...("agreed_level_pct" in change ? ["set.affects.agreed"] : []),
  ];

  return (
    <section className="block settings-group" aria-labelledby={`set-${group.id}`}>
      <div className="block-head"><h3 id={`set-${group.id}`}>{t(`set.group.${group.id}`)}</h3></div>
      <p className="caption">{t(`set.group.${group.id}.caption`)}</p>
      <dl className="settings-list">
        {group.rows.filter((r) => reply.schema[r.key]).map((r) => (
          <SettingRow key={r.key} row={r} spec={reply.schema[r.key]} value={current[r.key]} settings={current}
            draft={drafts[r.key] ?? ""} invalid={!!r.edit && !parsed[r.key]?.ok} disabled={saving || !canSet.ok} reason={canSet.reason}
            onDraft={(v) => setDrafts((d) => ({ ...d, [r.key]: v }))} t={t} f={f} />
        ))}
      </dl>
      {affects.map((k) => <p key={k} className="caption settings-affects" role="status">{t(k)}</p>)}
      {error && <p className="decision-error" role="alert"><Icon name="alert" size={15} />{error}</p>}
      {editable.length > 0 && (
        <div className="report-controls">
          <button className="btn small" disabled={saving || !dirty || !valid || !canSet.ok} title={canSet.reason} onClick={() => void save()}>
            {t("set.save", { group: t(`set.group.${group.id}`).toLowerCase() })}
          </button>
          {dirty && <button className="btn ghost small" disabled={saving} onClick={() => { setDrafts(initial); setError(null); }}>{t("set.undo")}</button>}
        </div>
      )}
    </section>
  );
}

function SettingRow({ row, spec, value, settings, draft, invalid, disabled, reason, onDraft, t, f }: {
  row: Row; spec: FieldSpec; value: any; settings: Record<string, any>; draft: string; invalid: boolean; disabled: boolean;
  reason?: string; onDraft: (v: string) => void; t: TFn; f: ReturnType<typeof useFmt>;
}) {
  const lang = useStore((s) => s.lang);
  const id = `set-f-${row.key}`;
  const name = t(`set.f.${row.key}`);
  const shown = (v: unknown) => show(row.key, spec, v, settings, t, f, lang);
  const differs = spec.kind !== "object" && JSON.stringify(value ?? null) !== JSON.stringify(spec.default ?? null);
  return (
    <div className="setting-row">
      <dt>
        <label htmlFor={row.edit ? id : undefined}>{row.term ? <Term id={row.term}>{name}</Term> : name}</label>
        <span className="caption-muted setting-meaning">{t(`set.f.${row.key}.means`)}</span>
        {differs && <span className="caption-muted setting-default">{t("set.default", { value: shown(spec.default) })}</span>}
      </dt>
      <dd>
        {row.edit
          ? <Control id={id} spec={spec} draft={draft} invalid={invalid} disabled={disabled} reason={reason} onDraft={onDraft} t={t} f={f} />
          : <span className="setting-value">{shown(value)}</span>}
        {row.where && <WhereLink where={row.where} t={t} />}
      </dd>
    </div>
  );
}

function Control({ id, spec, draft, invalid, disabled, reason, onDraft, t, f }: {
  id: string; spec: FieldSpec; draft: string; invalid: boolean; disabled: boolean; reason?: string; onDraft: (v: string) => void; t: TFn;
  f: ReturnType<typeof useFmt>;
}) {
  const common = { id, disabled, title: reason, "aria-invalid": invalid || undefined } as const;
  let input: ReactNode;
  if (spec.kind === "choice") {
    input = (
      <select {...common} value={draft} onChange={(e) => onDraft(e.target.value)}>
        {(spec.options ?? []).map((o) => <option key={o} value={o}>{t(`set.opt.${o}`)}</option>)}
      </select>
    );
  } else if (spec.kind === "time") {
    input = <input {...common} type="time" step={60} required value={draft} onChange={(e) => onDraft(e.target.value)} />;
  } else {
    input = (
      <input {...common} type="number" inputMode={spec.kind === "integer" ? "numeric" : "decimal"}
        min={spec.min} max={spec.max} step={spec.step} value={draft}
        placeholder={spec.nullable ? t("set.followsAgreed") : undefined} onChange={(e) => onDraft(e.target.value)} />
    );
  }
  return (
    <span className="field-inline setting-control">
      {input}
      {spec.unit && <span>{unitLabel(spec.unit, t)}</span>}
      {/* the bounds the API validates; an upper bound that is only a guard against nonsense is left unsaid */}
      {spec.min !== undefined && spec.max !== undefined && spec.max < BOUND_SHOWN && (
        <span className="caption-muted">{t("set.range", { min: f(spec.min, boundDigits(spec.min, spec)), max: f(spec.max, boundDigits(spec.max, spec)) })}</span>
      )}
    </span>
  );
}


function unitLabel(unit: string, t: TFn): string {
  const key = `set.unit.${unit === "%" ? "pct" : unit}`;
  const s = t(key);
  return s === key ? unit : s;
}

/** A value as the row shows it. */
function show(key: string, spec: FieldSpec, v: unknown, settings: Record<string, any>, t: TFn,
  f: ReturnType<typeof useFmt>, lang: string): string {
  if (key === "rule_signature") {
    const sig = v as { signed_by: string; valid_until: string } | null;
    return sig ? t("set.sig.by", { name: sig.signed_by, until: dateTime(lang, sig.valid_until, false) }) : t("set.sig.none");
  }
  if (key === "auto_decisions") return t(v ? "inbox.auto.on" : "inbox.auto.off");
  if (key === "allocation_policy") return t(`pol.${String(v)}`);
  if (spec.kind === "choice") return t(`set.opt.${String(v)}`);
  if (spec.kind === "number" || spec.kind === "integer") {
    if (v === null || v === undefined) {
      return spec.nullable ? t("set.followsAgreedValue", { value: f(settings.agreed_level_pct, 1) }) : "–";
    }
    return `${f(v as number, digitsOf(spec))} ${spec.unit ? unitLabel(spec.unit, t) : ""}`.trim();
  }
  if (spec.kind === "text" || spec.kind === "time") {
    const s = String(v ?? "");
    if (s) return s;
    return key.startsWith("brand_") ? t(key === "brand_contact" ? "set.notGiven" : "set.productOwn") : t("set.notStated");
  }
  return v === null || v === undefined ? "–" : String(v);
}

function WhereLink({ where, t }: { where: Where; t: TFn }) {
  if ("screen" in where) {
    return <a className="setting-where" href={`#/${where.screen}`}>{t("set.changeIn", { where: `${t(`tab.${where.screen}`)} › ${t(where.label)}` })}</a>;
  }
  const go = () => {
    const el = document.getElementById(where.block);
    if (el) scrollToInScroller(el, 72);
  };
  return <button type="button" className="link-btn setting-where" onClick={go}>{t("set.changeIn", { where: t(where.label) })}</button>;
}
