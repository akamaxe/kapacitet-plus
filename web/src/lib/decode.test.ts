import { describe, expect, it } from "vitest";
import { decodeCsv, decodedAs } from "./decode";

// "Kabelskåp Ängsvägen" as Windows-1252 writes it: å is 0xE5 and Ä is 0xC4, one byte each
const CP1252 = new Uint8Array([0x4b, 0x61, 0x62, 0x65, 0x6c, 0x73, 0x6b, 0xe5, 0x70, 0x20, 0xc4, 0x6e, 0x67, 0x73,
  0x76, 0xe4, 0x67, 0x65, 0x6e]);

describe("decodeCsv", () => {
  it("reads UTF-8 and drops a byte order mark", () => {
    const bytes = new Uint8Array([0xef, 0xbb, 0xbf, ...new TextEncoder().encode("bus_id;name\nB1;Kabelskåp")]);
    expect(decodeCsv(bytes)).toEqual({ text: "bus_id;name\nB1;Kabelskåp", encoding: "utf-8" });
  });

  it("reads what is not UTF-8 as Windows-1252, and says so", () => {
    expect(decodeCsv(CP1252)).toEqual({ text: "Kabelskåp Ängsvägen", encoding: "windows-1252" });
    expect(decodeCsv(CP1252.buffer)).toEqual({ text: "Kabelskåp Ängsvägen", encoding: "windows-1252" });
  });

  it("names only the tables that were not UTF-8", () => {
    expect(decodedAs({ buses: "windows-1252", lines: "utf-8" })).toEqual({ buses: "windows-1252" });
    expect(decodedAs({ lines: "utf-8" })).toEqual({});
  });
});
