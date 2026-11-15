import { expect, test } from "@playwright/test";

// Task 2.1: the studies in the start window. Making one would write in the server's studies' folder
// (KAPACITET_STUDY_DIR, the user's own by default), so this only opens the panel and leaves it; the
// whole flow, made, built, reviewed, signed and published, is tests/test_api_studies.py's.
test("the start window lists the studies and offers a new one", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  await page.goto("/#/start");
  await expect(page.getByRole("heading", { name: "Studies" })).toBeVisible();
  await page.getByRole("button", { name: "New study…" }).first().click();
  await expect(page.getByRole("heading", { name: "New study" })).toBeVisible();
  for (const source of ["Demonstration", "Empty", "Contract folders (.zip)", "CGMES"]) {
    await page.getByRole("button", { name: source, exact: true }).click();
    await expect(page.getByRole("button", { name: source, exact: true })).toHaveAttribute("aria-pressed", "true");
  }
  await expect(page.getByRole("button", { name: "Create the study" })).toBeDisabled();   // no CGMES files chosen
  await page.getByRole("button", { name: "Back to the projects" }).click();
  await expect(page.getByRole("heading", { name: "Studies" })).toBeVisible();
  // the File menu has the same entry
  await page.getByRole("menuitem", { name: "File" }).click();
  await expect(page.getByRole("menuitem", { name: "New study…" })).toBeVisible();
  expect(errors).toEqual([]);
});
