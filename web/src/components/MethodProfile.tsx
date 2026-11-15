/**
 * The DSO's static method as a profile (GET/PUT /api/v1/method-profile, task 2.5): the solar output
 * of the summer snapshot, its household load, and the DSO's own limits. The default profile is
 * today's static study; a saved one changes the static figures at the next computation of the
 * capacity, which this says, with the command. The fields' limits come from the server's model.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { CommandLine } from "./bits";
import { Icon } from "./Icon";
import { RoleNote } from "./RoleNote";
import { toast } from "./Toast";
import { apiWrite, useApi } from "../lib/api";
import { useCan } from "../lib/auth";
import { responseRefusal } from "../lib/errors";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";
import { log } from "../lib/log";

type Rule = "min_daytime_summer" | "no_load" | "fixed_per_customer";
interface Profile {
  pv_factor: number;
  load_rule: Rule;
  min_load_factor: number;
  fixed_load_kw: number | null;
  v_rise_limit_pct: number | null;
  max_loading_pct: number;
  source: string;
}
interface Doc {
  profile: Profile | null;
  default: Profile;
  in_use: Record<string, number | string>;
}
const RULES: Rule[] = ["min_daytime_summer", "no_load", "fixed_per_customer"];

export function MethodProfile() {
  const t = useT();
  const f = useFmt();
  const qc = useQueryClient();
  const can = useCan("method_profile");
  const doc = useApi<Doc>("/method-profile");
  const [form, setForm] = useState<Profile | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (doc.data && !form) setForm(doc.data.profile ?? doc.data.default);
  }, [doc.data, form]);
  if (!doc.data || !form) return null;
  const set = <K extends keyof Profile>(k: K, v: Profile[K]) => setForm({ ...form, [k]: v });
  const num = (v: string) => (v.trim() === "" ? null : Number(v.replace(",", ".")));

  const save = async () => {
    setBusy(true);
    try {
      const r = await apiWrite("/method-profile", form, "PUT");
      if (!r.ok) return toast.error(await responseRefusal(t, r));
      const out = await r.json();
      setSaved(out.command);
      log.ok(t("mp.saved"));
      await qc.invalidateQueries({ queryKey: ["/method-profile"] });
    } catch {
      toast.error(t("ui.offline"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="block stack method-profile" aria-labelledby="mp-title">
      <div className="block-head"><h3 id="mp-title">{t("mp.title")}</h3></div>
      <p className="caption">{t("mp.lead")}</p>
      <div className="mp-grid">
        <label>
          <span>{t("mp.pv")}</span>
          <input type="number" step="0.05" min={0.1} max={1.5} value={form.pv_factor} disabled={!can.ok}
            onChange={(e) => set("pv_factor", Number(e.currentTarget.value))} />
        </label>
        <label>
          <span>{t("mp.rule")}</span>
          <select value={form.load_rule} disabled={!can.ok} onChange={(e) => set("load_rule", e.currentTarget.value as Rule)}>
            {RULES.map((r) => <option key={r} value={r}>{t(`mp.rule.${r}`)}</option>)}
          </select>
        </label>
        {form.load_rule === "min_daytime_summer" && (
          <label>
            <span>{t("mp.minLoad")}</span>
            <input type="number" step="0.1" min={0} max={3} value={form.min_load_factor} disabled={!can.ok}
              onChange={(e) => set("min_load_factor", Number(e.currentTarget.value))} />
          </label>
        )}
        {form.load_rule === "fixed_per_customer" && (
          <label>
            <span>{t("mp.fixed")}</span>
            <input type="number" step="0.05" min={0} max={20} value={form.fixed_load_kw ?? ""} disabled={!can.ok}
              onChange={(e) => set("fixed_load_kw", num(e.currentTarget.value))} />
          </label>
        )}
        <label>
          <span>{t("mp.rise")}</span>
          <input type="number" step="0.5" min={0} max={10} value={form.v_rise_limit_pct ?? ""} placeholder={t("mp.none")} disabled={!can.ok}
            onChange={(e) => set("v_rise_limit_pct", num(e.currentTarget.value))} />
        </label>
        <label>
          <span>{t("mp.loading")}</span>
          <input type="number" step="1" min={50} max={100} value={form.max_loading_pct} disabled={!can.ok}
            onChange={(e) => set("max_loading_pct", Number(e.currentTarget.value))} />
        </label>
        <label className="mp-source">
          <span>{t("mp.source")}</span>
          <input value={form.source} maxLength={200} placeholder={t("mp.sourcePlaceholder")} disabled={!can.ok}
            onChange={(e) => set("source", e.currentTarget.value)} />
        </label>
      </div>
      <p className="caption-muted">{t("mp.inUse", { pv: f(Number(doc.data.in_use.static_pv_factor), 2),
        rule: t(`mp.rule.${String(doc.data.in_use.static_load_rule)}`) })}</p>
      <div className="answer-actions">
        <button className="btn" disabled={!can.ok || busy} title={can.reason} onClick={() => void save()}>{t("mp.save")}</button>
        <button className="btn ghost" disabled={!can.ok || busy} onClick={() => setForm(doc.data!.default)}>{t("mp.reset")}</button>
      </div>
      {saved && (
        <div className="stack">
          <p className="caption"><Icon name="check" size={14} /> {t("mp.later")}</p>
          <CommandLine command={saved} />
        </div>
      )}
      <RoleNote action="method_profile" />
    </section>
  );
}
