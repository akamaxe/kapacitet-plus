import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import { tokens, type Tokens } from "../lib/colors";
import { useStore } from "../lib/store";

/**
 * ECharts wrapper. `build` receives the active design tokens so every chart follows the theme;
 * the chart is rebuilt when the theme or language changes and resized with its container.
 */
export function Chart({
  build,
  height = 260,
  deps = [],
  onClick,
  ariaLabel,
}: {
  build: (tk: Tokens) => echarts.EChartsCoreOption;
  height?: number;
  deps?: unknown[];
  onClick?: (params: any) => void;
  ariaLabel: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const theme = useStore((s) => s.theme);
  const lang = useStore((s) => s.lang);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    chartRef.current = chart;
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    const onScheme = () => chart.setOption(withBase(build(tokens()), tokens()), true);
    mq?.addEventListener?.("change", onScheme);
    return () => {
      ro.disconnect();
      mq?.removeEventListener?.("change", onScheme);
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    // tokens are read after the theme attribute has been applied
    requestAnimationFrame(() => chart.setOption(withBase(build(tokens()), tokens()), true));
  }, [theme, lang, ...deps]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !onClick) return;
    chart.off("click");
    chart.on("click", onClick);
  }, [onClick]);

  return <div ref={ref} className="chart" style={{ height }} role="img" aria-label={ariaLabel} />;
}

/** Shared chart chrome: hairline solid grid, muted axes, text tokens, sans typeface. */
export function withBase(opt: echarts.EChartsCoreOption, tk: Tokens): echarts.EChartsCoreOption {
  return {
    animationDuration: 300,
    textStyle: { fontFamily: tk.font, color: tk.ink2, fontSize: 12 },
    tooltip: {
      backgroundColor: tk.surface,
      borderColor: tk.hairline,
      borderWidth: 1,
      textStyle: { color: tk.ink, fontFamily: tk.font, fontSize: 12.5 },
      extraCssText: "box-shadow: 0 4px 16px rgba(16,37,48,0.12); border-radius: 8px;",
      confine: true,
    },
    ...opt,
  };
}

export function axisStyle(tk: Tokens) {
  return {
    axisLine: { lineStyle: { color: tk.baseline } },
    axisTick: { show: false },
    axisLabel: { color: tk.muted, fontFamily: tk.font },
    splitLine: { lineStyle: { color: tk.hairline, width: 1, type: "solid" as const } },
    nameTextStyle: { color: tk.muted, fontFamily: tk.font },
  };
}
