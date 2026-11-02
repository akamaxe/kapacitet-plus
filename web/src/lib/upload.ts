/**
 * Send a data bundle to the setup page's upload (api/setup.py), then follow it until the server
 * has unpacked it and started again on the data.
 *
 * The file goes in chunks of a few MB, each a PUT with its byte offset, so no single request is
 * large (a codespace's port forwarding sits in between) and an upload that stops — a dropped
 * connection, a reloaded page — carries on from the bytes the server already holds. When a chunk
 * arrives out of step the server names its position (409 `offset_mismatch`) and the upload
 * resumes from there.
 */
import { API } from "./api";
import { refusalOf } from "./errors";

export const CHUNK = 8 * 2 ** 20;
const RETRIES = 4;
const RESYNCS = 8;

export interface UploadStatus {
  open: boolean;
  state: "idle" | "receiving" | "unpacking" | "done" | "failed";
  received: number;
  total: number | null;
  fp: string | null;
  files_done: number;
  files_total: number;
  error: { code: string; params?: Record<string, string | number> } | null;
  restart: "auto" | "manual" | null;
}

/** A refusal with the server's code (`err.<code>` in the dictionaries), or `stopped` when the
 * connection gave out. */
export class UploadError extends Error {
  constructor(readonly code: string, readonly params: Record<string, string | number> = {}, message = code) {
    super(message);
    this.name = "UploadError";
  }
}

interface Deps {
  fetch?: typeof fetch;
  wait?: (ms: number) => Promise<void>;
  chunk?: number;
}

const sleep = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));

/** Which file this is, so a second tab cannot interleave another file's bytes. */
export function fingerprint(file: File): string {
  return `${file.name}|${file.size}|${file.lastModified}`;
}

export async function uploadStatus(deps: Deps = {}): Promise<UploadStatus> {
  const r = await (deps.fetch ?? fetch)(`${API}/setup/upload`);
  if (!r.ok) throw new UploadError("stopped", {}, String(r.status));
  return r.json();
}

/** Send `file`; `onProgress(sent, total)` follows the bytes the server holds. Resolves with the
 * status the last chunk returned (unpacking, usually). */
export async function sendBundle(file: File, onProgress: (sent: number, total: number) => void, deps: Deps = {}): Promise<UploadStatus> {
  const f = deps.fetch ?? fetch;
  const wait = deps.wait ?? sleep;
  const chunk = deps.chunk ?? CHUNK;
  const fp = fingerprint(file);
  const total = file.size;
  if (total === 0) throw new UploadError("not_a_bundle");
  // the same file half-sent before, from a page that was reloaded: carry on where it stopped
  let offset = 0;
  try {
    const st = await uploadStatus(deps);
    if (st.state === "receiving" && st.total === total && st.fp === fp) offset = st.received;
  } catch {
    /* the first chunk will say what is wrong */
  }
  onProgress(offset, total);
  let failures = 0;
  let resyncs = 0;
  for (;;) {
    const body = file.slice(offset, Math.min(offset + chunk, total));
    const url = `${API}/setup/upload?offset=${offset}&total=${total}&fp=${encodeURIComponent(fp)}`;
    const r = await f(url, { method: "PUT", headers: { "Content-Type": "application/octet-stream" }, body }).catch(() => null);
    if (!r || (r.status >= 500 && r.status !== 507)) {
      // the connection dropped, or the proxy in between failed: wait, then ask where the server got to
      if (++failures > RETRIES) throw new UploadError("stopped");
      await wait(1000 * 2 ** (failures - 1));
      try {
        const st = await uploadStatus(deps);
        // the last chunk did arrive: the answer was what got lost
        if (st.state === "unpacking" || st.state === "done" || st.state === "failed") return st;
        offset = st.state === "receiving" && st.total === total && st.fp === fp ? st.received : 0;
      } catch {
        /* keep the offset and try again */
      }
      continue;
    }
    if (r.ok) {
      failures = 0;
      const st: UploadStatus = await r.json();
      if (st.state !== "receiving") {
        onProgress(total, total);
        return st;
      }
      offset = st.received;
      onProgress(offset, total);
      continue;
    }
    const d = refusalOf(await r.json().catch(() => null));
    if (r.status === 409 && d.code === "offset_mismatch" && ++resyncs <= RESYNCS) {
      offset = Number(d.params?.received ?? 0);
      onProgress(offset, total);
      continue;
    }
    throw new UploadError(d.code ?? "stopped", d.params ?? {}, d.message ?? String(r.status));
  }
}

/** Follow the unpacking; `onFiles(done, total)` counts the files written. Resolves when it is done. */
export async function waitUnpacked(onFiles: (done: number, total: number) => void, deps: Deps = {}): Promise<UploadStatus> {
  const wait = deps.wait ?? sleep;
  let misses = 0;
  for (;;) {
    let st: UploadStatus | null = null;
    try {
      st = await uploadStatus(deps);
      misses = 0;
    } catch {
      // the server restarting after it finished is a moment without answers
      if (++misses > 30) throw new UploadError("stopped");
    }
    if (st) {
      if (st.state === "done") return st;
      if (st.state === "failed") throw new UploadError(st.error?.code ?? "unpack_failed", st.error?.params ?? {});
      if (st.state === "unpacking") onFiles(st.files_done, st.files_total);
      // already restarted on the data: the upload is closed because the results are there
      else if (!st.open) return { ...st, state: "done", restart: "auto" };
      else throw new UploadError("stopped");
    }
    await wait(700);
  }
}

/** Wait for the server to come back as a new process, then reload: every cached answer on the
 * page came from the copy without data. Throws if it never does (a server without supervisor). */
export async function reloadAfterRestart(boot: string | undefined, deps: Deps = {}, reload = () => window.location.reload()): Promise<void> {
  const f = deps.fetch ?? fetch;
  const wait = deps.wait ?? sleep;
  for (let i = 0; i < 180; i++) {
    await wait(1000);
    const r = await f(`${API}/health`).catch(() => null);
    const h = r?.ok ? await r.json().catch(() => null) : null;
    if (h?.boot && h.boot !== boot) {
      reload();
      return;
    }
  }
  throw new UploadError("restart_timeout");
}
