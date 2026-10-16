import { keepPreviousData, useQuery } from "@tanstack/react-query";

export const API = "/api/v1";

/**
 * A failure that still knows what it was asking for. §7 wants an error to name the
 * source, and the plain Error this used to throw lost the path on the way up, so no
 * screen could say which endpoint had let it down.
 */
export class ApiError extends Error {
  constructor(
    readonly path: string,
    readonly status: number,
    readonly detail: string,
  ) {
    super(`${status} ${API}${path}${detail ? ` — ${detail}` : ""}`);
    this.name = "ApiError";
  }

  /** `GET /api/v1/hosting` — what the user is shown, and what a bug report needs. */
  get endpoint() {
    return `GET ${API}${this.path}`;
  }
}

export async function getJSON<T = any>(path: string): Promise<T> {
  let r: Response;
  try {
    r = await fetch(`${API}${path}`);
  } catch (e) {
    // the server is down, or the network is: status 0 says "never answered"
    throw new ApiError(path, 0, (e as Error)?.message ?? "");
  }
  if (!r.ok) throw new ApiError(path, r.status, (await r.text()).slice(0, 160));
  return r.json();
}

/** Results are static once computed: cache them for the whole session. */
export function useApi<T = any>(path: string | null, opts: { refetchMs?: number } = {}) {
  return useQuery<T>({
    queryKey: [path],
    queryFn: () => getJSON<T>(path as string),
    enabled: !!path,
    staleTime: opts.refetchMs ?? Infinity,
    refetchInterval: opts.refetchMs,
    retry: 1,
  });
}

export interface StateHeader {
  name: string;
  hours: number;
  n_bus: number;
  n_line: number;
  n_cust: number;
  v_offset: number;
  v_scale: number;
  loading_scale: number;
  p_offset: number;
  p_scale: number;
  bus_index: number[];
  line_index: number[];
  aggregates: {
    trafo_loading: number[];
    trafo_p_kw: number[];
    vmax: number[];
    vmin: number[];
    max_line_loading: number[];
    violation: number[];
    pv_kw: number[];
    load_kw: number[];
    curtailed_kw: number[] | null;
  };
}

export interface GridState {
  header: StateHeader;
  bytes: Uint8Array;
  voltage: (busRow: number, hour: number) => number;
  loading: (lineRow: number, hour: number) => number;
  power: (cid: number, hour: number) => number;
}

/** Full-year grid state of a scenario: uint8 blocks, time-major (see engine/run.py write_state). */
export async function loadState(name: string): Promise<GridState> {
  const [header, buf] = await Promise.all([
    getJSON<StateHeader>(`/state/${name}`),
    fetch(`${API}/state/${name}/bin`).then((r) => {
      if (!r.ok) throw new Error(`state ${name}: ${r.status}`);
      return r.arrayBuffer();
    }),
  ]);
  const bytes = new Uint8Array(buf);
  const H = header.hours;
  const nb = header.n_bus;
  const nl = header.n_line;
  const nc = header.n_cust;
  const offL = H * nb;
  const offP = offL + H * nl;
  return {
    header,
    bytes,
    voltage: (b, h) => header.v_offset + bytes[h * nb + b] * header.v_scale,
    loading: (l, h) => bytes[offL + h * nl + l] * header.loading_scale,
    power: (c, h) => header.p_offset + bytes[offP + h * nc + c] * header.p_scale,
  };
}

export function useGridState(name: string) {
  return useQuery<GridState>({
    queryKey: ["state", name],
    queryFn: () => loadState(name),
    staleTime: Infinity,
    // keep showing the previous scenario while the next one downloads (no layout jump)
    placeholderData: keepPreviousData,
  });
}
