import { expect, test } from "@playwright/test";

// A household's whole path on this machine's data: pick an address, ask for a small solar system,
// read the answer, apply, and follow the application until the DSO's rule has decided it.
test("a household applies and the rule decides", async ({ page, request }) => {
  const houses = (await (await request.get("/api/v1/apply/houses")).json()).items;
  const house = houses.find((h: any) => h.address) ?? houses[0];
  await page.goto("/#/apply");
  await expect(page.getByRole("heading", { level: 1, name: "Apply for a connection" })).toBeVisible({ timeout: 60_000 });
  await page.getByLabel("Address").fill(house.address);
  await expect(page.getByText(house.address, { exact: true })).toBeVisible();
  await page.getByLabel("Solar").fill("4");
  await page.getByRole("button", { name: "Check my house" }).click();
  await expect(page.getByText(/^Yes/)).toBeVisible({ timeout: 120_000 });
  await page.getByRole("button", { name: "Apply", exact: true }).click();
  const ref = page.locator(".portal-ref");
  await expect(ref).toHaveText(/^KP-[0-9A-F]{4}-[0-9A-F]{4}$/);
  await expect(page).toHaveURL(/#\/apply\?ref=KP-/);
  // the rule decides a small system on its own, after checking every weather year
  await expect(page.getByText(/Connected (firm|with an envelope)/)).toBeVisible({ timeout: 180_000 });
  await page.screenshot({ path: "test-results/shots/apply-flow.png", fullPage: true });
});
