import { useEffect, useRef } from "react";
// Only what the screens draw is registered, so the chart chunk carries no sankey, gauge, geo
// or toolbox code (bundle-barrel-imports): bar, line, scatter, heatmap and one custom series,
// with their grid, tooltip (which brings the axis pointer), markLine, the text graphic and
// both kinds of visualMap.
import * as echarts from "echarts/core";
import { BarChart, CustomChart, HeatmapChart, LineChart, ScatterChart } from "echarts/charts";
import {
  GraphicComponent,
  GridComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapContinuousComponent,
  VisualMapPiecewiseComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { tokens, type Tokens } from "../lib/colors";
import { useStore } from "../lib/store";

echarts.use([
  BarChart, LineChart, ScatterChart, HeatmapChart, CustomChart,
  GridComponent, TooltipComponent, MarkLineComponent, GraphicComponent,
  VisualMapContinuousComponent, VisualMapPiecewiseComponent,
  CanvasRenderer,
]);

/** The width under which the option builders switch to their compact layout. */
const COMPACT = 520;

// One listener for the desktop's colour scheme, however many charts are mounted
// (client-event-listeners): under "system", a scheme change is a theme change.
const onScheme = new Set<() => void>();
let schemeQuery: MediaQueryList | null = null;
function watchScheme(cb: () => void): () => void {
  if (!schemeQuery && window.matchMedia) {
    schemeQuery = window.matchMedia("(prefers-color-scheme: dark)");
    schemeQuery.addEventListener?.("change", () => onScheme.forEach((f) => f()));
  }
  onScheme.add(cb);
  return () => onScheme.delete(cb);
}

/**
 * ECharts wrapper. `build` receives the active design tokens so every chart follows the theme;
 * the chart is rebuilt when the theme, the language or its data change, and only resized when
 * its container does — unless the width crosses the compact layout's threshold.
 */
export function Chart({
  build,
  height = 260,
  deps = [],
  onClick,
  ariaLabel,
}: {
  build: (tk: Tokens, width: number) => echarts.EChartsCoreOption;
  height?: number;
  deps?: unknown[];
  onClick?: (params: any) => void;
  ariaLabel: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const drawn = useRef(false);
  const theme = useStore((s) => s.theme);
  const lang = useStore((s) => s.lang);
  const renderRef = useRef<() => void>(() => {});
  renderRef.current = () => {
    const chart = chartRef.current;
    if (!chart) return;
    const tk = tokens();
    // §4.11: a chart draws itself once; a theme, language or layout change just redraws it
    chart.setOption(withBase(build(tk, ref.current?.clientWidth ?? 800), tk, !drawn.current), true);
    drawn.current = true;
  };

  useEffect(() => {
    if (!ref.current) return;
    const el = ref.current;
    const chart = echarts.init(el, undefined, { renderer: "canvas" });
    chartRef.current = chart;
    let compact = el.clientWidth < COMPACT;
    const ro = new ResizeObserver(() => {
      chart.resize();
      const now = el.clientWidth < COMPACT;
      if (now !== compact) {
        compact = now;
        renderRef.current();
      }
    });
    ro.observe(el);
    const unwatch = watchScheme(() => renderRef.current());
    return () => {
      ro.disconnect();
      unwatch();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!chartRef.current) return;
    // tokens are read after the theme attribute has been applied
    const frame = requestAnimationFrame(() => renderRef.current());
    return () => cancelAnimationFrame(frame);
  }, [theme, lang, ...deps]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !onClick) return;
    chart.off("click");
    chart.on("click", onClick);
  }, [onClick]);

  return <div ref={ref} className="chart" style={{ height }} role="img" aria-label={ariaLabel} />;
}

/** Shared chart chrome: hairline grid, muted mono ticks, ink text, a tooltip that floats. */
export function withBase(opt: echarts.EChartsCoreOption, tk: Tokens, animate = false): echarts.EChartsCoreOption {
  const tooltip = typeof opt.tooltip === "object" && !Array.isArray(opt.tooltip) ? opt.tooltip : {};
  return {
    ...opt,
    animation: animate && !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    animationDuration: 200,
    animationEasing: "cubicOut",
    textStyle: { fontFamily: tk.font, color: tk.text2, fontSize: tk.size.small, ...opt.textStyle },
    tooltip: {
      backgroundColor: tk.elevated,
      borderWidth: 0,
      padding: [tk.size.space2, tk.size.space3],
      // a tooltip is all values, so §4.8 puts it in the mono face
      textStyle: { color: tk.text, fontFamily: tk.mono, fontSize: tk.size.caption },
      extraCssText: "box-shadow: var(--shadow-popover); border-radius: var(--radius-sm);",
      confine: true,
      ...tooltip,
    },
  };
}

/** Horizontal gridlines, for the charts where a value has to be read off the axis. */
export function gridLines(tk: Tokens) {
  return { splitLine: { show: true, lineStyle: { color: tk.border, width: 1, type: "solid" as const } } };
}

/** §4.3: hairline axis, no ticks, mono tick labels at caption size. */
export function axisStyle(tk: Tokens) {
  return {
    axisLine: { lineStyle: { color: tk.border } },
    axisTick: { show: false },
    axisLabel: { color: tk.muted, fontFamily: tk.mono, fontSize: tk.size.caption },
    splitLine: { show: false, lineStyle: { color: tk.border, width: 1, type: "solid" as const } },
    nameTextStyle: { color: tk.muted, fontFamily: tk.mono, fontSize: tk.size.caption },
  };
}
