import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { PortalContact, PortalFooter, PortalHeader, accentProps, isBranded, type Brand } from "./Brand";

// The portal's header under the DSO's brand (GET /apply/brand): its logo and name, its contact under
// the form and the product only in the footer; without a brand, the product's own bar as before.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const product: Brand = { name: "Kapacitet+", contact: "", accent: null, logo_url: null, logo_sha256: null };
const dso: Brand = {
  name: "Elnät Söder AB", contact: "Kundtjänst 08-123 45 67\nvardagar 8–17", accent: "#7a1f2b",
  logo_url: "/api/v1/apply/brand/logo.png?v=0123456789abcdef", logo_sha256: "0123456789abcdef".repeat(4),
};

let root: Root | null = null;
let host: HTMLElement | null = null;

async function render(brand: Brand | null | undefined): Promise<HTMLElement> {
  host = document.createElement("div");
  document.body.appendChild(host);
  await act(async () => {
    root = createRoot(host as HTMLElement);
    root.render(
      <div className="portal" {...accentProps(brand)}>
        <PortalHeader brand={brand}><span className="tools">EN SV</span></PortalHeader>
        <PortalContact brand={brand} />
        <PortalFooter brand={brand} />
      </div>,
    );
  });
  return host;
}

afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  root = null;
  host = null;
});

describe("the portal's header", () => {
  it("is the product's own bar when the DSO set no brand", async () => {
    const el = await render(product);
    const bar = el.querySelector("header.portal-bar")!;
    expect(bar.classList.contains("is-branded")).toBe(false);
    expect(bar.querySelector(".brand-name")?.textContent).toBe("Kapacitet+");
    expect(bar.querySelector(".roundel")).not.toBeNull();
    expect(bar.querySelector("img")).toBeNull();
    expect(el.querySelector(".portal-foot")).toBeNull();
    expect(el.querySelector(".portal-contact")).toBeNull();
    expect(el.querySelector(".portal")?.hasAttribute("data-accent")).toBe(false);
  });

  it("carries the DSO's logo and name, its contact and the product only in the footer", async () => {
    const el = await render(dso);
    const bar = el.querySelector("header.portal-bar")!;
    expect(bar.classList.contains("is-branded")).toBe(true);
    expect(bar.querySelector(".brand-name")?.textContent).toBe("Elnät Söder AB");
    expect(bar.textContent).not.toContain("Kapacitet+");
    const logo = bar.querySelector("img")!;
    expect(logo.getAttribute("src")).toBe(dso.logo_url);
    // decorative beside the name: a screen reader hears the name once
    expect(logo.getAttribute("alt")).toBe("");
    expect(bar.querySelector(".tools")).not.toBeNull();
    expect(el.querySelector(".portal-contact p")?.textContent).toBe(dso.contact);
    expect(el.querySelector(".portal-foot")?.textContent).toContain("Kapacitet+");
  });

  it("sets the DSO's accent as a custom property from the API's value", async () => {
    const el = await render(dso);
    const portal = el.querySelector<HTMLElement>(".portal")!;
    expect(portal.hasAttribute("data-accent")).toBe(true);
    expect(portal.style.getPropertyValue("--brand-accent")).toBe("#7a1f2b");
  });

  it("a name alone is a brand, and nothing is shown while the brand loads", async () => {
    expect(isBranded({ ...product, name: "Elnät Norr" })).toBe(true);
    expect(isBranded({ ...product, logo_url: "/x.png" })).toBe(true);
    expect(isBranded(product)).toBe(false);
    const el = await render(undefined);
    expect(el.querySelector(".brand-name")).toBeNull();
    expect(el.querySelector("header")?.textContent).toBe("EN SV");
  });
});
