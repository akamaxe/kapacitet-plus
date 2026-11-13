import { expect, test } from "@playwright/test";

// The frame of the workbench on a fresh clone: the bar with the logo and the menus, and the
// status bar. The menus fold into a sheet when they do not fit, measured in each language. At
// every width either the menus or the menu button show, never both and never neither, and
// nothing spills out of the bar.
const WIDTHS = [1600, 1366, 1100, 800, 390];
const LANGS = ["en", "sv"];

for (const lang of LANGS) {
  for (const width of WIDTHS) {
    test(`the bar in ${lang} at ${width} px`, async ({ page }) => {
      await page.addInitScript((l) => window.localStorage.setItem("kap.lang", l), lang);
      await page.setViewportSize({ width, height: 800 });
      // inside a project, where the bar has all six menus
      await page.goto("/#/compare");
      const bar = page.locator("header.topbar");
      await expect(bar).toBeVisible();
      await expect(page.locator("html")).toHaveAttribute("lang", lang);

      const menus = page.getByRole("menubar");
      const button = page.locator("button.menu-btn");
      // the bar measures the menus' names, so it settles once the web font has loaded
      await page.evaluate(() => document.fonts.ready.then(() => undefined));
      await expect.poll(async () => (await menus.isVisible()) !== (await button.isVisible()), { timeout: 3000 }).toBe(true);
      const menusShown = await menus.isVisible();
      const buttonShown = await button.isVisible();
      expect(menusShown !== buttonShown, `menus ${menusShown}, button ${buttonShown}`).toBe(true);
      if (width <= 390) expect(buttonShown).toBe(true);
      if (width >= 1100) expect(menusShown).toBe(true);

      for (const part of [bar, page.locator("footer.statusbar")]) {
        expect(await part.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(1);
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);
    });
  }
}

test("the logo is the brand kit's drawing, and leads to the start window", async ({ page, request }) => {
  await page.goto("/#/compare");
  const logo = page.locator("header.topbar a.brand img.logo");
  await expect(logo).toHaveAttribute("alt", "Kapacitet+");
  await expect(page.locator("header.topbar a.brand")).toHaveAttribute("href", "#/start");
  const src = await logo.getAttribute("src");
  const file = await request.get(src!);
  expect(file.ok()).toBeTruthy();
  expect(file.headers()["content-type"]).toContain("image/svg+xml");
  // it has loaded and takes its place: a broken image would have no size of its own
  await expect.poll(() => logo.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
});

test("the File menu's project file is chosen in a picker that is not a field of the page", async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 800 });
  await page.goto("/");
  // the page itself has one file field at most (the setup page's, where a copy takes its data
  // from the browser): the menu's own is made when it is used
  const before = await page.locator('input[type="file"]').count();
  await page.getByRole("menubar").getByRole("menuitem", { name: "File" }).click();
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("menu").getByRole("menuitem", { name: "Import project file…" }).click();
  expect((await chooser).isMultiple()).toBe(false);
  expect(await page.locator('input[type="file"]').count()).toBe(before);
});

test("the menus open and are walked from the keyboard", async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 800 });
  await page.goto("/");
  const names = page.getByRole("menubar").getByRole("menuitem");
  // the start window has the menus that act on projects and on the program
  await expect(names).toHaveText(["File", "View", "Tools", "Help"]);
  await names.first().focus();
  await page.keyboard.press("ArrowDown");
  const menu = page.getByRole("menu");
  await expect(menu).toHaveAttribute("aria-label", "File");
  await expect(menu.getByRole("menuitem", { name: "New project…" })).toBeFocused();
  await page.keyboard.press("ArrowRight");
  await expect(menu).toHaveAttribute("aria-label", "View");
  await expect(menu.getByRole("menuitemradio", { name: "Light" })).toHaveAttribute("aria-checked", "true");
  // a digit under an open menu is the menu's, not a screen's
  await page.keyboard.press("3");
  await expect(page).toHaveURL(/#\/start$|\/$/);
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await expect(names.nth(1)).toBeFocused();
});

test("inside a project with nothing computed, the studies are listed and cannot be opened", async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 800 });
  await page.goto("/#/compare");
  const names = page.getByRole("menubar").getByRole("menuitem");
  await expect(names).toHaveText(["File", "View", "Study", "Results", "Tools", "Help"]);
  await names.nth(2).click();
  const study = page.getByRole("menu");
  for (const screen of ["Static vs envelopes", "Connection request", "Operation", "2030 scenarios"]) {
    await expect(study.getByRole("menuitem", { name: new RegExp(`^${screen}`) })).toHaveAttribute("aria-disabled", "true");
  }
  // what is computed, and by which command, is said where the studies are
  await study.getByRole("menuitem", { name: "Computation status…" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "Computation status" })).toBeVisible();
  await expect(dialog.getByText("Nothing has been computed for this project yet.")).toBeVisible();
  await expect(dialog.getByText("python -m kapacitet compute", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(names.nth(2)).toBeFocused();
});

test("the status bar says the server answers, and changes the language", async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 800 });
  await page.goto("/");
  const foot = page.locator("footer.statusbar");
  await expect(foot.getByText("Connected")).toBeVisible();
  await expect(foot.getByText(/^v\d+\.\d+\.\d+/)).toBeVisible();
  await foot.getByRole("button", { name: "SV" }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "sv");
  await expect(foot.getByText("Ansluten")).toBeVisible();
  await expect(page.getByRole("menubar").getByRole("menuitem")).toHaveText(["Arkiv", "Visa", "Verktyg", "Hjälp"]);
});

test("a narrow window gets the same menus as a sheet", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto("/#/compare");
  await page.locator("button.menu-btn").click();
  const sheet = page.getByRole("dialog");
  await expect(sheet.getByRole("heading", { level: 2 })).toHaveText(["File", "View", "Study", "Results", "Tools", "Help"]);
  await sheet.getByRole("button", { name: "About Kapacitet+…" }).click();
  await expect(page.getByRole("dialog").getByText("MIT, the code")).toBeVisible();
});

test("a browser can install it with a window of its own", async ({ page, request }) => {
  const manifest = await request.get("/site.webmanifest");
  expect(manifest.ok()).toBeTruthy();
  expect(manifest.headers()["content-type"]).toContain("application/manifest+json");
  const doc = await manifest.json();
  expect(doc.display).toBe("standalone");
  for (const icon of doc.icons) {
    const file = await request.get(icon.src);
    expect(file.ok(), icon.src).toBeTruthy();
    expect(file.headers()["content-type"]).toContain("image/png");
  }
  await page.goto("/");
  await expect(page.locator('link[rel="manifest"]')).toHaveAttribute("href", "/site.webmanifest");
  // Chrome's own verdict on the page. A test's browser is a private window, which Chrome never
  // installs from: that one objection is the browser's, not the page's.
  const cdp = await page.context().newCDPSession(page);
  const { installabilityErrors } = await cdp.send("Page.getInstallabilityErrors");
  expect(installabilityErrors.map((e) => e.errorId).filter((id) => id !== "in-incognito")).toEqual([]);
  const { errors } = await cdp.send("Page.getAppManifest");
  expect(errors).toEqual([]);
});
