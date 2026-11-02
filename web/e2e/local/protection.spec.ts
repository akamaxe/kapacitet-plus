import { expect, test } from "@playwright/test";

// The network's protection check on the Method screen, and a single-phase charger's phase verdict
// on a request.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("kap.tour", "1"));
});

test("Method shows which feeders clear a far-end fault in time", async ({ page }) => {
  await page.goto("/#/method");
  const region = page.locator("section#protection");
  await expect(region.getByRole("heading", { name: /Protection: does every fault blow its fuse in time\?/ })).toBeVisible({ timeout: 60_000 });
  await expect(region.locator("table.protocol tr")).toHaveCount((await (await page.request.get("/api/v1/protection")).json()).data.feeders.length + 1);
  await expect(region.getByText(/of \d+ feeders clear a fault at their far end within 5 s/)).toBeVisible();
  await region.screenshot({ path: "test-results/shots/protection-method.png" });
});

test("a single-phase charger is tried on each phase", async ({ page }) => {
  const meta = await (await page.request.get("/api/v1/meta")).json();
  const cid = Number(meta.grid.customers) - 1;
  await page.goto(`/#/request?cid=${cid}`);
  await expect(page.getByRole("button", { name: "Evaluate connection" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("main").getByText(/Fault current at the service point/)).toBeVisible();
  await page.getByRole("group", { name: "EV charger" }).getByRole("button", { name: "3.7 kW" }).click();
  await page.getByRole("button", { name: "Evaluate connection" }).click();
  const phase = page.getByRole("main").locator(".verdict").filter({ hasText: "Phase balance" });
  await expect(phase).toBeVisible({ timeout: 90_000 });
  // a figure and its unit are held together by a no-break space (i18n/core.ts), hence \s
  await expect(phase).toContainText(/unbalance [\d.]+\s% on L\d to [\d.]+\s% on L\d \(limit 2\s%\)/);
  await page.getByRole("main").locator(".result").screenshot({ path: "test-results/shots/request-phases.png" });
});
