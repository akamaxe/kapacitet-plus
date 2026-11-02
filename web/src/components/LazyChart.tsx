/**
 * The chart, loaded when it mounts. The opening screen renders its words and figures first and
 * fetches the chart library (some 200 kB) beside them, behind a skeleton of the chart's own height,
 * so nothing moves when it arrives.
 */
import { lazy, Suspense } from "react";
import type { ChartProps } from "./Chart";
import { ChartSkeleton } from "./Skeleton";

const Inner = lazy(() => import("./Chart").then((m) => ({ default: m.Chart })));

export function Chart(props: ChartProps) {
  return (
    <Suspense fallback={<ChartSkeleton height={props.height ?? 260} />}>
      <Inner {...props} />
    </Suspense>
  );
}
