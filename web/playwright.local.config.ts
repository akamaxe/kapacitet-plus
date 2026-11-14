import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

// The local project (npm run test:e2e:local): the real API on this machine's computed data, for
// the flows that need numbers. A server already running on the port is reused, so a check can
// share the verification server instead of starting a second engine. The server it starts has every
// part on (KAPACITET_FEATURES=all unless the shell names others), since these flows go through the
// envelopes and the portal; the parts as shipped are capacity.spec.ts's, against E2E_CAPACITY_URL.
const HERE = fileURLToPath(new URL(".", import.meta.url));
const ROOT = resolve(HERE, "..");
const PY = process.env.KAPACITET_PY
  ?? resolve(ROOT, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const PORT = Number(process.env.E2E_PORT ?? 8130);

export default defineConfig({
  testDir: "e2e/local",
  timeout: 120_000,
  workers: 1,
  reporter: "list",
  use: { baseURL: `http://127.0.0.1:${PORT}`, channel: "chrome", headless: true, viewport: { width: 1536, height: 730 } },
  webServer: {
    command: `"${PY}" -m kapacitet serve --port ${PORT}`,
    cwd: ROOT,
    url: `http://127.0.0.1:${PORT}/api/v1/health`,
    env: { ...(process.env as Record<string, string>), KAPACITET_FEATURES: process.env.KAPACITET_FEATURES ?? "all" },
    reuseExistingServer: true,
    timeout: 180_000,
  },
});
