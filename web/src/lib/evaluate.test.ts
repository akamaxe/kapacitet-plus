import { afterEach, describe, expect, it, vi } from "vitest";
import { follow } from "./evaluate";

// jsdom has no EventSource: a stand-in that lets the test send the server's events by name.
class FakeSource {
  static last: FakeSource;
  listeners: Record<string, ((e: { data: string }) => void)[]> = {};
  closed = false;
  constructor(public url: string) {
    FakeSource.last = this;
  }
  addEventListener(name: string, fn: (e: { data: string }) => void) {
    (this.listeners[name] ??= []).push(fn);
  }
  close() {
    this.closed = true;
  }
  send(name: string, data: unknown = {}) {
    (this.listeners[name] ?? []).forEach((fn) => fn({ data: JSON.stringify(data) }));
  }
}

const quiet = { stage: () => {}, done: () => {}, fail: () => {} };

describe("follow", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reports the place in line as it moves up, then the start and the stages", () => {
    vi.stubGlobal("EventSource", FakeSource);
    const seen: (number | string)[] = [];
    follow("/events", "/result", ["static"], {
      ...quiet,
      stage: (s) => seen.push(s),
      queued: (n) => seen.push(n),
      started: () => seen.push("started"),
    });
    FakeSource.last.send("queued", { position: 2 });
    FakeSource.last.send("queued", { position: 1 });
    FakeSource.last.send("started");
    FakeSource.last.send("static", { ms: 4 });
    expect(seen).toEqual([2, 1, "started", "static"]);
  });

  it("stops the evaluation on the server while it runs, and only once", () => {
    vi.stubGlobal("EventSource", FakeSource);
    const calls: [string, RequestInit][] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
      calls.push([url, init]);
      return new Response("{}", { status: 200 });
    }));
    const following = follow("/events", "/result", [], quiet, "/apply/evaluate/abc123abc123");
    following.cancel();
    following.cancel();
    expect(FakeSource.last.closed).toBe(true);
    expect(calls).toHaveLength(1);
    expect(calls[0][0]).toBe("/api/v1/apply/evaluate/abc123abc123");
    expect(calls[0][1].method).toBe("DELETE");
  });

  it("sends nothing for an evaluation that has already failed", () => {
    vi.stubGlobal("EventSource", FakeSource);
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const failed: string[] = [];
    const following = follow("/events", "/result", [], { ...quiet, fail: (why) => failed.push(why) }, "/requests/x");
    FakeSource.last.send("failed", { error: "cancelled", code: "cancelled" });
    following.cancel();
    expect(failed).toEqual(["failed"]);
    expect(fetcher).not.toHaveBeenCalled();
  });
});
