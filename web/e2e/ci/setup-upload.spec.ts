import { expect, test } from "@playwright/test";

// A copy that may take its data from the browser (KAPACITET_SETUP_UPLOAD, as in a codespace): the
// setup page becomes one file chooser, the commands fold away, and a wrong file says so in plain words.
test.use({ baseURL: "http://127.0.0.1:8132" });

test("an empty copy that allows it asks for the data file", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Load the data" })).toBeVisible();
  await expect(page.getByText("Choose kapacitet-data.zip")).toBeVisible();
  await expect(page.getByText("python -m kapacitet all")).toBeHidden();
  await page.getByText("From a terminal instead").click();
  await expect(page.getByText("python -m kapacitet all")).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("a file that is not a data bundle is refused at once", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles({
    name: "notes.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.7 not a bundle"),
  });
  await expect(page.getByRole("alert")).toContainText("not a Kapacitet+ data file");
  // nothing was kept: the next try starts from the beginning
  const st = await (await page.request.get("/api/v1/setup/upload")).json();
  expect(st.received).toBe(0);
  expect(st.open).toBe(true);
});
