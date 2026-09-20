const paths = {
  grid: "M4 8h16M4 16h16M8 4v16M16 4v16M9 9l6 6M15 9l-6 6",
  national: "M12 3l4 3-1 5 3 4-5 6-4-3 1-5-3-3 5-7Z",
  twin: "m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5",
  compare: "M4 20h16M7 15V9M12 15V4M17 15V7",
  request: "M7 3v5m10-5v5M5 8h14v3a7 7 0 0 1-7 7v3m-7-13v3a7 7 0 0 0 7 7",
  scenarios: "M4 19 10 13l4 3 6-11M14 5h6v6",
  method: "M9 4H5v16h14V4h-4M9 3h6v4H9V3Zm0 9h6m-6 4h6",
  arrow: "M4 12h16m-6-6 6 6-6 6",
  notes: "M5 3h14v18H5V3Zm4 5h6m-6 4h6m-6 4h3",
  expand: "M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5",
  close: "m6 6 12 12M6 18 18 6",
  play: "m8 4 12 8-12 8V4Z",
  pause: "M8 4v16M16 4v16",
  previous: "m14 6-6 6 6 6",
  next: "m10 6 6 6-6 6",
  reset: "M4 10a8 8 0 1 1 1 8M4 4v6h6",
  bolt: "m13 2-9 12h7l-1 8 10-13h-8l1-7Z",
  sun: "M12 2v2m0 16v2M2 12h2m16 0h2M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  check: "m5 12 4 4L19 6",
} as const;

export function Icon({ name, size = 20 }: { name: keyof typeof paths; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
