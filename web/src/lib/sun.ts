/** Solar elevation (degrees) at a UTC time and location (NOAA simplified equations). */
export function solarElevation(dateUtcMs: number, lat: number, lon: number): number {
  const rad = Math.PI / 180;
  const d = new Date(dateUtcMs);
  const start = Date.UTC(d.getUTCFullYear(), 0, 0);
  const doy = Math.floor((dateUtcMs - start) / 86400000);
  const hours = d.getUTCHours() + d.getUTCMinutes() / 60;
  const g = ((2 * Math.PI) / 365) * (doy - 1 + (hours - 12) / 24);
  const eqtime =
    229.18 * (0.000075 + 0.001868 * Math.cos(g) - 0.032077 * Math.sin(g) - 0.014615 * Math.cos(2 * g) - 0.040849 * Math.sin(2 * g));
  const decl =
    0.006918 - 0.399912 * Math.cos(g) + 0.070257 * Math.sin(g) - 0.006758 * Math.cos(2 * g) +
    0.000907 * Math.sin(2 * g) - 0.002697 * Math.cos(3 * g) + 0.00148 * Math.sin(3 * g);
  const tst = hours * 60 + eqtime + 4 * lon;
  const ha = (tst / 4 - 180) * rad;
  const cosz = Math.sin(lat * rad) * Math.sin(decl) + Math.cos(lat * rad) * Math.cos(decl) * Math.cos(ha);
  return 90 - Math.acos(Math.max(-1, Math.min(1, cosz))) / rad;
}

/** Opacity of the night overlay for a solar elevation: dark at night, clear in daylight. */
export function nightOpacity(elevationDeg: number): number {
  if (elevationDeg >= 6) return 0;
  if (elevationDeg <= -8) return 0.58;
  return ((6 - elevationDeg) / 14) * 0.58;
}
