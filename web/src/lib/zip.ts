/**
 * Read a small .zip in the browser: a project file (GET /datasets/{id}/project.zip) or any zip of
 * the contract's CSV files.
 *
 * A project file is opened here and not on the server: its tables then go to POST /check and
 * POST /datasets as JSON, the same door as any export, and the server gains no route that takes an
 * archive. So this reads only what such a file holds: a handful of entries, stored or deflated,
 * no encryption, no zip64, within fixed limits; anything else is refused by name.
 */

export type ZipProblem = "not_zip" | "encrypted" | "zip64" | "too_many" | "too_large" | "unsupported" | "corrupt";

export class ZipError extends Error {
  constructor(readonly code: ZipProblem) {
    super(code);
    this.name = "ZipError";
  }
}

export interface ZipLimits {
  /** the most entries an archive may list */
  entries: number;
  /** the most bytes its entries may unpack to, together */
  bytes: number;
}

/** A project file is four or five tables of text: these limits are far above any real one. */
export const PROJECT_LIMITS: ZipLimits = { entries: 32, bytes: 40 * 2 ** 20 };

const EOCD = 0x06054b50;
const CENTRAL = 0x02014b50;
const LOCAL = 0x04034b50;

async function inflate(data: Uint8Array, expected: number): Promise<Uint8Array> {
  if (typeof DecompressionStream === "undefined") throw new ZipError("unsupported");
  // a stream of the one chunk, not a Blob's: the same code then runs wherever streams do
  const source = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(data);
      controller.close();
    },
  });
  const stream = source.pipeThrough(new DecompressionStream("deflate-raw") as unknown as ReadableWritablePair<Uint8Array, Uint8Array>);
  const reader = stream.getReader();
  const out = new Uint8Array(expected);
  let at = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      // an entry that unpacks to more than it declared is a file lying about its size
      if (at + value.length > expected) throw new ZipError("corrupt");
      out.set(value, at);
      at += value.length;
    }
  } catch (e) {
    await reader.cancel().catch(() => undefined);
    throw e instanceof ZipError ? e : new ZipError("corrupt");
  }
  if (at !== expected) throw new ZipError("corrupt");
  return out;
}

/**
 * The files of an archive by name (the last part of the path; folders are skipped). Two entries
 * with one name, an entry outside the limits or one this cannot read refuse the whole archive.
 */
export async function readZip(buf: ArrayBuffer, limits: ZipLimits = PROJECT_LIMITS): Promise<Map<string, Uint8Array>> {
  const bytes = new Uint8Array(buf);
  const view = new DataView(buf);
  // the end record is the last thing in the file, before a comment of at most 65 535 bytes
  let end = -1;
  for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 22 - 0xffff); i--) {
    if (view.getUint32(i, true) === EOCD) {
      end = i;
      break;
    }
  }
  if (end < 0) throw new ZipError("not_zip");
  const count = view.getUint16(end + 10, true);
  const dirSize = view.getUint32(end + 12, true);
  const dirAt = view.getUint32(end + 16, true);
  if (count === 0xffff || dirSize === 0xffffffff || dirAt === 0xffffffff) throw new ZipError("zip64");
  if (count > limits.entries) throw new ZipError("too_many");
  if (dirAt + dirSize > end) throw new ZipError("corrupt");

  const utf8 = new TextDecoder("utf-8");
  const out = new Map<string, Uint8Array>();
  let total = 0;
  let at = dirAt;
  for (let n = 0; n < count; n++) {
    if (at + 46 > bytes.length || view.getUint32(at, true) !== CENTRAL) throw new ZipError("corrupt");
    const flags = view.getUint16(at + 8, true);
    const method = view.getUint16(at + 10, true);
    const packed = view.getUint32(at + 20, true);
    const size = view.getUint32(at + 24, true);
    const nameLen = view.getUint16(at + 28, true);
    const extraLen = view.getUint16(at + 30, true);
    const commentLen = view.getUint16(at + 32, true);
    const localAt = view.getUint32(at + 42, true);
    const path = utf8.decode(bytes.subarray(at + 46, at + 46 + nameLen));
    at += 46 + nameLen + extraLen + commentLen;
    if (flags & 1) throw new ZipError("encrypted");
    if (packed === 0xffffffff || size === 0xffffffff || localAt === 0xffffffff) throw new ZipError("zip64");
    if (path.endsWith("/")) continue;
    if (method !== 0 && method !== 8) throw new ZipError("unsupported");
    total += size;
    if (total > limits.bytes) throw new ZipError("too_large");
    if (localAt + 30 > bytes.length || view.getUint32(localAt, true) !== LOCAL) throw new ZipError("corrupt");
    const start = localAt + 30 + view.getUint16(localAt + 26, true) + view.getUint16(localAt + 28, true);
    if (start + packed > bytes.length) throw new ZipError("corrupt");
    const data = bytes.subarray(start, start + packed);
    if (method === 0 && packed !== size) throw new ZipError("corrupt");
    const name = path.split(/[\\/]/).pop() ?? path;
    if (out.has(name)) throw new ZipError("corrupt");
    out.set(name, method === 0 ? data.slice() : await inflate(data, size));
  }
  return out;
}

export interface ProjectFile {
  /** the project's own name, when the file says it */
  name: string | null;
  /** the contract's tables found in the file, as CSV text */
  files: Record<string, string>;
  /** the file says its network is synthetic (made from an example network) */
  synthetic: boolean;
}

/**
 * A project file as what POST /check and POST /datasets take. `tables` are the contract's table
 * names; an archive that holds none of them is not a project.
 */
export async function readProjectFile(buf: ArrayBuffer, tables: Iterable<string>): Promise<ProjectFile> {
  const entries = await readZip(buf);
  const text = new TextDecoder("utf-8");
  const files: Record<string, string> = {};
  for (const table of tables) {
    const data = entries.get(`${table}.csv`);
    if (data) files[table] = text.decode(data);
  }
  if (Object.keys(files).length === 0) throw new ZipError("not_zip");
  let name: string | null = null;
  let synthetic = false;
  const doc = entries.get("project.json");
  if (doc) {
    try {
      const parsed = JSON.parse(text.decode(doc));
      if (typeof parsed?.name === "string" && parsed.name.trim()) name = parsed.name.trim().slice(0, 80);
      synthetic = parsed?.origin?.synthetic === true || parsed?.origin?.kind === "template";
    } catch {
      /* a project.json nobody can read: the tables still say what the network is */
    }
  }
  return { name, files, synthetic };
}
