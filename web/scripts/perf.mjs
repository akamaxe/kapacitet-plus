// What the opening screen costs a first visit: bytes over the wire by kind, request count, the
// largest contentful paint and long tasks, measured in the system's Chrome with an empty cache.
//
//   node scripts/perf.mjs [url] [out.json]
//   default url: http://127.0.0.1:8130/#/compare (a running `python -m kapacitet serve --port 8130`)
//
// Numbers vary from run to run: it takes the median of three visits.
import { chromium } from "@playwright/test";
import { writeFileSync } from "node:fs";

const url = process.argv[2] ?? "http://127.0.0.1:8130/#/compare";
const out = process.argv[3];
const RUNS = 3;

function median(xs) {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

async function visit(browser) {
  const context = await browser.newContext({ viewport: { width: 1536, height: 730 } });
  const page = await context.newPage();
  const requests = [];
  page.on("requestfinished", async (req) => {
    try {
      const sizes = await req.sizes();
      requests.push({ url: req.url(), type: req.resourceType(), bytes: sizes.responseBodySize + sizes.responseHeadersSize });
    } catch { /* a request the page abandoned */ }
  });
  await page.addInitScript(() => {
    window.__perf = { lcp: 0, long: [] };
    new PerformanceObserver((list) => { for (const e of list.getEntries()) window.__perf.lcp = e.startTime; })
      .observe({ type: "largest-contentful-paint", buffered: true });
    new PerformanceObserver((list) => { for (const e of list.getEntries()) window.__perf.long.push(e.duration); })
      .observe({ type: "longtask", buffered: true });
  });
  await page.goto(url, { waitUntil: "networkidle", timeout: 120_000 });
  await page.waitForTimeout(1500);
  const m = await page.evaluate(() => {
    const nav = performance.getEntriesByType("navigation")[0];
    return { lcp: window.__perf.lcp, long: window.__perf.long, dcl: nav?.domContentLoadedEventEnd ?? null };
  });
  await context.close();
  const by = {};
  for (const r of requests) {
    const kind = r.url.includes("/api/") ? "api" : r.type;
    by[kind] = (by[kind] ?? 0) + r.bytes;
  }
  return {
    requests: requests.length,
    bytes: by,
    total_bytes: requests.reduce((a, r) => a + r.bytes, 0),
    lcp_ms: Math.round(m.lcp),
    dcl_ms: m.dcl === null ? null : Math.round(m.dcl),
    long_tasks: m.long.length,
    long_task_ms: Math.round(m.long.reduce((a, d) => a + d, 0)),
  };
}

const browser = await chromium.launch({ channel: "chrome" });
const runs = [];
for (let i = 0; i < RUNS; i++) runs.push(await visit(browser));
await browser.close();

const kinds = [...new Set(runs.flatMap((r) => Object.keys(r.bytes)))];
const result = {
  url,
  measured_at: new Date().toISOString(),
  runs: RUNS,
  median: {
    requests: median(runs.map((r) => r.requests)),
    total_kb: Math.round(median(runs.map((r) => r.total_bytes)) / 1024),
    kb: Object.fromEntries(kinds.map((k) => [k, Math.round(median(runs.map((r) => r.bytes[k] ?? 0)) / 1024)])),
    lcp_ms: median(runs.map((r) => r.lcp_ms)),
    dcl_ms: median(runs.map((r) => r.dcl_ms ?? 0)),
    long_tasks: median(runs.map((r) => r.long_tasks)),
    long_task_ms: median(runs.map((r) => r.long_task_ms)),
  },
};
console.log(JSON.stringify(result, null, 2));
if (out) writeFileSync(out, JSON.stringify(result, null, 2) + "\n");
