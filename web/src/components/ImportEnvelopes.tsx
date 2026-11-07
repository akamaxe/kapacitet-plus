import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Status, Tile } from "./bits";
import { Term } from "./Term";
import { toast } from "./Toast";
import { RoleNote } from "./RoleNote";
import { apiWrite, useApi } from "../lib/api";
import { useCan } from "../lib/auth";
import { responseRefusal } from "../lib/errors";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";

/** The settings reply's `import_envelopes` block (api/operation.import_status). */
export interface ImportStatus {
  computed: boolean;
  /** the window the published import envelopes were issued with; null before the operation stage */
  window_days: number | null;
  setting_window_days: number;
  max_window_days: number;
  stale: boolean;
  actual_violation_hours?: number | null;
  exceedance?: {
    hours_over: number | null;
    customer_hours_over: number | null;
    worst_customer_kw: number | null;
    aggregate_hours_over: number | null;
    worst_aggregate_kw: number | null;
  } | null;
  command: string;
}

/** The import envelopes' window, read from GET /settings, next to the charging profile. */
export function ImportEnvelopes() {
  const settings = useApi<any>("/settings");
  const S: ImportStatus | undefined = settings.data?.import_envelopes;
  return S ? <ImportBlock status={S} /> : null;
}

/**
 * The EV chargers' import envelopes: the window they were issued with and the one the DSO's settings
 * hold now, the issue's audit of the hours the real import went past the planning floor, and the
 * hours above a grid limit. When the two windows differ the envelopes are stale, and the charging
 * profile is refused until the operation stage issues them again. The window is a DSO setting.
 */
export function ImportBlock({ status: S }: { status: ImportStatus }) {
  const t = useT();
  const f = useFmt();
  const client = useQueryClient();
  const canSet = useCan("settings");
  const [value, setValue] = useState(String(S.setting_window_days));
  const [saving, setSaving] = useState(false);
  useEffect(() => setValue(String(S.setting_window_days)), [S.setting_window_days]);

  const n = Number(value);
  const valid = value.trim() !== "" && Number.isInteger(n) && n >= 0 && n <= S.max_window_days;
  const changed = valid && n !== S.setting_window_days;
  const days = (d: number | null | undefined) => (d === null || d === undefined ? "–" : f(d));
  const ex = S.exceedance ?? null;

  const save = async () => {
    setSaving(true);
    const r = await apiWrite("/settings", { import_window_days: n }, "PUT").catch(() => null);
    setSaving(false);
    if (!r?.ok) return toast.error(r ? await responseRefusal(t, r) : t("ui.offline"));
    await client.invalidateQueries({ queryKey: ["/settings"] });
    toast.info(t("op.imp.saved", { days: f(n) }));
  };

  return (
    <section className="stack import-env" aria-labelledby="op-import">
      <div className="block-head">
        <h4 id="op-import">{t("op.imp.title")}</h4>
        {!S.computed
          ? <span className="status measured">{t("op.imp.notIssuedTag")}</span>
          : S.stale
            ? <Status kind="warning">{t("op.imp.staleTag")}</Status>
            : <Status kind="good">{t("op.imp.currentTag")}</Status>}
      </div>
      <p className="caption">{t("op.imp.caption")}</p>
      {S.stale && (
        <div className="stale-note" role="status">
          <span>{t("op.imp.stale", { issued: f(S.window_days), setting: f(S.setting_window_days) })} <code>{S.command}</code></span>
        </div>
      )}
      {!S.computed && (
        <p className="caption-muted">{t("op.imp.none")} <code>{S.command}</code></p>
      )}
      <div className="tiles cols-4">
        <Tile label={t("op.imp.issued")} value={days(S.window_days)} unit={t("op.imp.days")} />
        <Tile label={t("op.imp.setting")} value={days(S.setting_window_days)} unit={t("op.imp.days")} />
        {S.computed && (
          <Tile
            label={<Term id="planningFloor">{t("op.imp.over")}</Term>}
            value={ex ? f(ex.hours_over) : "–"}
            unit="h"
            note={ex ? t("op.imp.overNote", { houseHours: f(ex.customer_hours_over), kw: f(ex.worst_customer_kw, 2), agg: f(ex.aggregate_hours_over) }) : undefined}
          />
        )}
        {S.computed && (
          <Tile label={t("op.imp.violations")} value={f(S.actual_violation_hours)} unit="h"
            tone={S.actual_violation_hours ? "danger" : undefined} note={t("op.imp.violationsNote")} />
        )}
      </div>
      <div className="report-controls">
        <label className="field-inline">
          <span>{t("op.imp.window", { max: f(S.max_window_days) })}</span>
          <input type="number" inputMode="numeric" min={0} max={S.max_window_days} step={1} value={value}
            disabled={saving || !canSet.ok} title={canSet.reason} aria-invalid={!valid}
            onChange={(e) => setValue(e.target.value)} />
        </label>
        <button className="btn small" disabled={saving || !changed || !canSet.ok} title={canSet.reason} onClick={() => void save()}>
          {t("op.imp.save")}
        </button>
      </div>
      <RoleNote action="settings" />
    </section>
  );
}
