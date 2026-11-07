/**
 * The DSO's brand on the applicant's portal, set where the DSO handles its applications: the name,
 * how to reach its customer service, the accent and the logo (kapacitet/brand.py).
 *
 * The three texts are DSO settings (PUT /settings), sealed with every change; the logo goes to
 * PUT /settings/brand-logo as base64, where the server checks it chunk by chunk, drops everything
 * but the image and seals its digest. The server is the one judge of the accent's contrast: a
 * refusal comes back with its own code and is shown as it says.
 */
import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { BrandMark, PRODUCT, useBrand, type Brand } from "../apply/Brand";
import { Icon } from "./Icon";
import { RoleNote } from "./RoleNote";
import { toast } from "./Toast";
import { apiWrite, useApi } from "../lib/api";
import { useCan } from "../lib/auth";
import { responseRefusal } from "../lib/errors";
import { useT } from "../lib/i18n";

const ACCENT = /^#[0-9a-fA-F]{6}$/;

/** A file's bytes as base64, read in the browser. */
function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export function PortalBrand() {
  const t = useT();
  const client = useQueryClient();
  const settings = useApi<any>("/settings");
  const brand = useBrand().data;
  const S = settings.data?.settings;
  const canSet = useCan("settings");
  const [name, setName] = useState("");
  const [contact, setContact] = useState("");
  const [accent, setAccent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!S) return;
    setName(S.brand_name ?? "");
    setContact(S.brand_contact ?? "");
    setAccent(S.brand_accent ?? "");
  }, [S]);
  if (!S) return null;

  const changed = name !== (S.brand_name ?? "") || contact !== (S.brand_contact ?? "") || accent !== (S.brand_accent ?? "");
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ["/settings"] });
    await client.invalidateQueries({ queryKey: ["/apply/brand"] });
  };
  const send = async (path: string, body: unknown, method: "PUT" | "DELETE", done: string) => {
    setBusy(true);
    setError(null);
    const r = await apiWrite(path, body, method).catch(() => null);
    setBusy(false);
    if (!r) return setError(t("ui.offline"));
    if (!r.ok) return setError(await responseRefusal(t, r));
    await refresh();
    toast.info(t(done));
  };
  const save = () => send("/settings", { brand_name: name, brand_contact: contact, brand_accent: accent }, "PUT", "brand.saved");
  const upload = async (e: ChangeEvent<HTMLInputElement>) => {
    const picked = e.target.files?.[0];
    e.target.value = "";
    if (!picked) return;
    const data = await readBase64(picked).catch(() => null);
    if (data === null) return setError(t("brand.readFail"));
    await send("/settings/brand-logo", { png_base64: data }, "PUT", "brand.uploaded");
  };

  // the preview is the portal's bar as it will read: the name typed here, the logo already kept
  const preview: Brand = { name: name.trim() || PRODUCT, contact, accent: ACCENT.test(accent) ? accent : null,
    logo_url: brand?.logo_url ?? null, logo_sha256: brand?.logo_sha256 ?? null };
  const hasLogo = !!brand?.logo_url;
  return (
    <section className="block stack" aria-labelledby="portal-brand">
      <div className="block-head"><h3 id="portal-brand">{t("brand.title")}</h3></div>
      <p className="caption">{t("brand.lead", { product: PRODUCT })}</p>
      <div className="portal-bar is-branded brand-preview" aria-label={t("brand.preview")} role="img"
        style={preview.accent ? { borderBottomColor: preview.accent } : undefined}>
        <BrandMark brand={preview} />
      </div>
      <div className="brand-fields">
        <div className="field decision-note">
          <label htmlFor="brand-name">{t("brand.name")}</label>
          <input id="brand-name" maxLength={80} value={name} placeholder={PRODUCT} autoComplete="organization" onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field decision-note">
          <label htmlFor="brand-accent">{t("brand.accent")}</label>
          <span className="brand-accent">
            <input id="brand-accent" maxLength={7} value={accent} placeholder="#RRGGBB" spellCheck={false} aria-describedby="brand-accent-hint"
              onChange={(e) => setAccent(e.target.value.trim())} />
            <span className="brand-swatch" aria-hidden="true" style={ACCENT.test(accent) ? { background: accent } : undefined} />
          </span>
          <small id="brand-accent-hint" className="caption-muted">{t("brand.accentHint")}</small>
        </div>
        <div className="field decision-note brand-contact">
          <label htmlFor="brand-contact">{t("brand.contact")}</label>
          <textarea id="brand-contact" rows={2} maxLength={200} value={contact} onChange={(e) => setContact(e.target.value)} />
        </div>
      </div>
      <div className="answer-actions">
        <button className="btn ghost small" disabled={!changed || busy || !canSet.ok} title={canSet.reason} onClick={() => void save()}>{t("brand.save")}</button>
        <input ref={file} id="brand-logo" className="sr-only" type="file" accept="image/png" tabIndex={-1} onChange={(e) => void upload(e)} />
        <button className="btn ghost small" disabled={busy || !canSet.ok} title={canSet.reason} aria-describedby="brand-logo-hint" onClick={() => file.current?.click()}>
          {t(hasLogo ? "brand.replace" : "brand.upload")}
        </button>
        {hasLogo && (
          <button className="btn ghost small" disabled={busy || !canSet.ok} title={canSet.reason}
            onClick={() => void send("/settings/brand-logo", {}, "DELETE", "brand.removed")}>{t("brand.remove")}</button>
        )}
      </div>
      <p id="brand-logo-hint" className="caption-muted">{t("brand.logoHint")}</p>
      {error && <p className="decision-error" role="alert"><Icon name="alert" size={15} />{error}</p>}
      <RoleNote action="settings" />
    </section>
  );
}
