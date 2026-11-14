/**
 * A CSV file's bytes as text, by the server's own rule (importer/validate.py, `decode`): UTF-8, a
 * byte order mark dropped, and otherwise Windows-1252, which older versions of Excel write for
 * "CSV (semicolon)" in Sweden. `file.text()` reads every file as UTF-8 and turns å, ä and ö of such a
 * file into U+FFFD without a word; this says which encoding it took, and the check's report notes
 * it (`decoded_as` in POST /check and POST /datasets).
 */
export type Encoding = "utf-8" | "windows-1252";

export interface Decoded {
  text: string;
  encoding: Encoding;
}

export function decodeCsv(bytes: ArrayBuffer | Uint8Array): Decoded {
  const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  try {
    return { text: new TextDecoder("utf-8", { fatal: true }).decode(data), encoding: "utf-8" };
  } catch {
    return { text: new TextDecoder("windows-1252").decode(data), encoding: "windows-1252" };
  }
}

export async function readCsvFile(file: Blob): Promise<Decoded> {
  return decodeCsv(await file.arrayBuffer());
}

/** The tables that were not UTF-8, as the API takes them: the others need no mention. */
export function decodedAs(read: Record<string, Encoding>): Record<string, Encoding> {
  return Object.fromEntries(Object.entries(read).filter(([, e]) => e !== "utf-8"));
}
