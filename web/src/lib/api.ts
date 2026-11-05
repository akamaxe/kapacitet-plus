import { keepPreviousData, useQuery } from "@tanstack/react-query";

export const API = "/api/v1";

/**
 * A failure that still knows what it was asking for. §7 wants an error to name the
 * source, and the plain Error this used to throw lost the path on the way up, so no
 * screen could say which endpoint had let it down.
 */
export class ApiError extends Error {
  /** what the server said, when it said it in the structured form (`not_computed`) */
  readonly info: { code?: string; file?: string; command?: string; message?: string } | null;

  constructor(
    readonly path: string,
    readonly status: number,
    readonly detail: string,
  ) {
    super(`${status} ${API}${path}${detail ? ` — ${detail}` : ""}`);
    this.name = "ApiError";
    let info = null;
    try {
      const body = JSON.parse(detail);
      info = typeof body?.detail === "object" ? body.detail : typeof body?.detail === "string" ? { message: body.detail } : null;
    } catch {
      /* not JSON: the plain text stays in `detail` */
    }
    this.info = info;
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
  if (!r.ok) throw new ApiError(path, r.status, (await r.text()).slice(0, 600));
  return r.json();
}

export type WriteMethod = "POST" | "PUT" | "DELETE";

/**
 * Every write the interface makes: a JSON body, `{}` when there is nothing to say. The server
 * (api/guard.py) takes a write only as JSON, so a page on another site, which cannot send JSON
 * without asking first, cannot make one. It rejects like `fetch` when the server never answers.
 * The one exception is the setup page's upload of bytes (lib/upload.ts).
 */
export function apiWrite(path: string, body: unknown = {}, method: WriteMethod = "POST", headers: Record<string, string> = {}): Promise<Response> {
  return fetch(`${API}${path}`, {
    method,
    headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
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

/**
 * Read a year of grid state: uint8 blocks, time-major, with the scales in the header. The other
 * half of the contract is engine/stateblob.py; tests/fixtures/state_tiny.json pins both.
 */
export function decodeState(header: StateHeader, buf: ArrayBuffer | Uint8Array): GridState {
  const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
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

/** Full-year grid state of a scenario, fetched and decoded. */
export async function loadState(name: string): Promise<GridState> {
  const [header, buf] = await Promise.all([
    getJSON<StateHeader>(`/state/${name}`),
    fetch(`${API}/state/${name}/bin`).then((r) => {
      if (!r.ok) throw new Error(`state ${name}: ${r.status}`);
      return r.arrayBuffer();
    }),
  ]);
  return decodeState(header, buf);
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
