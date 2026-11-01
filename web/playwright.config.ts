import { defineConfig } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

// The CI project (npm run test:e2e): the real API pointed at an empty data folder, as on a fresh
// clone, so the interface has to explain itself. It needs web/dist (npm run build) and the
// project's Python: KAPACITET_PY, or kapacitet/.venv by default. Playwright drives the system's
// Chrome (channel "chrome"), so nothing is downloaded. The data flows are in
// playwright.local.config.ts.
const HERE = fileURLToPath(new URL(".", import.meta.url));
const ROOT = resolve(HERE, "..");
const EMPTY = resolve(HERE, "e2e/.empty-data");
mkdirSync(EMPTY, { recursive: true });
const PY = process.env.KAPACITET_PY
  ?? resolve(ROOT, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const PORT = 8131;

export default defineConfig({
  testDir: "e2e/ci",
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL: `http://127.0.0.1:${PORT}`, channel: "chrome", headless: true },
  webServer: {
    command: `"${PY}" -m kapacitet serve --port ${PORT}`,
    cwd: ROOT,
    url: `http://127.0.0.1:${PORT}/api/v1/health`,
    env: { ...(process.env as Record<string, string>), KAPACITET_DATA_DIR: EMPTY },
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
