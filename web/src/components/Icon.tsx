const paths = {
  national: "M12 3l4 3-1 5 3 4-5 6-4-3 1-5-3-3 5-7Z",
  twin: "m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5",
  compare: "M4 20h16M7 15V9M12 15V4M17 15V7",
  request: "M7 3v5m10-5v5M5 8h14v3a7 7 0 0 1-7 7v3m-7-13v3a7 7 0 0 0 7 7",
  operation: "M4 5h16v15H4V5Zm0 5h16M8 3v4m8-4v4m-8 7h3v3H8v-3",
  scenarios: "M4 19 10 13l4 3 6-11M14 5h6v6",
  method: "M9 4H5v16h14V4h-4M9 3h6v4H9V3Zm0 9h6m-6 4h6",
  report: "M6 3h9l4 4v14H6V3Zm9 0v4h4M9 17v-3m3 3v-6m3 6v-4",
  arrow: "M4 12h16m-6-6 6 6-6 6",
  notes: "M5 3h14v18H5V3Zm4 5h6m-6 4h6m-6 4h3",
  expand: "M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5",
  close: "m6 6 12 12M6 18 18 6",
  menu: "M4 7h16M4 12h16M4 17h16",
  play: "m8 4 12 8-12 8V4Z",
  pause: "M8 4v16M16 4v16",
  previous: "m14 6-6 6 6 6",
  next: "m10 6 6 6-6 6",
  reset: "M4 10a8 8 0 1 1 1 8M4 4v6h6",
  sun: "M12 2v2m0 16v2M2 12h2m16 0h2M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  check: "m5 12 4 4L19 6",
  alert: "M12 8v5m0 3h.01M12 3 2 20h20L12 3Z",
  search: "M11 19a8 8 0 1 1 0-16 8 8 0 0 1 0 16Zm6.5-1.5L21 21",
  sort: "M8 9 12 5l4 4M8 15l4 4 4-4",
  copy: "M9 9h10v10H9V9Zm-4 6V5h10",
  download: "M12 4v10m-4-4 4 4 4-4M5 19h14",
  upload: "M12 20V10m-4 4 4-4 4 4M5 5h14",
} as const;

export function Icon({ name, size = 20 }: { name: keyof typeof paths; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
