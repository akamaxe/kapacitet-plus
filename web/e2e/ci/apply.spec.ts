import { expect, test } from "@playwright/test";

// The applicant's portal is its own small page: it opens at #/apply without being turned into a
// screen of the application, and without downloading the map or the chart library. On a fresh
// clone's empty data it says applications are not open yet.
test("the portal opens on its own, without the map or the chart library", async ({ page }) => {
  const loaded: string[] = [];
  page.on("request", (r) => loaded.push(r.url()));
  await page.goto("/#/apply");
  await expect(page.getByRole("heading", { level: 1, name: "Apply for a connection" })).toBeVisible();
  await expect(page.getByText(/Applications are not open yet/)).toBeVisible();
  expect(page.url()).toContain("#/apply");
  expect(loaded.filter((u) => /MapView|\/Chart-|maplibre|echarts/i.test(u))).toEqual([]);
  // the status form still answers, and an unknown code is said in words
  await page.getByLabel("Reference code").fill("KP-0000-0000");
  await page.getByRole("button", { name: "Show the state" }).click();
  await expect(page.getByRole("alert")).toContainText(/Unknown application/);
});
