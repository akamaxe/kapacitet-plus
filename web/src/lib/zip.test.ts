import { describe, expect, it } from "vitest";
import { readProjectFile, readZip, ZipError, type ZipProblem } from "./zip";

// A project file is read in the browser. These build small archives by hand, stored and deflated,
// and the ones that must be refused: the reader takes nothing it was not written for.

const enc = new TextEncoder();

async function deflate(data: Uint8Array): Promise<Uint8Array> {
  const source = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(data);
      controller.close();
    },
  });
  const reader = source.pipeThrough(new CompressionStream("deflate-raw") as unknown as ReadableWritablePair<Uint8Array, Uint8Array>).getReader();
  const chunks: Uint8Array[] = [];
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
  }
  const out = new Uint8Array(chunks.reduce((n, c) => n + c.length, 0));
  let at = 0;
  for (const c of chunks) {
    out.set(c, at);
    at += c.length;
  }
  return out;
}

interface Entry {
  name: string;
  text: string;
  /** 0 stored, 8 deflated */
  method?: 0 | 8;
  flags?: number;
  /** what the headers claim the unpacked size is, when not the truth */
  claim?: number;
}

/** A zip archive with exactly these entries, written the way any tool writes one. */
async function zip(entries: Entry[], patch?: (bytes: Uint8Array, view: DataView, eocd: number) => void): Promise<ArrayBuffer> {
  const parts: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  for (const e of entries) {
    const raw = enc.encode(e.text);
    const method = e.method ?? 0;
    const data = method === 8 ? await deflate(raw) : raw;
    const name = enc.encode(e.name);
    const size = e.claim ?? raw.length;
    const local = new Uint8Array(30 + name.length);
    const lv = new DataView(local.buffer);
    lv.setUint32(0, 0x04034b50, true);
    lv.setUint16(4, 20, true);
    lv.setUint16(6, e.flags ?? 0x0800, true);
    lv.setUint16(8, method, true);
    lv.setUint32(18, data.length, true);
    lv.setUint32(22, size, true);
    lv.setUint16(26, name.length, true);
    local.set(name, 30);
    const head = new Uint8Array(46 + name.length);
    const cv = new DataView(head.buffer);
    cv.setUint32(0, 0x02014b50, true);
    cv.setUint16(4, 20, true);
    cv.setUint16(6, 20, true);
    cv.setUint16(8, e.flags ?? 0x0800, true);
    cv.setUint16(10, method, true);
    cv.setUint32(20, data.length, true);
    cv.setUint32(24, size, true);
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
  ev.setUint16(8, entries.length, true);
  ev.setUint16(10, entries.length, true);
  ev.setUint32(12, dirSize, true);
  ev.setUint32(16, offset, true);
  const all = [...parts, ...central, end];
  const out = new Uint8Array(all.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of all) {
    out.set(p, at);
    at += p.length;
  }
  patch?.(out, new DataView(out.buffer), out.length - 22);
  return out.buffer;
}

async function problem(buf: ArrayBuffer | Promise<ArrayBuffer>, limits?: { entries: number; bytes: number }): Promise<ZipProblem | "read"> {
  try {
    await readZip(await buf, limits);
    return "read";
  } catch (e) {
    if (e instanceof ZipError) return e.code;
    throw e;
  }
}

const text = (m: Map<string, Uint8Array>, name: string) => new TextDecoder().decode(m.get(name));

describe("readZip", () => {
  it("reads stored and deflated entries, by the last part of their path", async () => {
    const csv = "bus_id,kind\nB1,substation\n".repeat(200);
    const got = await readZip(await zip([
      { name: "project.json", text: '{"name":"Åkern"}' },
      { name: "export/buses.csv", text: csv, method: 8 },
      { name: "export/", text: "" },
    ]));
    expect([...got.keys()]).toEqual(["project.json", "buses.csv"]);
    expect(text(got, "project.json")).toBe('{"name":"Åkern"}');
    expect(text(got, "buses.csv")).toBe(csv);
  });

  it("refuses what is not a zip, and a zip it was not written for", async () => {
    expect(await problem(enc.encode("bus_id,kind\n").buffer as ArrayBuffer)).toBe("not_zip");
    expect(await problem(new ArrayBuffer(0))).toBe("not_zip");
    expect(await problem(zip([{ name: "a.csv", text: "x", flags: 0x0801 }]))).toBe("encrypted");
    expect(await problem(zip([{ name: "a.csv", text: "x" }], (_b, v, eocd) => v.setUint16(eocd + 10, 0xffff, true)))).toBe("zip64");
    // a method other than stored or deflated (12 is bzip2)
    expect(await problem(zip([{ name: "a.csv", text: "x" }], (_b, v, eocd) => v.setUint16(v.getUint32(eocd + 16, true) + 10, 12, true)))).toBe("unsupported");
  });

  it("holds an archive to its limits before unpacking anything", async () => {
    const three = [1, 2, 3].map((i) => ({ name: `t${i}.csv`, text: "x".repeat(100) }));
    expect(await problem(zip(three), { entries: 2, bytes: 1e6 })).toBe("too_many");
    expect(await problem(zip(three), { entries: 8, bytes: 250 })).toBe("too_large");
    expect(await problem(zip(three), { entries: 8, bytes: 300 })).toBe("read");
  });

  it("refuses an entry that is not what its header says", async () => {
    const body = "a,b\n1,2\n".repeat(500);
    // unpacks to more than it declares, and to less
    expect(await problem(zip([{ name: "a.csv", text: body, method: 8, claim: 10 }]))).toBe("corrupt");
    expect(await problem(zip([{ name: "a.csv", text: body, method: 8, claim: body.length + 5 }]))).toBe("corrupt");
    expect(await problem(zip([{ name: "a.csv", text: body, claim: 3 }]))).toBe("corrupt");
    // two entries under one name, and a directory that points past the file
    expect(await problem(zip([{ name: "a.csv", text: "1" }, { name: "x/a.csv", text: "2" }]))).toBe("corrupt");
    expect(await problem(zip([{ name: "a.csv", text: "1" }], (_b, v, eocd) => v.setUint32(eocd + 16, 0x00ffffff, true)))).toBe("corrupt");
  });
});

describe("readProjectFile", () => {
  const TABLES = ["buses", "lines", "transformer", "customers", "equipment"];

  it("gives the contract's tables and what the project says of itself", async () => {
    const got = await readProjectFile(await zip([
      { name: "project.json", text: JSON.stringify({ schema: "kapacitet.project/1", name: "  Short feeders ", origin: { kind: "template", synthetic: true } }) },
      { name: "buses.csv", text: "bus_id\nB1\n", method: 8 },
      { name: "lines.csv", text: "line_id\nL1\n" },
      { name: "transformer.csv", text: "lv_bus\nB1\n" },
      { name: "customers.csv", text: "customer_id\nC1\n" },
      { name: "README.md", text: "# the contract" },
      { name: "notes.txt", text: "not a table" },
    ]), TABLES);
    expect(got.name).toBe("Short feeders");
    expect(got.synthetic).toBe(true);
    expect(Object.keys(got.files)).toEqual(["buses", "lines", "transformer", "customers"]);
    expect(got.files.buses).toBe("bus_id\nB1\n");
  });

  it("opens a plain zip of the CSV files, and one whose project.json cannot be read", async () => {
    const plain = await readProjectFile(await zip([{ name: "buses.csv", text: "bus_id\nB1\n" }]), TABLES);
    expect(plain).toEqual({ name: null, files: { buses: "bus_id\nB1\n" }, synthetic: false });
    const odd = await readProjectFile(await zip([{ name: "project.json", text: "{" }, { name: "lines.csv", text: "line_id\n" }]), TABLES);
    expect(odd.name).toBeNull();
    expect(odd.synthetic).toBe(false);
  });

  it("refuses an archive that holds none of the contract's tables", async () => {
    await expect(readProjectFile(await zip([{ name: "photo.txt", text: "x" }]), TABLES)).rejects.toMatchObject({ code: "not_zip" });
  });
});
