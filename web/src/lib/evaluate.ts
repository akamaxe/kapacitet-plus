/**
 * Follow a running evaluation (server-sent events) to its answer: each stage as it finishes, then
 * the answer itself, fetched once the server has stored it. The server says "still running" until
 * then (202 on the DSO's endpoint, 409 on the portal's), so those are retried briefly.
 *
 * When every slot of the server's pool is taken, the evaluation waits in line: the stream says
 * `queued` with its place (and again as it moves up), then `started`. `cancel()` stops it on the
 * server, so a page that is left does not keep a slot from the next one.
 */
import { apiWrite } from "./api";

export type Failure = "failed" | "ended" | "timeout" | "dropped";

export interface Handlers {
  stage: (name: string) => void;
  done: (result: any) => void;
  fail: (why: Failure, detail?: string) => void;
  /** Waiting for a free slot: its place in line (1 is next); called again as it moves up. */
  queued?: (position: number) => void;
  /** Left the line and began. */
  started?: () => void;
}

export interface Following {
  close: () => void;
  /** Close the stream and stop the evaluation on the server, if it has not finished. */
  cancel: () => void;
}

export function follow(eventsUrl: string, resultUrl: string, stages: readonly string[], on: Handlers,
  cancelPath?: string): Following {
  const es = new EventSource(eventsUrl);
  let settled = false;
  const settle = () => {
    settled = true;
    es.close();
  };
  stages.forEach((s) => es.addEventListener(s, () => on.stage(s)));
  es.addEventListener("queued", (e: MessageEvent) => {
    const position = Number(JSON.parse(e.data)?.position);
    if (Number.isFinite(position)) on.queued?.(position);
  });
  es.addEventListener("started", () => on.started?.());
  es.addEventListener("done", async () => {
    settle();
    let res: any = null;
    for (let i = 0; i < 20 && !res; i++) {
      const r = await fetch(resultUrl).catch(() => null);
      if (r?.status === 200) res = await r.json();
      else if (r && r.status !== 202 && r.status !== 409) break;
      else await new Promise((ok) => setTimeout(ok, 150));
    }
    if (res) on.done(res);
    else on.fail("ended");
  });
  // the engine reports its own failure as a named event; the native `error` is the stream dropping
  es.addEventListener("failed", (e: MessageEvent) => {
    settle();
    on.fail("failed", JSON.parse(e.data)?.error ?? "");
  });
  es.addEventListener("end", () => {
    if (!settled) {
      settle();
      on.fail("ended");
    }
  });
  es.addEventListener("timeout", () => {
    settle();
    on.fail("timeout");
  });
  es.addEventListener("error", () => {
    if (!settled) {
      settle();
      on.fail("dropped");
    }
  });
  return {
    close: () => es.close(),
    cancel: () => {
      const running = !settled;
      settle();
      // best effort: the server ends a job nobody stops at its timeout anyway
      if (running && cancelPath) void apiWrite(cancelPath, {}, "DELETE").catch(() => null);
    },
  };
}
