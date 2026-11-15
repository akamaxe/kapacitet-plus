import { describe, expect, it } from "vitest";
import { readZip } from "./zip";
import { runChoices, stationsFromZip, stepDone, stepReady, StudyZipError, type RunState, type StudyDetail } from "./studies";

const enc = new TextEncoder();
const TABLES = ["buses", "lines", "transformer", "customers", "equipment", "cable_types"];

/** A stored zip archive of these files (no compression, which is all the reader needs to see). */
function zip(files: Record<string, string>): ArrayBuffer {
  const parts: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  for (const [path, text] of Object.entries(files)) {
    const data = enc.encode(text);
    const name = enc.encode(path);
    const local = new Uint8Array(30 + name.length);
    const lv = new DataView(local.buffer);
    lv.setUint32(0, 0x04034b50, true);
    lv.setUint16(4, 20, true);
    lv.setUint16(6, 0x0800, true);
    lv.setUint32(18, data.length, true);
    lv.setUint32(22, data.length, true);
    lv.setUint16(26, name.length, true);
    local.set(name, 30);
    const head = new Uint8Array(46 + name.length);
    const cv = new DataView(head.buffer);
    cv.setUint32(0, 0x02014b50, true);
    cv.setUint16(4, 20, true);
    cv.setUint16(6, 20, true);
    cv.setUint16(8, 0x0800, true);
    cv.setUint32(20, data.length, true);
    cv.setUint32(24, data.length, true);
    cv.setUint16(28, name.length, true);
    cv.setUint32(42, offset, true);
    head.set(name, 46);
    parts.push(local, data);
    central.push(head);
    offset += local.length + data.length;
  }
  const dirSize = central.reduce((n, c) => n + c.length, 0);
  const end = new Uint8Array(22);
  const ev = new DataView(end.buffer);
  ev.setUint32(0, 0x06054b50, true);
  ev.setUint16(8, central.length, true);
  ev.setUint16(10, central.length, true);
  ev.setUint32(12, dirSize, true);
  ev.setUint32(16, offset, true);
  const all = [...parts, ...central, end];
  const out = new Uint8Array(all.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of all) {
    out.set(p, at);
    at += p.length;
  }
  return out.buffer;
}

describe("an archive of contract folders", () => {
  it("is one station per folder, by the folder's name, with only the contract's tables", async () => {
    const out = await stationsFromZip(zip({
      "st-001/buses.csv": "bus_id\nB1\n", "st-001/lines.csv": "line_id\nL1\n", "st-001/notes.txt": "skip",
      "st-002/buses.csv": "bus_id\nB2\n", "st-002/Customers.CSV": "customer_id\nC2\n", "readme.md": "skip",
    }), TABLES);
    expect(out).toEqual({ "st-001": { buses: "bus_id\nB1\n", lines: "line_id\nL1\n" },
      "st-002": { buses: "bus_id\nB2\n", customers: "customer_id\nC2\n" } });
  });

  it("looks one folder down when the whole archive is one folder", async () => {
    const out = await stationsFromZip(zip({ "export/st-1/buses.csv": "bus_id\nB1\n", "export/st-2/buses.csv": "bus_id\nB2\n" }), TABLES);
    expect(Object.keys(out).sort()).toEqual(["st-1", "st-2"]);
  });

  it("is refused when no folder holds a table of the contract", async () => {
    await expect(stationsFromZip(zip({ "a/b.txt": "x", "c.csv": "y" }), TABLES)).rejects.toBeInstanceOf(StudyZipError);
  });

  it("is read by whole paths, which never climb out of the archive", async () => {
    const keys = [...(await readZip(zip({ "a/buses.csv": "1", "b/buses.csv": "2" }), undefined, "path")).keys()];
    expect(keys.sort()).toEqual(["a/buses.csv", "b/buses.csv"]);
    await expect(readZip(zip({ "../x/buses.csv": "1" }), undefined, "path")).rejects.toThrow();
    // by name, as a project file is read, two folders with the same file are one name twice
    await expect(readZip(zip({ "a/buses.csv": "1", "b/buses.csv": "2" }))).rejects.toThrow();
  });
});

describe("the steps of a run", () => {
  const run = (over: Partial<RunState> = {}): RunState => ({ run: "2026Q4", collected: false, reviewed: false,
    signed: false, published: null, compared: false, ...over });

  it("are done by what the run's folder holds", () => {
    expect(stepDone(run({ collected: true, published: "utkast" }), "collect")).toBe(true);
    expect(stepDone(run({ published: "utkast" }), "publish")).toBe(true);
    expect(stepDone(run(), "diff")).toBe(false);
    expect(stepDone(undefined, "collect")).toBe(false);
  });

  it("each need the one before: a built station to collect, a review to sign", () => {
    expect(stepReady(undefined, "collect", 0)).toBe(false);
    expect(stepReady(undefined, "collect", 2)).toBe(true);
    expect(stepReady(run(), "review", 2)).toBe(false);
    expect(stepReady(run({ collected: true }), "review", 2)).toBe(true);
    expect(stepReady(run({ collected: true }), "sign", 2)).toBe(false);
    expect(stepReady(run({ collected: true, reviewed: true }), "sign", 2)).toBe(true);
    // a comparison needs another collected run to compare with
    expect(stepReady(run({ collected: true }), "diff", 2)).toBe(false);
    expect(stepReady(run({ collected: true }), "diff", 2, 1)).toBe(true);
  });

  it("offer the current period first when the study has no run for it yet", () => {
    const detail = { runs: [run({ run: "2026Q3" })], current_run: "2026Q4" } as StudyDetail;
    expect(runChoices(detail)).toEqual(["2026Q4", "2026Q3"]);
    expect(runChoices({ ...detail, current_run: "2026Q3" })).toEqual(["2026Q3"]);
  });
});
