import { describe, expect, it } from "vitest";
import { fingerprint, reloadAfterRestart, sendBundle, UploadError, waitUnpacked, type UploadStatus } from "./upload";

/** A stand-in for api/setup.py: it keeps the bytes it was sent and answers like the server. */
function fakeServer(opts: { failFirstPut?: number; refuse?: { status: number; code: string; params?: object } } = {}) {
  const got: number[] = [];
  let received = 0;
  let total = 0;
  let fp = "";
  let failures = opts.failFirstPut ?? 0;
  const status = (state: UploadStatus["state"] = received && received < total ? "receiving" : "idle"): UploadStatus => ({
    open: true, state, received, total, fp, files_done: 0, files_total: 0, error: null, restart: null,
  });
  const json = (body: unknown, code = 200) => new Response(JSON.stringify(body), { status: code, headers: { "Content-Type": "application/json" } });
  const fetchFn = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://x");
    if (!init || init.method !== "PUT") return json(status());
    if (failures > 0) {
      failures--;
      throw new TypeError("network down");
    }
    if (opts.refuse) return json({ detail: { code: opts.refuse.code, message: "no", params: opts.refuse.params ?? {} } }, opts.refuse.status);
    const offset = Number(url.searchParams.get("offset"));
    const body = new Uint8Array(await (init.body as Blob).arrayBuffer());
    if (offset === 0) {
      received = 0;
      total = Number(url.searchParams.get("total"));
      fp = url.searchParams.get("fp") ?? "";
    }
    if (offset !== received) return json({ detail: { code: "offset_mismatch", message: "", params: { received } } }, 409);
    got.push(...body);
    received += body.length;
    return json(status(received === total ? "unpacking" : "receiving"));
  }) as typeof fetch;
  return { fetchFn, got, set: (r: number, t: number, f: string) => { received = r; total = t; fp = f; } };
}

const file = (n: number) => new File([new Uint8Array(Array.from({ length: n }, (_, i) => i % 251))], "kapacitet-data.zip", { lastModified: 7 });
const noWait = async () => undefined;

describe("sendBundle", () => {
  it("sends the file in chunks, in order, and reports the bytes the server holds", async () => {
    const server = fakeServer();
    const seen: number[] = [];
    const st = await sendBundle(file(25), (sent) => seen.push(sent), { fetch: server.fetchFn, chunk: 10, wait: noWait });
    expect(st.state).toBe("unpacking");
    expect(server.got).toEqual(Array.from({ length: 25 }, (_, i) => i % 251));
    expect(seen).toEqual([0, 10, 20, 25]);
  });

  it("carries on where a reloaded page left the same file", async () => {
    const server = fakeServer();
    const f = file(25);
    server.set(20, 25, fingerprint(f));
    const seen: number[] = [];
    await sendBundle(f, (sent) => seen.push(sent), { fetch: server.fetchFn, chunk: 10, wait: noWait });
    expect(seen[0]).toBe(20);
  });

  it("waits and asks where the server got to when the connection drops", async () => {
    const server = fakeServer({ failFirstPut: 2 });
    const st = await sendBundle(file(25), () => undefined, { fetch: server.fetchFn, chunk: 10, wait: noWait });
    expect(st.state).toBe("unpacking");
    expect(server.got.length).toBe(25);
  });

  it("gives up after a few drops in a row and says the upload stopped", async () => {
    const server = fakeServer({ failFirstPut: 99 });
    await expect(sendBundle(file(25), () => undefined, { fetch: server.fetchFn, chunk: 10, wait: noWait }))
      .rejects.toMatchObject({ code: "stopped" });
  });

  it("passes a refusal on with its code and values", async () => {
    const server = fakeServer({ refuse: { status: 422, code: "not_a_bundle" } });
    const err = await sendBundle(file(25), () => undefined, { fetch: server.fetchFn, chunk: 10, wait: noWait }).catch((e) => e);
    expect(err).toBeInstanceOf(UploadError);
    expect(err.code).toBe("not_a_bundle");
  });

  it("refuses an empty file without asking the server", async () => {
    await expect(sendBundle(file(0), () => undefined, { fetch: fakeServer().fetchFn, wait: noWait }))
      .rejects.toMatchObject({ code: "not_a_bundle" });
  });
});

describe("waitUnpacked and the restart", () => {
  const answers = (list: Partial<UploadStatus>[]) => {
    let i = 0;
    return (async () => {
      const st = list[Math.min(i++, list.length - 1)];
      return new Response(JSON.stringify({ open: true, received: 0, total: 0, fp: null, files_done: 0, files_total: 0, error: null, restart: null, ...st }));
    }) as typeof fetch;
  };

  it("follows the files written until the server says done", async () => {
    const seen: [number, number][] = [];
    const st = await waitUnpacked((d, t) => seen.push([d, t]), {
      fetch: answers([{ state: "unpacking", files_done: 1, files_total: 3 }, { state: "unpacking", files_done: 3, files_total: 3 }, { state: "done", restart: "auto" }]),
      wait: noWait,
    });
    expect(st.restart).toBe("auto");
    expect(seen).toEqual([[1, 3], [3, 3]]);
  });

  it("reports a failed unpacking with the server's code", async () => {
    await expect(waitUnpacked(() => undefined, {
      fetch: answers([{ state: "failed", error: { code: "damaged", params: { entry: "data/raw/x" } } }]), wait: noWait,
    })).rejects.toMatchObject({ code: "damaged", params: { entry: "data/raw/x" } });
  });

  it("takes a server already back on the data, its upload closed, as done", async () => {
    const st = await waitUnpacked(() => undefined, { fetch: answers([{ state: "idle", open: false }]), wait: noWait });
    expect(st.state).toBe("done");
  });

  it("reloads only once a new server answers", async () => {
    let calls = 0;
    const health = (async () => {
      calls++;
      if (calls < 3) throw new TypeError("restarting");
      return new Response(JSON.stringify({ boot: calls < 4 ? "old" : "new" }));
    }) as typeof fetch;
    let reloaded = 0;
    await reloadAfterRestart("old", { fetch: health, wait: noWait }, () => reloaded++);
    expect(reloaded).toBe(1);
    expect(calls).toBe(4);
  });
});
