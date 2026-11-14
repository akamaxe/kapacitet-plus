import { expect, test } from "@playwright/test";

// An installation as shipped (src/kapacitet/features.py): the envelopes, the Sweden screen, the 2030
// scenarios and the applicant's portal are off. The server says so, a route of a part that is off
// answers feature_off, and the portal's page says it is not open rather than failing. The third
// server of playwright.config.ts runs so, on a fresh clone's empty data.
const SHIPPED = "http://127.0.0.1:8133";

test("a server as shipped says which parts are off and refuses their routes", async ({ request }) => {
  const health = await (await request.get(`${SHIPPED}/api/v1/health`)).json();
  expect(health.features).toEqual({ envelopes: false, national: false, scenarios: false, portal: false });
  for (const path of ["/api/v1/national", "/api/v1/compliance", "/api/v1/apply/houses"]) {
    const r = await request.get(`${SHIPPED}${path}`);
    expect(r.status(), path).toBe(404);
    expect((await r.json()).detail.code, path).toBe("feature_off");
  }
});

test("the portal of a server as shipped says it is not open", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  await page.goto(`${SHIPPED}/#/apply`);
  await expect(page.getByRole("heading", { level: 1, name: "The connection portal is not open here" })).toBeVisible();
  expect(errors).toEqual([]);
});
