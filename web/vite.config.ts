import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

/**
 * The opening screen asks for two files late: the latin cut of the web font (found only once the
 * stylesheet has loaded) and the comparison screen's chunk (found only once the application has
 * run). Preloading both from the HTML saves a round trip each on a first visit.
 */
function preloadOpening(): Plugin {
  return {
    name: "kapacitet-preload-opening",
    apply: "build",
    transformIndexHtml: {
      order: "post",
      handler(_html, ctx) {
        const bundle = ctx.bundle;
        if (!bundle) return [];
        const files = Object.values(bundle);
        const font = files.find((f) => /familjen-grotesk-latin-wght-normal-[\w-]+\.woff2$/.test(f.fileName));
        const opening = files.find((f) => f.type === "chunk" && (f as { facadeModuleId?: string | null }).facadeModuleId?.replace(/\\/g, "/").endsWith("/screens/Start.tsx"));
        const tags = [];
        if (font) tags.push({ tag: "link", attrs: { rel: "preload", as: "font", type: "font/woff2", href: `/${font.fileName}`, crossorigin: "" }, injectTo: "head" as const });
        if (opening) tags.push({ tag: "link", attrs: { rel: "modulepreload", crossorigin: "", href: `/${opening.fileName}` }, injectTo: "head" as const });
        return tags;
      },
    },
  };
}

// The API runs on :8000 (python -m kapacitet serve); the dev server proxies /api to it.
export default defineConfig({
  plugins: [react(), preloadOpening()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: {
    outDir: "dist",
    // main.tsx awaits the stored language before the first render (top-level await)
    target: "es2022",
    chunkSizeWarningLimit: 2500,
  },
  // MapLibre 6 runs its worker as an ES module (see MapView.tsx: setWorkerUrl).
  worker: { format: "es" },
});
