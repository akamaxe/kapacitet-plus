import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API runs on :8000 (python -m kapacitet serve); the dev server proxies /api to it.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 2500,
  },
  // MapLibre 6 runs its worker as an ES module (see MapView.tsx: setWorkerUrl).
  worker: { format: "es" },
});
