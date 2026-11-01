import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Unit tests of the interface's pure parts and small components (npm run test:unit).
// jsdom gives the store its window, history and localStorage; dates are read in UTC so a
// formatted time is the same on a laptop in Stockholm and on the CI runner.
export default defineConfig({
  plugins: [react()],
  test: {
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    environment: "jsdom",
    env: { TZ: "UTC" },
  },
});
