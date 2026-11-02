import { expect, test } from "@playwright/test";

// The opening screen on this machine's data: its largest paint comes before the chart and map
// libraries are needed, and the heatmaps far down the screen load only when their section nears.
const LCP_BUDGET_MS = 1500; // 204 ms when the budget was set (docs/perf.md)

test("the comparison screen paints within its budget and fetches the heatmaps only when they near", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("kap.tour", "1"));
  const api: string[] = [];
  page.on("request", (r) => { if (r.url().includes("/api/v1/")) api.push(r.url()); });
  await page.goto("/#/compare");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 60_000 });
  const lcp = await page.evaluate(() => new Promise<number>((resolve) => {
    new PerformanceObserver((list) => {
      const entries = list.getEntries();
      resolve(entries[entries.length - 1].startTime);
    }).observe({ type: "largest-contentful-paint", buffered: true });
  }));
  expect(lcp).toBeLessThan(LCP_BUDGET_MS);
  expect(api.some((u) => u.includes("/showcase/heatmaps"))).toBe(false);
  await page.getByRole("button", { name: "When it binds" }).click();
  await expect.poll(() => api.some((u) => u.includes("/showcase/heatmaps")), { timeout: 10_000 }).toBe(true);
});
