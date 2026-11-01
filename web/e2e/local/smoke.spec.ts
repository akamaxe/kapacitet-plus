import { expect, test } from "@playwright/test";

// Every screen opens on this machine's computed data with its title and without a console error.
const SCREENS = ["national", "twin", "compare", "request", "operation", "report", "scenarios", "method"];

for (const screen of SCREENS) {
  test(`${screen} opens with its title and no console error`, async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    page.on("pageerror", (e) => errors.push(String(e)));

    await page.goto(`/#/${screen}`);
    await expect(page.locator("main h1").first()).toBeVisible({ timeout: 60_000 });
    await page.waitForLoadState("networkidle");
    expect(errors).toEqual([]);
  });
}
