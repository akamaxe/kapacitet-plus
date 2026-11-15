import { expect, test } from "@playwright/test";

// A network export whose cable names come from a network information system the program does not
// know (task 2.2 of the technical review): New project's check names them with their sections, the
// table translates each to a type of the catalogue of Swedish cables, and the export passes with
// the translation as its cable_types.csv. The tables are pandapower's 12-house Dickert network; it is
// a local flow because without computed results the setup screen stands in for the start window.
const ELECTRICAL = new Set(["r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km", "max_i_ka"]);

function nisNamed(lines: string): string {
  const [head, ...rows] = lines.trim().split("\n");
  const cols = head.split(",");
  const at = cols.indexOf("std_type");
  const keep = cols.map((c, i) => (ELECTRICAL.has(c) ? -1 : i)).filter((i) => i >= 0);
  const named = rows.map((row, n) => {
    const cells = row.split(",");
    cells[at] = n % 2 ? "KABEL 95 AL" : "N1XE-AR 4G150";
    return keep.map((i) => cells[i]).join(",");
  });
  return [keep.map((i) => cols[i]).join(","), ...named].join("\n") + "\n";
}

test("new project translates the cable names of a network information system", async ({ page, request }) => {
  const errors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  const { files } = (await (await request.get("/api/v1/templates/dickert-short")).json()) as { files: Record<string, string> };
  const tables = { ...files, lines: nisNamed(files.lines) };

  await page.goto("/#/start");
  await page.getByRole("button", { name: "New project…" }).first().click();
  await page.locator('input[type="file"][accept=".csv,text/csv"]').setInputFiles(
    Object.entries(tables).map(([name, text]) => ({ name: `${name}.csv`, mimeType: "text/csv", buffer: Buffer.from(text) })),
  );

  const table = page.locator(".new-cables table");
  await expect(table).toContainText("N1XE-AR 4G150");
  await expect(table).toContainText("KABEL 95 AL");
  await expect(page.getByText("Not usable yet", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Continue" })).toBeDisabled();

  await page.getByLabel("Catalogue type for N1XE-AR 4G150").selectOption("AXQJ 4x150/41");
  await page.getByLabel("Catalogue type for KABEL 95 AL").selectOption("AXQJ 4x95/29");
  await page.getByRole("button", { name: "Translate and check again" }).click();

  await expect(page.getByText("2 cable names translated in cable_types.csv.")).toBeVisible();
  await expect(page.getByText("Usable: the engine can build on it")).toBeVisible();
  await expect(page.getByRole("button", { name: "Continue" })).toBeEnabled();

  // and taken back, the names are unknown again
  await page.getByRole("button", { name: "Undo the translation" }).click();
  await expect(page.locator(".new-cables table")).toContainText("KABEL 95 AL");
  await expect(page.getByRole("button", { name: "Continue" })).toBeDisabled();
  expect(errors).toEqual([]);
});
