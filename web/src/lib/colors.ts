/** Colour helpers that read the design tokens, so charts and maps follow the active theme. */

export function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** All the tokens a chart or map needs, read from one computed style. */
function readTokens() {
  const cs = getComputedStyle(document.documentElement);
  const tk = (name: string) => cs.getPropertyValue(name).trim();
  // the type and spacing tokens are in rem; charts draw on a canvas and need pixels
  const rem = parseFloat(cs.fontSize) || 16;
  const px = (name: string) => Math.round(parseFloat(tk(name)) * rem);
  return {
    bg: tk("--bg-base"),
    surface: tk("--bg-surface"),
    inset: tk("--bg-inset"),
    elevated: tk("--bg-elevated"),
    text: tk("--text-primary"),
    text2: tk("--text-secondary"),
    muted: tk("--text-muted"),
    border: tk("--border"),
    borderStrong: tk("--border-strong"),
    // the one action and selection (SL blue); data never takes it
    accent: tk("--accent"),
    accentQuiet: tk("--accent-quiet"),
    // the envelope side, firm status and the main single series of a chart
    dynamic: tk("--series-dynamic"),
    main: tk("--series-main"),
    good: tk("--good"),
    goodFill: tk("--good-fill"),
    goodQuiet: tk("--good-quiet"),
    neutralSeries: tk("--neutral-series"),
    warning: tk("--warning"),
    warningQuiet: tk("--warning-quiet"),
    warningFill: tk("--warning-fill"),
    danger: tk("--danger"),
    dangerQuiet: tk("--danger-quiet"),
    dangerFill: tk("--danger-fill"),
    production: tk("--data-production"),
    consumption: tk("--data-consumption"),
    cat: ["--cat-1", "--cat-2", "--cat-3", "--cat-4", "--cat-5", "--cat-6"].map(tk),
    // cable and transformer loading: stops at 0, 50 and 85 %, flat above the rating
    load: ["--load-0", "--load-50", "--load-85"].map(tk),
    loadOver: tk("--load-over"),
    // voltage: diverging around 1.00 pu, blue and amber
    voltLow: tk("--volt-low"),
    voltMid: tk("--volt-mid"),
    voltHigh: tk("--volt-high"),
    font: tk("--font-sans"),
    // §2's chart scale: ticks and sources, labels, values on a chart, the one annotated figure
    size: {
      caption: px("--text-caption"),
      small: px("--text-small"),
      body: px("--text-body"),
      h3: px("--text-h3"),
      space2: px("--space-2"),
      space3: px("--space-3"),
      radius: parseFloat(tk("--radius-sm")) || 3,
    },
  };
}

export type Tokens = ReturnType<typeof readTokens>;

let cached: { key: string; value: Tokens } | null = null;

/**
 * The tokens of the theme on screen. They only change with the theme attribute, or with the
 * desktop's scheme when no theme is chosen, so they are read once per theme rather than once
 * per row, cell and hour (js-cache-function-results).
 */
export function tokens(): Tokens {
  const attr = document.documentElement.getAttribute("data-theme");
  const key = attr ?? (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "system-dark" : "system-light");
  if (cached?.key !== key) cached = { key, value: readTokens() };
  return cached.value;
}

const rgbCache = new Map<string, [number, number, number]>();

function hexToRgb(hex: string): [number, number, number] {
  let rgb = rgbCache.get(hex);
  if (!rgb) {
    const h = hex.replace("#", "");
    const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
    rgb = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    rgbCache.set(hex, rgb);
  }
  return rgb;
}

function mix(a: string, b: string, t: number): string {
  const [r1, g1, b1] = hexToRgb(a);
  const [r2, g2, b2] = hexToRgb(b);
  const c = (x: number, y: number) => Math.round(x + (y - x) * t);
  return `rgb(${c(r1, r2)},${c(g1, g2)},${c(b1, b2)})`;
}

/**
 * A legend bar drawn from the colour function the marks use, so the key always matches them.
 * `color` takes the position along the bar, from 0 at the left to 1 at the right.
 */
export function rampCss(color: (t: number) => string): string {
  // a colour-scale key, drawn from the same function as the marks it explains
  return `linear-gradient(90deg, ${Array.from({ length: 9 }, (_, k) => color(k / 8)).join(", ")})`; // taste-skill-ignore
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

/**
 * A zone's balance, from using more than it makes (-1, the consumption amber) to making
 * more than it uses (+1, the production blue), with the page colour at zero.
 */
export function balanceColor(tk: Tokens, t: number): string {
  const d = Math.max(-1, Math.min(1, t));
  const base = tk.surface.startsWith("#") ? tk.surface : "#ffffff";
  return d < 0 ? mix(base, tk.consumption, 0.2 + 0.8 * -d) : mix(base, tk.production, 0.2 + 0.8 * d);
}
