import { expect, test } from "@playwright/test";

// A request for an 11 kW charger answers whether the house's main fuse carries it, next to the
// grid's own verdicts, and the static study shows its numbers.
test("a charger request shows the main fuse verdict and the static study's numbers", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.addInitScript(() => localStorage.setItem("kap.tour", "1"));

  const meta = await (await page.request.get("/api/v1/meta")).json();
  const cid = Number(meta.grid.customers) - 1;
  await page.goto(`/#/request?cid=${cid}`);
  await expect(page.getByRole("button", { name: "Evaluate connection" })).toBeVisible({ timeout: 60_000 });

  await page.getByRole("group", { name: "EV charger" }).getByRole("button", { name: "11 kW" }).click();
  await page.getByRole("button", { name: "Evaluate connection" }).click();

  const fuse = page.getByRole("main").locator(".verdict").filter({ hasText: "Main fuse" });
  await expect(fuse).toBeVisible({ timeout: 90_000 });
  await expect(fuse).toContainText(/carries the busiest phase|needs a \d+ A fuse subscription|largest fuse subscription/);
  await expect(page.getByRole("main").getByText(/At the winter peak: transformer \d+\s%/)).toBeVisible();

  // the answer is sealed, and its seal checks out
  const seal = page.getByRole("main").locator(".seal-line");
  await expect(seal).toContainText("Sealed");
  await seal.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByText(/Intact: the kept record still hashes to its seal/)).toBeVisible();
  expect(errors).toEqual([]);
});
