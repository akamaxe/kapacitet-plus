import { expect, test } from "@playwright/test";

// A network where every house fits (the technical review's twelve houses on short feeders: static,
// hourly and envelopes all 94 kW, the transformer at 18.9 %). The screens were written for the
// reference network, where the transformer binds, and said "Fail" next to 0 h, named the
// transformer as the binding limit and priced deferring a swap nobody needs (H11, 14-11-2026).
//
// It needs a server open on such a network, named by E2E_ALL_FIT_URL, for example a copy of the
// data with the twelve-house project open:
//   KAPACITET_DATA_DIR=<copy>/datasets/<id> KAPACITET_HOME=<copy> KAPACITET_RAW_DIR=<copy>/raw
//   KAPACITET_DATASET=<id> KAPACITET_PORT=8172 python -m kapacitet serve
// Without it the test is skipped.
const BASE = process.env.E2E_ALL_FIT_URL;

test.skip(!BASE, "set E2E_ALL_FIT_URL to a server open on a network where every house fits");

test("a network where everything fits is not said to fail, bind or need a reinforcement", async ({ page }) => {
  await page.goto(`${BASE}/#/compare`);
  await expect(page.locator("main h1").first()).toBeVisible({ timeout: 60_000 });
  const verify = page.locator("#verify");
  await expect(verify).toBeVisible();
  const unc = verify.locator("li, tr").filter({ hasText: "without control" }).first();
  await expect(unc).toContainText("0");
  await expect(unc).not.toContainText("Fail");
  await expect(verify).toContainText("Without control no limit is exceeded");
  const main = page.locator("main");
  await expect(main).not.toContainText("The binding limit is the transformer");
  await expect(main).not.toContainText("Deferring the transformer swap");

  await page.goto(`${BASE}/#/request?cid=0`);
  await expect(page.locator("main h1").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.locator("main")).toContainText("already allows all");
  await expect(page.locator("main")).not.toContainText("full for that method");
});
