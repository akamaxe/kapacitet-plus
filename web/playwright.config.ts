import { defineConfig } from "@playwright/test";
import { mkdirSync, rmSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

// The CI project (npm run test:e2e): the real API pointed at an empty data folder, as on a fresh
// clone, so the interface has to explain itself. It needs web/dist (npm run build) and the
// project's Python: KAPACITET_PY, or kapacitet/.venv by default. Playwright drives the system's
// Chrome (channel "chrome"), so nothing is downloaded. The data flows are in
// playwright.local.config.ts. A second server allows the setup page's bundle upload
// (KAPACITET_SETUP_UPLOAD, as in a codespace) on a folder emptied at every run; it takes any
// well-formed bundle (KAPACITET_SETUP_ANY_BUNDLE), since the tests' files are not the pinned release.
const HERE = fileURLToPath(new URL(".", import.meta.url));
const ROOT = resolve(HERE, "..");
const EMPTY = resolve(HERE, "e2e/.empty-data");
mkdirSync(EMPTY, { recursive: true });
const PY = process.env.KAPACITET_PY
  ?? resolve(ROOT, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const PORT = 8131;
const UPLOAD = resolve(HERE, "e2e/.upload-data");
const UPLOAD_PORT = 8132;
// emptied once per run, by the main process: the workers load this file too
if (!process.env.TEST_WORKER_INDEX) rmSync(UPLOAD, { recursive: true, force: true });
mkdirSync(UPLOAD, { recursive: true });

export default defineConfig({
  testDir: "e2e/ci",
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL: `http://127.0.0.1:${PORT}`, channel: "chrome", headless: true },
  webServer: [
    {
      command: `"${PY}" -m kapacitet serve --port ${PORT}`,
      cwd: ROOT,
      url: `http://127.0.0.1:${PORT}/api/v1/health`,
      env: { ...(process.env as Record<string, string>), KAPACITET_DATA_DIR: EMPTY },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `"${PY}" -m kapacitet serve --port ${UPLOAD_PORT}`,
      cwd: ROOT,
      url: `http://127.0.0.1:${UPLOAD_PORT}/api/v1/health`,
      env: {
        ...(process.env as Record<string, string>),
        KAPACITET_DATA_DIR: UPLOAD, KAPACITET_SETUP_UPLOAD: "1", KAPACITET_SETUP_ANY_BUNDLE: "1",
      },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
