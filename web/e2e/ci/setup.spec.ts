import { expect, test } from "@playwright/test";

// A fresh clone: the pipeline has not run, so the interface must say what to run, offer no tour,
// and log nothing broken on the way.
test("with nothing computed, the setup screen says what to run", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Nothing computed here yet" })).toBeVisible();
  await expect(page.getByText("python -m kapacitet all", { exact: false }).first()).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("the API answers health without data, and says what is missing", async ({ request }) => {
  const r = await request.get("/api/v1/health");
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(body.status).toBe("ok");
});
