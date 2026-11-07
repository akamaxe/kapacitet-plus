// Runs the embed page in jsdom against a publication folder; prints what it drew as JSON.
// usage: node embed_dom.mjs <jsdom dir> <publication dir> [query]
import { createRequire } from "node:module";
import { readFileSync, existsSync } from "node:fs";
import { join, resolve } from "node:path";

const [, , jsdomDir, pub, query = ""] = process.argv;
const { JSDOM } = createRequire(join(resolve(jsdomDir), "x.js"))("jsdom");
const html = readFileSync(join(pub, "embed", "index.html"), "utf8");
const requests = [];
const dom = new JSDOM(html, {
  url: "https://dso.example/pub/embed/index.html" + query,
  runScripts: "outside-only",
  pretendToBeVisual: true,
});
const w = dom.window;
w.fetch = async (u) => {
  const url = new URL(u, w.location.href);
  requests.push(url.href);
  const file = join(pub, url.pathname.replace("/pub/", ""));
  if (url.origin !== "https://dso.example" || !existsSync(file)) return { ok: false, status: 404, json: async () => ({}) };
  return { ok: true, status: 200, json: async () => JSON.parse(readFileSync(file, "utf8")) };
};
w.eval(readFileSync(join(pub, "embed", "embed.js"), "utf8"));
await new Promise((r) => setTimeout(r, 300));
const d = w.document;
const cells = [...d.querySelectorAll("svg .cell")];
console.log(JSON.stringify({
  ready: d.body.classList.contains("is-ready"),
  cells: cells.length,
  focusable: cells.filter((c) => c.getAttribute("tabindex") === "0").length,
  named: cells.filter((c) => (c.getAttribute("aria-label") || "").length > 0).length,
  rows: d.querySelectorAll("table tbody tr").length,
  layers: [...d.querySelectorAll("#embed-switch input")].map((i) => i.value),
  draft: !d.getElementById("embed-draft-note").hidden,
  text: d.body.textContent,
  external: requests.filter((r) => !r.startsWith("https://dso.example/")),
  requests,
}));
