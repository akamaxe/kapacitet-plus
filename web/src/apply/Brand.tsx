/**
 * Whose portal this is: the DSO's name, logo, contact and accent (GET /apply/brand, kapacitet/brand.py).
 *
 * The portal speaks under the DSO's brand when one is set, and names Kapacitet+ only small in its
 * footer; without one it is the product's own page, as before. The accent arrives from the API at
 * run time and is set as a CSS custom property (`--brand-accent`) on the portal's root, where
 * app.css lets it take the accent's place in the light theme only: the server holds it to 4.5:1
 * against white, which says nothing about a night ground.
 */
import type { CSSProperties, ReactNode } from "react";
import { Logo } from "../components/Logo";
import { useApi } from "../lib/api";
import { useT } from "../lib/i18n";

export interface Brand {
  name: string;
  contact: string;
  accent: string | null;
  logo_url: string | null;
  logo_sha256: string | null;
}

/** The product's name: the portal's name when the DSO set none. A label, not a figure. */
export const PRODUCT = "Kapacitet+";

export function useBrand() {
  return useApi<Brand>("/apply/brand");
}

/** Whether the DSO put its own name or logo on the portal. */
export function isBranded(b: Brand | null | undefined): b is Brand {
  return !!b && (b.name !== PRODUCT || b.logo_url !== null);
}

/** The portal root's attributes for the DSO's accent, when it set one. */
export function accentProps(b: Brand | null | undefined): { style?: CSSProperties; "data-accent"?: "" } {
  return b?.accent ? { style: { "--brand-accent": b.accent } as CSSProperties, "data-accent": "" } : {};
}

/** The mark in the header: the DSO's logo and name, or the product's own logo, which is its name
 * (null: the brand could not be read). The DSO's logo is decorative next to its name, so it
 * carries no text of its own. */
export function BrandMark({ brand, className = "brand" }: { brand: Brand | null | undefined; className?: string }) {
  // still loading: nothing yet, so a DSO's portal never opens under the product's name
  if (brand === undefined) return <span className={className} aria-hidden="true" />;
  if (!isBranded(brand)) {
    return <span className={className}><Logo tone="reversed" height={24} /></span>;
  }
  return (
    <span className={`${className} is-dso`}>
      {brand.logo_url && <img className="brand-logo" src={brand.logo_url} alt="" />}
      <span className="brand-name">{brand.name}</span>
    </span>
  );
}

/** The portal's top bar: the mark, and whatever tools sit at its end (the languages). */
export function PortalHeader({ brand, children }: { brand: Brand | null | undefined; children?: ReactNode }) {
  return (
    <header className={isBranded(brand) ? "portal-bar is-branded" : "portal-bar"}>
      <BrandMark brand={brand} />
      {children}
    </header>
  );
}

/** How to reach the DSO, under the form; nothing when it gave no contact. */
export function PortalContact({ brand }: { brand: Brand | null | undefined }) {
  const t = useT();
  if (!brand?.contact) return null;
  return (
    <section className="portal-step portal-contact" aria-labelledby="portal-contact">
      <h2 id="portal-contact">{t("apply.contact.title", { name: brand.name })}</h2>
      <p>{brand.contact}</p>
    </section>
  );
}

/** The product, named small at the foot of a branded portal. */
export function PortalFooter({ brand }: { brand: Brand | null | undefined }) {
  const t = useT();
  if (!isBranded(brand)) return null;
  return <footer className="portal-foot">{t("apply.foot", { product: PRODUCT })}</footer>;
}
