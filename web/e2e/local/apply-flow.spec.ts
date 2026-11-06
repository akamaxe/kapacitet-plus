import { expect, test } from "@playwright/test";

// A household's whole path on this machine's data: pick an address, ask for a small solar system,
// read the answer, apply, and follow the application until it is decided, or waits for a person with
// the rule's proposal (advisor mode, the default: the rule decides only what a person signed).
test("a household applies, and the rule decides or proposes", async ({ page, request }) => {
  const houses = (await (await request.get("/api/v1/apply/houses")).json()).items;
  // a house the queue has not approved and that has no application waiting: the test can run again
  const queue = (await (await request.get("/api/v1/queue")).json()).items;
  const inbox = (await (await request.get("/api/v1/inbox")).json()).items;
  const taken = new Set<number>([
    ...queue.filter((it: any) => it.decision !== "declined").map((it: any) => it.cid),
    ...inbox.filter((a: any) => a.status === "pending" || a.status === "review").map((a: any) => a.cid),
  ]);
  const house = houses.find((h: any) => h.address && !taken.has(h.cid));
  expect(house, "a free house to apply for").toBeTruthy();
  await page.goto("/#/apply");
  await expect(page.getByRole("heading", { level: 1, name: "Apply for a connection" })).toBeVisible({ timeout: 60_000 });
  await page.getByLabel("Address").fill(house.address);
  await expect(page.getByText(house.address, { exact: true })).toBeVisible();
  await page.getByLabel("Solar").fill("4");
  await page.getByRole("button", { name: "Check my house" }).click();
  await expect(page.getByText(/^Yes/)).toBeVisible({ timeout: 120_000 });
  await page.getByRole("button", { name: "Apply", exact: true }).click();
  const ref = page.locator(".portal-ref");
  await expect(ref).toHaveText(/^KP-(?:[0-9A-HJKMNP-TV-Z]{4}-){3}[0-9A-HJKMNP-TV-Z]{4}$/);
  await expect(page).toHaveURL(/#\/apply\?ref=KP-/);
  const box = await (await request.get("/api/v1/inbox")).json();
  const sig = box.rule_signature;
  const signed = box.auto_decisions && box.signature_state === "in_force" && sig.request_kinds.includes("pv") && sig.max_pv_kwp >= 4;
  if (signed) {
    // a signed rule decides a small system on its own, after checking every weather year
    await expect(page.getByText(/Connected (firm|with an envelope)/)).toBeVisible({ timeout: 180_000 });
  } else {
    // advisor mode: it waits for a person, and the DSO's inbox keeps what the rule proposes
    await expect(page.getByText("Needs a person")).toBeVisible({ timeout: 180_000 });
    const app = (await (await request.get("/api/v1/inbox")).json()).items.find((a: any) => a.cid === house.cid);
    expect(["firm", "nonfirm"]).toContain(app.proposal);
  }
  await page.screenshot({ path: "test-results/shots/apply-flow.png", fullPage: true });
});
