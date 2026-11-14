/**
 * What the screens conclude from the open project's results, as pure functions: a verdict, the
 * limit that binds, which words a note takes, where a curve's labels go.
 *
 * The screens were written for the reference network, where the transformer binds and the
 * envelopes add capacity. On a network where every house fits they said "Fail" next to 0 h, named
 * a transformer that limits nothing, called the grid full and piled every label on one point
 * (H11 of the technical review of 14-11-2026). Each conclusion is now drawn from the figures it
 * stands beside, and tested here on both kinds of network.
 */

/** showcase.verification, as far as these conclusions read it. */
export interface Verification {
  uncontrolled_violation_hours: number;
  uncontrolled_overvoltage_hours?: number;
  uncontrolled_trafo_overload_hours?: number;
  uncontrolled_line_overload_hours?: number;
  uncontrolled_max_voltage_pu: number;
  uncontrolled_max_trafo_loading_pct?: number;
}

export interface Sentence {
  key: string;
  vars: { h?: number; v?: number; load?: number };
}

/** "Pass" when the same houses without control break no limit in any hour: 0 h is not a failure. */
export function uncontrolledPasses(v: Verification): boolean {
  return v.uncontrolled_violation_hours === 0;
}

/**
 * The limit the same houses break without control: the one broken in the most hours (the
 * transformer, a cable or the voltage), or, when none is, that nothing binds and how close the
 * network comes. Results older than the breakdown by limit keep a sentence that names none.
 */
export function bindingLimit(v: Verification): Sentence {
  if (!v.uncontrolled_violation_hours) {
    return v.uncontrolled_max_trafo_loading_pct == null
      ? { key: "cmp.binding.noneV", vars: { v: v.uncontrolled_max_voltage_pu } }
      : { key: "cmp.binding.none", vars: { v: v.uncontrolled_max_voltage_pu, load: v.uncontrolled_max_trafo_loading_pct } };
  }
  const hours: [string, number][] = [
    ["trafo", v.uncontrolled_trafo_overload_hours ?? 0],
    ["line", v.uncontrolled_line_overload_hours ?? 0],
    ["voltage", v.uncontrolled_overvoltage_hours ?? 0],
  ];
  const [which, h] = hours.reduce((best, x) => (x[1] > best[1] ? x : best));
  if (h <= 0) return { key: "cmp.binding.some", vars: { h: v.uncontrolled_violation_hours, v: v.uncontrolled_max_voltage_pu } };
  return { key: `cmp.binding.${which}`, vars: { h, v: v.uncontrolled_max_voltage_pu } };
}

/**
 * The note under the static baseline of a request: the grid is "full for that method" only when
 * the static study does not connect every house.
 */
export function staticNoteKey(firm: number, customers: number | null | undefined): string {
  return customers != null && firm >= customers ? "req.base.staticNoteAll" : "req.base.staticNote";
}

export type MarkPosition = "insideEndTop" | "insideStartTop";

/**
 * The vertical marks of a curve (the static, hourly and envelope medians): marks at the same
 * kilowatt share one label, and a mark close to the one before it takes the other end of the
 * line, so no label covers another. `span` is the width of the axis, in the marks' unit.
 */
export function curveMarks(marks: { x: number; name: string }[], span: number): { x: number; name: string; position: MarkPosition }[] {
  const out: { x: number; name: string; position: MarkPosition }[] = [];
  for (const m of [...marks].sort((a, b) => a.x - b.x)) {
    const last = out[out.length - 1];
    if (last && Math.abs(m.x - last.x) < 0.5) {
      last.name = `${last.name} = ${m.name}`;
      continue;
    }
    const close = !!last && Math.abs(m.x - last.x) < Math.max(span, 1) * 0.06;
    out.push({ x: m.x, name: m.name, position: close && last.position === "insideEndTop" ? "insideStartTop" : "insideEndTop" });
  }
  return out;
}
