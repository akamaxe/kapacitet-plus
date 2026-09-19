/** Colour helpers that read the design tokens, so charts and maps follow the active theme. */

export function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function tokens() {
  return {
    paper: token("--paper"),
    surface: token("--surface"),
    surface2: token("--surface-2"),
    ink: token("--ink"),
    ink2: token("--ink-2"),
    muted: token("--muted"),
    hairline: token("--hairline"),
    baseline: token("--baseline"),
    s1: token("--series-1"),
    s2: token("--series-2"),
    s3: token("--series-3"),
    deemph: token("--deemph"),
    good: token("--good"),
    warning: token("--warning"),
    serious: token("--serious"),
    critical: token("--critical"),
    seq: ["--seq-100", "--seq-200", "--seq-300", "--seq-400", "--seq-500", "--seq-600", "--seq-700"].map(token),
    divLow: token("--div-low"),
    divMid: token("--div-mid"),
    divHigh: token("--div-high"),
    font: token("--font"),
  };
}

export type Tokens = ReturnType<typeof tokens>;

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function mix(a: string, b: string, t: number): string {
  const [r1, g1, b1] = hexToRgb(a);
  const [r2, g2, b2] = hexToRgb(b);
  const c = (x: number, y: number) => Math.round(x + (y - x) * t);
  return `rgb(${c(r1, r2)},${c(g1, g2)},${c(b1, b2)})`;
}

/** Sequential ramp: t in [0,1] → one-hue light→dark colour. */
export function seqColor(tk: Tokens, t: number): string {
  const s = tk.seq;
  const x = Math.max(0, Math.min(1, t)) * (s.length - 1);
  const i = Math.min(s.length - 2, Math.floor(x));
  return mix(s[i], s[i + 1], x - i);
}

/** Diverging voltage colour: 1.0 pu neutral, low → blue, high → red, saturating at ±span. */
export function voltageColor(tk: Tokens, v: number, span = 0.1): string {
  const d = Math.max(-1, Math.min(1, (v - 1) / span));
  return d < 0 ? mix(tk.divMid, tk.divLow, -d) : mix(tk.divMid, tk.divHigh, d);
}

/** Net power at a house: import (blue) ← 0 (neutral) → export (orange), saturating at ±span kW. */
export function powerColor(tk: Tokens, kw: number, span = 8): string {
  const d = Math.max(-1, Math.min(1, kw / span));
  return d < 0 ? mix(tk.divMid, tk.divLow, -d) : mix(tk.divMid, tk.s2, d);
}

/** Cable loading: sequential up to 100 %, reserved critical colour above the rating. */
export function loadingColor(tk: Tokens, pct: number): string {
  if (pct > 100) return tk.critical;
  return seqColor(tk, pct / 100);
}
