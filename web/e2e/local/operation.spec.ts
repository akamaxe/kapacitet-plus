import { expect, test } from "@playwright/test";

// The operation screen: one house's limits for a day and the file issued for its inverter, the summer
// replayed in the shadow, and a meter checked with the synthetic day.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("kap.tour", "1"));
});

test("operation shows a day's limits, the summer in shadow and a meter check", async ({ page, request }) => {
  const year = (await (await request.get("/api/v1/operation/index")).json()).year;
  await page.goto("/#/operation");
  await expect(page.getByRole("heading", { level: 1, name: "Each day's limits, issued the day before" })).toBeVisible({ timeout: 60_000 });
  const main = page.getByRole("main");

  // the inverter's file is previewed on screen without being issued
  await expect(main.locator("pre.payload")).toContainText("DERControlList");
  await expect(main.getByText(/not certified and no device is connected/)).toBeVisible();
  // issuing it is a write (a POST), sealed, and the page keeps the seal's receipt under the button
  const saved = page.waitForEvent("download");
  await main.getByRole("button", { name: "Issue the file (XML)" }).click();
  expect((await saved).suggestedFilename()).toMatch(/^kapacitet-dercontrols-\d+-\d{4}-\d{2}-\d{2}(-15min)?\.xml$/);
  await expect(main.locator(".seal-receipt").first()).toContainText(/Sealed in the evidence chain/);

  // the EV chargers' import limits: the window they were issued with and the one in the settings,
  // with the audit of the hours over the planning floor (the figures are the API's, not checked here)
  const imp = main.getByRole("region", { name: "Import limits for EV chargers" });
  await expect(imp).toBeVisible();
  await expect(imp.getByText("Window in the settings")).toBeVisible();
  await expect(imp.getByLabel(/Window, days either side/)).toBeVisible();

  // a day chosen is a day in the link
  await main.getByLabel("Day", { exact: true }).fill(`${year}-06-20`);
  await expect(page).toHaveURL(new RegExp(`date=${year}-06-20`));

  // the season's verdict, and the tests it rests on
  await expect(main.getByText(/ready to go live/i)).toBeVisible();
  await expect(main.getByRole("row", { name: /Agreements within their level/ })).toBeVisible();
  await expect(main.getByRole("row", { name: /Hours above a grid limit, season/ })).toBeVisible();

  // the synthetic day carries a deliberate breach, and the check lists it
  await main.getByRole("button", { name: "Try a synthetic day" }).click();
  await expect(main.getByText(/Readings every 5 minutes for/)).toBeVisible();
  await expect(main.getByRole("table", { name: /Readings above the tolerance/ })).toBeVisible();
  await page.screenshot({ path: "test-results/shots/operation.png" });
});

test("the operation screen is the fifth of eight, by key and in the menu", async ({ page }) => {
  await page.goto("/#/compare");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 60_000 });
  await page.keyboard.press("5");
  await expect(page).toHaveURL(/#\/operation/);
  await page.keyboard.press("8");
  await expect(page).toHaveURL(/#\/method/);
});
