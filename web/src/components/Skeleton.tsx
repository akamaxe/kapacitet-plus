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

/**
 * The scrolling screens: the section bar when the screen has one, the opening answer in its
 * two columns when it has one, then blocks at the heights their charts take.
 */
export function PageSkeleton({ blocks = 3, bar = false, hero = false }: { blocks?: number; bar?: boolean; hero?: boolean }) {
  const t = useT();
  return (
    <div className="page" role="status" aria-label={t("ui.loading")}>
      {bar && (
        <div className="screen-bar">
          <div className="screen-sections">
            {[9, 11, 12, 9, 10].map((w, i) => <Bar key={i} w={`${w}ch`} h={12} />)}
          </div>
        </div>
      )}
      <div className="page-inner">
        {hero ? (
          <section className="overview-hero">
            <div className="overview-copy">
              <Bar w="20ch" h={30} />
              <Bar w="46ch" />
              <Bar w="16ch" h={40} />
              <Bar w="100%" h={60} />
            </div>
            <span className="sk sk-plan" />
          </section>
        ) : (
          <div className="stack">
            <Bar w="22ch" h={28} />
            <Bar w="52ch" />
          </div>
        )}
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
