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
  build: (tk: Tokens, width: number) => echarts.EChartsCoreOption;
  height?: number;
  deps?: unknown[];
  onClick?: (params: any) => void;
  ariaLabel: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const theme = useStore((s) => s.theme);
  const lang = useStore((s) => s.lang);
  const renderRef = useRef<() => void>(() => {});
  renderRef.current = () => {
    const tk = tokens();
    chartRef.current?.setOption(withBase(build(tk, ref.current?.clientWidth ?? 800), tk), true);
  };

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    chartRef.current = chart;
    const ro = new ResizeObserver(() => { chart.resize(); renderRef.current(); });
    ro.observe(ref.current);
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    const onScheme = () => renderRef.current();
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

/** Shared chart chrome: hairline solid grid, muted axes, text tokens, sans typeface. */
export function withBase(opt: echarts.EChartsCoreOption, tk: Tokens): echarts.EChartsCoreOption {
  const tooltip = typeof opt.tooltip === "object" && !Array.isArray(opt.tooltip) ? opt.tooltip : {};
  return {
    ...opt,
    animation: !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    animationDuration: 300,
    textStyle: { fontFamily: tk.font, color: tk.text2, fontSize: 12, ...opt.textStyle },
    tooltip: {
      backgroundColor: tk.surface,
      borderColor: tk.border,
      borderWidth: 1,
      // a tooltip is all values, so §4.8 puts it in the mono face
      textStyle: { color: tk.text, fontFamily: tk.mono, fontSize: 12 },
      extraCssText: "box-shadow: var(--shadow-popover); border-radius: var(--radius-md);",
      confine: true,
      ...tooltip,
    },
  };
}

/** Horizontal gridlines, for the charts where a value has to be read off the axis. */
export function gridLines(tk: Tokens) {
  return { splitLine: { show: true, lineStyle: { color: tk.border, width: 1, type: "solid" as const } } };
}

export function axisStyle(tk: Tokens) {
  return {
    axisLine: { lineStyle: { color: tk.border } },
    axisTick: { show: false },
    axisLabel: { color: tk.muted, fontFamily: tk.font },
    splitLine: { show: false, lineStyle: { color: tk.border, width: 1, type: "solid" as const } },
    nameTextStyle: { color: tk.muted, fontFamily: tk.font },
  };
}
