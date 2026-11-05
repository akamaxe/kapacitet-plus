import { expect, test } from "@playwright/test";

// The top bar folds its screens into the menu when they do not fit, measured in each language
// (Swedish runs longest). At every width, either the screens or the menu button show, never
// both and never neither, and nothing spills out of the bar.
const WIDTHS = [1600, 1366, 1280, 1100, 800];
const LANGS = ["en", "sv"];

for (const lang of LANGS) {
  for (const width of WIDTHS) {
    test(`top bar in ${lang} at ${width} px`, async ({ page }) => {
      await page.addInitScript((l) => window.localStorage.setItem("kap.lang", l), lang);
      await page.setViewportSize({ width, height: 800 });
      await page.goto("/");
      const bar = page.locator("header.topbar");
      await expect(bar).toBeVisible();
      await expect(page.locator("html")).toHaveAttribute("lang", lang);

      const nav = page.locator("nav.topnav");
      const menu = page.locator("button.menu-btn");
      // the bar measures its labels, so it settles once the web font has loaded
      await page.evaluate(() => document.fonts.ready.then(() => undefined));
      await expect.poll(async () => (await nav.isVisible()) !== (await menu.isVisible()), { timeout: 3000 }).toBe(true);
      const navShown = await nav.isVisible();
      const menuShown = await menu.isVisible();
      expect(navShown !== menuShown, `nav ${navShown}, menu ${menuShown}`).toBe(true);
      if (width <= 800) expect(menuShown).toBe(true);
      if (width >= 1600 && lang === "en") expect(navShown).toBe(true);

      const spill = await bar.evaluate((el) => el.scrollWidth - el.clientWidth);
      expect(spill).toBeLessThanOrEqual(1);
      if (navShown) {
        const navSpill = await nav.evaluate((el) => el.scrollWidth - el.clientWidth);
        expect(navSpill).toBeLessThanOrEqual(1);
      }
    });
  }
}
