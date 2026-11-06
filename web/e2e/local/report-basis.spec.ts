import { expect, test } from "@playwright/test";

// The annual report under each basis and rule, the DSO's rule set from it, and its printed sheet.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("kap.tour", "1"));
});

test("the report reads the day-ahead basis and another rule, and prints", async ({ page }) => {
  await page.goto("/#/report");
  await expect(page.getByRole("heading", { level: 1, name: /The year's report, \d{4}/ })).toBeVisible({ timeout: 60_000 });

  await page.getByRole("group", { name: "Basis" }).getByRole("button", { name: "Issued the day before" }).click();
  await expect(page).toHaveURL(/basis=dayahead/);
  await expect(page.getByRole("row", { name: /Limited hours that needed it/ })).toBeVisible();
  // the binding rows count the hours a limit was needed, with the plan's own count beside them
  await expect(page.getByRole("row", { name: /the plan issued the day before limited/ }).first()).toBeVisible();
  // an audited, empirical bound: nowhere called a guarantee, and the pack cites no figure priced
  // on the perfect-foresight deployment
  await expect(page.getByText(/guarantee/i)).toHaveCount(0);
  await expect(page.getByText(/every issue audits/).first()).toBeVisible();
  const pack = page.locator("#ei");
  if (await pack.count()) await expect(pack).not.toContainText("kSEK");

  // on the perfect-foresight basis no day-ahead plan was issued, so no row names one
  await page.getByRole("group", { name: "Basis" }).getByRole("button", { name: "Perfect foresight" }).click();
  // perfect foresight is the default basis, so the address leaves it out (store.ts hashFor)
  await expect(page).not.toHaveURL(/basis=/);
  await expect(page.getByText(/the plan issued the day before limited/)).toHaveCount(0);
  await page.getByRole("group", { name: "Basis" }).getByRole("button", { name: "Issued the day before" }).click();

  await page.getByLabel("Sharing rule").selectOption("prorata");
  await expect(page).toHaveURL(/policy=prorata/);
  await expect(page.getByText(/You are reading the filing as it would be under/)).toBeVisible();

  await page.emulateMedia({ media: "print" });
  const sheet = page.locator("article.print-record.filing");
  const method = page.locator("article.print-record.ei-method");
  await expect(sheet).toBeVisible();
  await expect(method).toBeHidden();
  // the sheet is hidden from assistive tech on screen (it is print only), so it is read by tag
  await expect(sheet.locator("h1")).toContainText("Annual report on conditional connection agreements");
  await sheet.screenshot({ path: "test-results/shots/report-print.png" });
  // asked for, the method description for Ei prints instead of the filing
  await page.evaluate(() => { document.documentElement.dataset.print = "ei"; });
  await expect(method).toBeVisible();
  await expect(sheet).toBeHidden();
  await expect(method.locator("h1")).toContainText("Method for conditional connection agreements");
  await method.screenshot({ path: "test-results/shots/ei-method-print.png" });
  await page.evaluate(() => { delete document.documentElement.dataset.print; });
  await page.emulateMedia({ media: "screen" });
});

test("the DSO's rule can be changed from the report, and is sealed", async ({ page, request }) => {
  const before = (await (await request.get("/api/v1/settings")).json()).settings.allocation_policy;
  await page.goto("/#/report?policy=prorata");
  await page.getByRole("button", { name: "Make this the DSO's rule" }).click();
  await expect(page.getByText(/The DSO's sharing rule is now/)).toBeVisible();
  expect((await (await request.get("/api/v1/settings")).json()).settings.allocation_policy).toBe("prorata");
  const chain = await (await request.get("/api/v1/seals")).json();
  expect(chain.ok).toBe(true);
  expect(chain.last[0].kind).toBe("settings");
  // put it back as it was
  await request.put("/api/v1/settings", { data: { allocation_policy: before } });
});
