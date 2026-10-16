/**
 * Loading states shaped like what is coming, so nothing jumps when it arrives.
 *
 * No shimmer: §7 says nothing pulses and nothing loops, and a flat inset block reads
 * more like an instrument warming up than like a marketing page anyway.
 */
import { useT } from "../lib/i18n";

export function Bar({ w = "100%", h = 14 }: { w?: string | number; h?: number }) {
  return <span className="sk" style={{ width: w, height: h }} />;
}

/** A chart's box, at the height the real chart will take, so the page does not reflow. */
export function ChartSkeleton({ height = 260 }: { height?: number }) {
  return <span className="sk sk-chart" style={{ height }} />;
}

export function TableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="sk-rows">
      {Array.from({ length: rows }, (_, i) => (
        <Bar key={i} w={`${92 - (i % 3) * 11}%`} />
      ))}
    </div>
  );
}

/** The scrolling screens: a title, a line of lede, then blocks. */
export function PageSkeleton({ blocks = 3 }: { blocks?: number }) {
  const t = useT();
  return (
    <div className="page" role="status" aria-label={t("ui.loading")}>
      <div className="page-inner">
        <div className="stack">
          <Bar w="22ch" h={28} />
          <Bar w="52ch" />
        </div>
        {Array.from({ length: blocks }, (_, i) => (
          <section className="block" key={i}>
            <Bar w="18ch" h={18} />
            <ChartSkeleton height={i === 0 ? 220 : 180} />
          </section>
        ))}
      </div>
    </div>
  );
}

/** The map screens: the map area, the strip under it, and the side panel. */
export function SplitSkeleton({ wide = false }: { wide?: boolean }) {
  const t = useT();
  return (
    <div className={`split ${wide ? "wide-panel" : ""}`} role="status" aria-label={t("ui.loading")}>
      <div className="map-col">
        <div className="map-wrap">
          <span className="sk sk-map" />
        </div>
      </div>
      <aside className="panel">
        <div className="stack">
          <Bar w="16ch" h={20} />
          <Bar w="100%" />
          <Bar w="80%" />
        </div>
        <div className="tiles">
          {Array.from({ length: 6 }, (_, i) => (
            <div className="tile" key={i}>
              <Bar w="9ch" h={11} />
              <Bar w="6ch" h={20} />
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}
