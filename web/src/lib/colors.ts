/** Colour helpers that read the design tokens, so charts and maps follow the active theme. */

export function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function tokens() {
  return {
    bg: token("--bg-base"),
    surface: token("--bg-surface"),
    inset: token("--bg-inset"),
    elevated: token("--bg-elevated"),
    text: token("--text-primary"),
    text2: token("--text-secondary"),
    muted: token("--text-muted"),
    border: token("--border"),
    borderStrong: token("--border-strong"),
    accent: token("--accent"),
    neutralSeries: token("--neutral-series"),
    warning: token("--warning"),
    danger: token("--danger"),
    production: token("--data-production"),
    consumption: token("--data-consumption"),
    cat: ["--cat-1", "--cat-2", "--cat-3", "--cat-4", "--cat-5", "--cat-6"].map(token),
    // cable and transformer loading: stops at 0, 50 and 85 %, flat above the rating
    load: ["--load-0", "--load-50", "--load-85"].map(token),
    loadOver: token("--load-over"),
    // voltage: diverging around 1.00 pu, blue and amber
    voltLow: token("--volt-low"),
    voltMid: token("--volt-mid"),
    voltHigh: token("--volt-high"),
    font: token("--font-sans"),
    mono: token("--font-mono"),
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

/** Loading ramp: stops at 0, 50 and 85 % of the rating (design-system.md 1.4). */
export function seqColor(tk: Tokens, t: number): string {
  const stops = tk.load;
  const x = Math.max(0, Math.min(1, t)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x));
  return mix(stops[i], stops[i + 1], x - i);
}

/** Voltage: 1.00 pu neutral, 0.94 pu blue, 1.06 pu amber. Never red and green. */
export function voltageColor(tk: Tokens, v: number, span = 0.06): string {
  const d = Math.max(-1, Math.min(1, (v - 1) / span));
  return d < 0 ? mix(tk.voltMid, tk.voltLow, -d) : mix(tk.voltMid, tk.voltHigh, d);
}

/** Net power at a house: import to the left, export to the right, on the voltage poles. */
export function powerColor(tk: Tokens, kw: number, span = 8): string {
  const d = Math.max(-1, Math.min(1, kw / span));
  return d < 0 ? mix(tk.voltMid, tk.voltLow, -d) : mix(tk.voltMid, tk.voltHigh, d);
}

/** Cable loading: the ramp up to the rating, then one flat colour. Exceeding it is binary. */
export function loadingColor(tk: Tokens, pct: number): string {
  if (pct > 100) return tk.loadOver;
  // the ramp's last stop is 85 %, and it holds from there to the rating
  return seqColor(tk, Math.min(pct, 85) / 85);
}
