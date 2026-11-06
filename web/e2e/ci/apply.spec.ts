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
  await page.getByLabel("Reference code").fill("KP-0000-0000-0000-0000");
  await page.getByRole("button", { name: "Show the state" }).click();
  await expect(page.getByRole("alert")).toContainText(/Unknown application/);
});

// The server takes a write only from its own pages and only as JSON (api/guard.py): the same
// application sent from another site, or as plain text, is refused before it reaches the portal,
// while the portal's own origin goes through to the route, which knows no such answer.
test("a write from another site, or not as JSON, is refused before it reaches the route", async ({ request, baseURL }) => {
  const body = { assessment: "0123456789ab", key: "e2e-guard-key-0123456789" };
  const foreign = await request.post("/api/v1/apply/submit", { data: body, headers: { Origin: "https://evil.example" } });
  expect(foreign.status()).toBe(403);
  expect((await foreign.json()).detail.code).toBe("bad_origin");
  const plain = await request.post("/api/v1/apply/submit", { data: JSON.stringify(body), headers: { "Content-Type": "text/plain" } });
  expect(plain.status()).toBe(415);
  const own = await request.post("/api/v1/apply/submit", { data: body, headers: { Origin: new URL(baseURL!).origin } });
  expect(own.status()).toBe(404);
  expect((await own.json()).detail.code).toBe("unknown_assessment");
});
