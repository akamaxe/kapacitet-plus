/**
 * The consumption capacity of the open project (results/consumption.json, in GET /hosting): how
 * many new EV chargers, heat pumps or constant loads the street takes, house after house, before an
 * hour of the weather record breaks a limit on the import side, and how much each house alone can
 * add. The engine computed it from the start; no screen showed it until the technical review of
 * 14-11-2026 named it the product's strongest result (H9).
 */

export interface Spread {
  median: number;
  p10: number;
  p90: number;
  min: number;
  max: number;
}

export interface Device {
  /** the rating of one device in kW; null for a heat pump, which the model sizes per house */
  device_kw: number | null;
  /** houses that have one today, and houses that could add one */
  existing: number;
  candidates: number;
  /** new devices the street takes, over the adoption orders */
  adopters: Spread;
}

export interface ConsumptionDoc {
  years: number[];
  n_orders: number;
  customers: number;
  baseline_violation_hours?: Record<string, number>;
  baseline_violates: boolean;
  devices: { ev: Device; hp: Device; flat: Device };
  nodal_kw: { timeseries: Spread; static: Spread };
}

export interface ConsumptionFigures {
  ev: Device;
  hp: Device;
  /** a constant load of the charger rating in every hour: the static study's convention */
  flat: Device;
  /** how many times the hourly study's chargers are the static convention's; null when that takes none */
  ratio: number | null;
  /** hours of the record in which today's grid already breaks an import limit */
  baselineHours: number;
  nodal: { timeseries: Spread; static: Spread };
  /** the weather years as the screen says them: one year, or the first and the last */
  span: string;
}

/** The figures the consumption section shows, each read from the document. Pure. */
export function consumptionFigures(c: ConsumptionDoc): ConsumptionFigures {
  const { ev, hp, flat } = c.devices;
  const baselineHours = Object.values(c.baseline_violation_hours ?? {}).reduce((a, b) => a + b, 0);
  const span = c.years.length === 1 ? String(c.years[0]) : `${c.years[0]}–${c.years[c.years.length - 1]}`;
  return {
    ev, hp, flat,
    ratio: flat.adopters.median > 0 ? ev.adopters.median / flat.adopters.median : null,
    baselineHours,
    nodal: c.nodal_kw,
    span,
  };
}
