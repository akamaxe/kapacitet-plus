/**
 * The chart chrome option builders share, without the chart library: a screen that builds options
 * can import this and still load ECharts only when a chart mounts (components/LazyChart.tsx).
 */
import type { Tokens } from "./colors";

/** Hairline axis, no ticks, tick labels at caption size in the text face. */
export function axisStyle(tk: Tokens) {
  return {
    axisLine: { lineStyle: { color: tk.border } },
    axisTick: { show: false },
    axisLabel: { color: tk.muted, fontFamily: tk.font, fontSize: tk.size.caption },
    splitLine: { show: false, lineStyle: { color: tk.border, width: 1, type: "solid" as const } },
    nameTextStyle: { color: tk.muted, fontFamily: tk.font, fontSize: tk.size.caption },
  };
}
