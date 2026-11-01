import { describe, expect, it } from "vitest";
import golden from "../../../tests/fixtures/state_tiny.json";
import { decodeState, type StateHeader } from "./api";

// The same golden file tests/test_state_encoding.py encodes: if the engine's layout and this
// reader drift apart, one side or the other fails here.
function bytesOf(b64: string): Uint8Array {
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

describe("decodeState", () => {
  const header = golden.header as unknown as StateHeader;
  const state = decodeState(header, bytesOf(golden.blob_b64));

  it("reads every probe the engine side wrote", () => {
    for (const p of golden.probes) {
      const read = p.kind === "voltage" ? state.voltage(p.row, p.hour)
        : p.kind === "loading" ? state.loading(p.row, p.hour)
        : state.power(p.row, p.hour);
      expect(read).toBeCloseTo(p.value, 12);
    }
  });

  it("accepts an ArrayBuffer as well as a byte view", () => {
    const bytes = bytesOf(golden.blob_b64);
    const fromBuffer = decodeState(header, bytes.buffer.slice(0) as ArrayBuffer);
    expect(fromBuffer.voltage(2, 1)).toBe(state.voltage(2, 1));
  });

  it("has exactly one byte per bus, cable and customer per hour", () => {
    const { hours, n_bus, n_line, n_cust } = header;
    expect(state.bytes.length).toBe(hours * (n_bus + n_line + n_cust));
  });
});
