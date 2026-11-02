import { expect, test } from "@playwright/test";

// What the application costs before it shows anything: the entry chunk stays small, and a screen
// with no chart and no map (the setup screen of a fresh clone) never downloads either library.
const ENTRY_BUDGET_KB = 80; // gzip; 66 kB when the budget was set (docs/perf.md)

test("the entry chunk stays within its budget and the heavy libraries wait for a screen that draws", async ({ page }) => {
  const scripts: { url: string; kb: number }[] = [];
  page.on("requestfinished", async (req) => {
    if (req.resourceType() !== "script") return;
    const s = await req.sizes().catch(() => null);
    if (s) scripts.push({ url: req.url(), kb: (s.responseBodySize + s.responseHeadersSize) / 1024 });
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.waitForLoadState("networkidle");
  const entry = scripts.find((s) => /\/assets\/index-[\w-]+\.js$/.test(s.url));
  expect(entry, "the entry chunk was requested").toBeTruthy();
  expect(entry!.kb).toBeLessThanOrEqual(ENTRY_BUDGET_KB);
  // the map library, and the chart library (not the few-byte chunk that only re-exports a helper)
  const heavy = scripts.filter((s) => /\/assets\/MapView-/.test(s.url) || (/\/assets\/Chart-/.test(s.url) && s.kb > 50));
  expect(heavy.map((s) => s.url)).toEqual([]);
});
