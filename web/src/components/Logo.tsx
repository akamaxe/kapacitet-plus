/**
 * The product's logo: the K whose arms open for the plus, then the name. It is drawn, not set in
 * a typeface, so it comes as the brand kit's own files (web/public/brand, copied from the kit's
 * masters) and is never rebuilt from text.
 *
 * `reversed` is the cut for the blue bar and for a dark ground: white, with strokes a little
 * thinner. `color` is navy for a white ground and for print. `ground` follows the theme.
 */
const FILES = {
  // each master's own width over a height of 256
  color: { src: "/brand/kapacitet-logo-color.svg", ratio: 1166 / 256 },
  reversed: { src: "/brand/kapacitet-logo-reversed.svg", ratio: 1164 / 256 },
} as const;

export const PRODUCT_NAME = "Kapacitet+";

function Cut({ tone, height, className }: { tone: keyof typeof FILES; height: number; className?: string }) {
  const file = FILES[tone];
  return (
    <img
      className={className ? `logo ${className}` : "logo"}
      src={file.src}
      alt={PRODUCT_NAME}
      height={height}
      width={Math.round(height * file.ratio)}
      decoding="async"
      draggable={false}
    />
  );
}

export function Logo({ tone, height = 22 }: { tone: "color" | "reversed" | "ground"; height?: number }) {
  if (tone !== "ground") return <Cut tone={tone} height={height} />;
  // both cuts, and the theme shows one (app.css: .logo-day, .logo-night)
  return (
    <>
      <Cut tone="color" height={height} className="logo-day" />
      <Cut tone="reversed" height={height} className="logo-night" />
    </>
  );
}
