import { expect, test } from "@playwright/test";

// The workbench on this machine's computed data: a project is opened from the start window, its
// tree says what it holds and leads to each part, and the status bar says which project it is.
test("the start window lists the project in use, and opens it where it was left", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Projects" })).toBeVisible({ timeout: 60_000 });
  const row = page.locator(".start-table tbody tr").filter({ hasText: "In use" });
  await expect(row).toHaveCount(1);
  // no project is on show, so the bar has no tree and the menus that study one are away
  await expect(page.locator("aside.tree-dock")).toHaveCount(0);
  await expect(page.getByRole("menubar").getByRole("menuitem")).toHaveText(["File", "View", "Tools", "Help"]);
  await page.locator(".proj-detail").getByRole("button", { name: "Open project" }).click();
  await expect(page).toHaveURL(/#\/compare/);
  await expect(page.getByRole("menubar").getByRole("menuitem")).toHaveText(["File", "View", "Study", "Results", "Tools", "Help"]);
});

test("the project tree holds the network, the studies and the results, and leads to each", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto("/#/compare");
  const tree = page.getByRole("tree");
  await expect(tree).toBeVisible({ timeout: 60_000 });
  await expect(tree.getByRole("treeitem", { name: "Static vs envelopes" })).toHaveAttribute("aria-current", "page");
  for (const group of ["Network", "Studies", "Results", "Reference"]) {
    await expect(tree.getByRole("treeitem", { name: group, exact: true })).toHaveAttribute("aria-expanded", "true");
  }

  // a study opens its screen
  await tree.getByRole("treeitem", { name: "Annual report" }).click();
  await expect(page).toHaveURL(/#\/report/);
  await expect(tree.getByRole("treeitem", { name: "Annual report" })).toHaveAttribute("aria-current", "page");

  // a feeder opens on its houses, and a house is selected as the map and the search select it
  const feeder = tree.locator('[data-kind="feeder"]').first();
  await expect(feeder).toHaveAttribute("aria-expanded", "false");
  await feeder.click();
  await expect(feeder).toHaveAttribute("aria-expanded", "true");
  const house = tree.locator('[data-kind="house"]').first();
  await house.click();
  await expect(page).toHaveURL(/#\/twin\?.*cid=\d+/);
  await expect(house).toHaveAttribute("aria-selected", "true");

  // the keyboard walks it: one row is in the tab order, and the arrows move the focus
  await tree.locator('[data-id="project"]').focus();
  await page.keyboard.press("ArrowDown");
  await expect(tree.getByRole("treeitem", { name: "Network", exact: true })).toBeFocused();
  await page.keyboard.press("ArrowLeft");
  await expect(tree.getByRole("treeitem", { name: "Network", exact: true })).toHaveAttribute("aria-expanded", "false");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await expect(tree.getByRole("treeitem", { name: "Neighbourhood" })).toBeFocused();
  expect(await tree.locator('[tabindex="0"]').count()).toBe(1);
  expect(errors).toEqual([]);
});

test("the tree folds to a rail and the map takes the room it leaves", async ({ page }) => {
  await page.goto("/#/twin");
  const dock = page.locator("aside.tree-dock");
  await expect(dock).toHaveAttribute("data-dock", "tree", { timeout: 60_000 });
  const canvas = page.locator(".maplibregl-canvas");
  await expect(canvas).toBeVisible({ timeout: 60_000 });
  const before = (await canvas.boundingBox())!.width;
  await page.getByRole("button", { name: "Fold the project tree" }).click();
  await expect(dock).toHaveAttribute("data-dock", "rail");
  // the map follows its own box, not the window's
  await expect.poll(async () => (await canvas.boundingBox())!.width).toBeGreaterThan(before + 150);
  const box = (await page.locator(".map").first().boundingBox())!.width;
  expect(Math.abs((await canvas.boundingBox())!.width - box)).toBeLessThanOrEqual(1);
  // the rail leads to the screens, and the choice is kept
  await dock.getByRole("link", { name: "Operation" }).click();
  await expect(page).toHaveURL(/#\/operation/);
  await page.reload();
  await expect(dock).toHaveAttribute("data-dock", "rail", { timeout: 60_000 });
  await page.keyboard.press("t");
  await expect(dock).toHaveAttribute("data-dock", "tree");
});

test("the status bar names the project and its year", async ({ page }) => {
  await page.goto("/#/compare");
  const foot = page.locator("footer.statusbar");
  await expect(foot.getByText("Connected")).toBeVisible({ timeout: 60_000 });
  const site = (await (await page.request.get("/api/v1/meta")).json()).site as string;
  await expect(foot.locator(".sb-name")).toHaveText(site);
  await expect(foot).toContainText("Reference year");
  // the bar's name of the project opens its properties
  await page.locator("header.topbar").getByRole("button", { name: site }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: site })).toBeVisible();
  await expect(dialog.getByText("Identifier")).toBeVisible();
});
