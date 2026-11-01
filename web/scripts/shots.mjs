// Screenshots of every screen, for looking at a change in each language, theme and width.
//
//   node scripts/shots.mjs [--lang en,es,sv] [--theme light,dark] [--width 1536,390] [--screens a,b]
//                          [--base http://127.0.0.1:8130] [--out test-results/shots]
//
// Needs a running `python -m kapacitet serve` (port 8130 by default) and the system's Chrome.
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? process.argv[i + 1] : fallback;
};
const langs = arg("lang", "en").split(",");
const themes = arg("theme", "light").split(",");
const widths = arg("width", "1536").split(",").map(Number);
const screens = arg("screens", "national,twin,compare,request,report,scenarios,method").split(",");
const base = arg("base", "http://127.0.0.1:8130");
const out = arg("out", "test-results/shots");
mkdirSync(out, { recursive: true });

const browser = await chromium.launch({ channel: "chrome" });
for (const lang of langs) {
  for (const theme of themes) {
    for (const width of widths) {
      const context = await browser.newContext({ viewport: { width, height: width < 700 ? 844 : 730 } });
      await context.addInitScript(([l, t]) => {
        localStorage.setItem("kap.lang", l);
        localStorage.setItem("kap.theme", t);
        localStorage.setItem("kap.tour", "1");
      }, [lang, theme]);
      const page = await context.newPage();
      for (const screen of screens) {
        await page.goto(`${base}/#/${screen}`);
        await page.locator("main h1").first().waitFor({ timeout: 60_000 }).catch(() => undefined);
        await page.waitForLoadState("networkidle").catch(() => undefined);
        await page.waitForTimeout(800);
        const file = join(out, `${screen}-${lang}-${theme}-${width}.png`);
        await page.screenshot({ path: file });
        console.log(file);
      }
      await context.close();
    }
  }
}
await browser.close();
