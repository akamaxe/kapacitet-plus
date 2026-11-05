import { afterEach, describe, expect, it, vi } from "vitest";
import { apiWrite } from "./api";

// The server takes a write only as JSON (api/guard.py): every write of the interface goes through
// apiWrite, the bodyless ones included, so none of them can reach it as a form or as plain text.
describe("apiWrite", () => {
  afterEach(() => vi.unstubAllGlobals());

  function stub() {
    const calls: [string, RequestInit][] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
      calls.push([url, init]);
      return new Response("{}", { status: 200 });
    }));
    return calls;
  }

  it("posts JSON under the API, with {} when there is nothing to send", async () => {
    const calls = stub();
    await apiWrite("/datasets/abc/activate");
    const [url, init] = calls[0];
    expect(url).toBe("/api/v1/datasets/abc/activate");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(init.body).toBe("{}");
  });

  it("sends the body, the method and extra headers, and keeps the content type its own", async () => {
    const calls = stub();
    await apiWrite("/settings", { auto_decisions: true }, "PUT", { "Idempotency-Key": "k-12345678", "Content-Type": "text/plain" });
    await apiWrite("/queue", undefined, "DELETE");
    expect(calls[0][1]).toMatchObject({ method: "PUT", body: '{"auto_decisions":true}' });
    expect(calls[0][1].headers).toEqual({ "Idempotency-Key": "k-12345678", "Content-Type": "application/json" });
    expect(calls[1][1]).toMatchObject({ method: "DELETE", body: "{}" });
  });
});
