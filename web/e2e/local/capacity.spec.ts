import { expect, test } from "@playwright/test";

// The product as an installation ships it, every part off (src/kapacitet/features.py), on this
// machine's computed data: four screens, none of which names the envelopes, a screen that is off
// opening the capacity screen, and a request answered by the hourly study. It needs a server started
// without KAPACITET_FEATURES, on a copy of the data (an evaluation is saved in its requests/):
//   E2E_CAPACITY_URL=http://127.0.0.1:8171 npm run test:e2e:local -- capacity
const BASE = process.env.E2E_CAPACITY_URL;
test.skip(!BASE, "E2E_CAPACITY_URL names a server with every part off");

function watch(page: import("@playwright/test").Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  return errors;
}

for (const screen of ["twin", "compare", "request", "method"]) {
  test(`${screen} opens without naming the envelopes`, async ({ page }) => {
    const errors = watch(page);
    await page.goto(`${BASE}/#/${screen}`);
    await expect(page.locator("main h1").first()).toBeVisible({ timeout: 60_000 });
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(1_500);
    expect(await page.locator("body").innerText()).not.toMatch(/envelope|curtail/i);
    expect(errors).toEqual([]);
  });
}

test("a screen that is off opens the capacity screen, and the menus hold four screens", async ({ page }) => {
  await page.goto(`${BASE}/#/operation`);
  await expect(page.locator("main h1").first()).toBeVisible({ timeout: 60_000 });
  await expect(page).toHaveURL(/#\/compare/);
  await page.getByRole("menuitem", { name: "View" }).click();
  const items = await page.getByRole("menu").getByRole("menuitem").allInnerTexts();
  expect(items.join(" ")).not.toMatch(/Operation|Annual report|Sweden|2030/);
});

test("a request is answered by the static and the hourly study, with no battery and no envelope", async ({ page }) => {
  const errors = watch(page);
  await page.goto(`${BASE}/#/request?cid=84`);
  await expect(page.locator(".house-title")).toBeVisible({ timeout: 60_000 });
  await expect(page.locator("#bat")).toHaveCount(0);
  await page.getByRole("button", { name: "Evaluate" }).click();
  const answer = page.locator(".answer");
  await expect(answer).toBeVisible({ timeout: 90_000 });
  await expect(answer).toContainText("Static study");
  await expect(answer).toContainText("Hourly study");
  const firm = page.locator(".decision-actions button").first();
  await expect(firm).toHaveText(/^Connect firm/);
  expect(await page.locator("body").innerText()).not.toMatch(/envelope|curtail/i);
  expect(errors).toEqual([]);
});

test("the start window and every step of the tour name no envelope", async ({ page }) => {
  await page.goto(`${BASE}/#/start`);
  await expect(page.getByRole("button", { name: "Open project", exact: true })).toBeVisible({ timeout: 60_000 });
  await page.waitForTimeout(1_000);
  expect(await page.locator("body").innerText()).not.toMatch(/envelope|curtail/i);
  await page.goto(`${BASE}/#/compare`);
  await page.getByRole("button", { name: "Take the tour" }).click();
  const tour = page.locator(".tour").first();
  for (let i = 0; i < 12; i++) {
    await expect(tour).toBeVisible();
    expect(await tour.innerText()).not.toMatch(/envelope|curtail/i);
    const done = tour.getByRole("button", { name: "Done" });
    if (await done.count()) break;
    await tour.getByRole("button", { name: "Next" }).click();
  }
});
