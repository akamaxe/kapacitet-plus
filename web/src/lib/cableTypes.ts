/**
 * The translation of cable names that New project's check lets the user make, written as the
 * export's `cable_types.csv` (the contract's optional table): one row per name of `lines.csv`, with
 * the catalogue type it is. The rows go into the export's own table when it has one, in its own
 * separator, and replace any row it gave the same name; without one, the table is just these two
 * columns. The server reads the result like any other table, so the check, the build and a project
 * file all see the same translation.
 */

export interface CableName {
  /** the name in lines.csv's std_type */
  name: string;
  /** how many sections use it */
  count: number;
}

/** One type of the catalogue of Swedish cables (GET /cable-types). */
export interface CableType {
  type: string;
  conductor: string;
  section_mm2: number;
  max_i_ka: number;
  source: string;
  url: string;
}

/** The names a finding of the check lists in its `names` parameter. */
export function namesOf(params: Record<string, unknown> | undefined): CableName[] {
  const raw = params?.names;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((x): x is CableName => !!x && typeof x.name === "string" && typeof x.count === "number")
    .map((x) => ({ name: x.name, count: x.count }));
}

/** The cells of one CSV row, with quoted cells and doubled quotes inside them. */
export function splitRow(line: string, sep: string): string[] {
  const cells: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (quoted) {
      if (c === '"' && line[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') quoted = false;
      else cell += c;
    } else if (c === '"') quoted = true;
    else if (c === sep) { cells.push(cell); cell = ""; }
    else cell += c;
  }
  cells.push(cell);
  return cells;
}

function cellOf(value: string, sep: string): string {
  return /["\n\r]/.test(value) || value.includes(sep) || value !== value.trim()
    ? `"${value.replace(/"/g, '""')}"`
    : value;
}

const count = (text: string, c: string) => text.split(c).length - 1;

/**
 * `table` (the export's cable_types.csv, if it has one) with a row for each translated name. A
 * table without a `name` column is left as it is: the check then says what it lacks.
 */
export function withTranslations(table: string | undefined, chosen: Record<string, string>): string | undefined {
  const rows = Object.entries(chosen)
    .map(([name, type]) => [name.trim(), type.trim()] as const)
    .filter(([name, type]) => name && type);
  if (!rows.length) return table;
  if (!table || !table.trim()) {
    return ["name,type", ...rows.map(([name, type]) => `${cellOf(name, ",")},${cellOf(type, ",")}`)].join("\n") + "\n";
  }
  const lines = table.replace(/^﻿/, "").replace(/\r\n?/g, "\n").split("\n");
  let header = lines[0];
  // as the server reads it: a semicolon header is Excel's Swedish dialect
  const sep = count(header, ";") > count(header, ",") ? ";" : ",";
  const cols = splitRow(header, sep).map((c) => c.trim().toLowerCase());
  const at = cols.indexOf("name");
  if (at < 0) return table;
  let typeAt = cols.indexOf("type");
  let body = lines.slice(1).filter((line) => line.trim() !== "");
  let width = cols.length;
  if (typeAt < 0) {
    header += `${sep}type`;
    typeAt = width;
    width += 1;
    body = body.map((line) => `${line}${sep}`);
  }
  const names = new Set(rows.map(([name]) => name));
  const kept = body.filter((line) => !names.has((splitRow(line, sep)[at] ?? "").trim()));
  const added = rows.map(([name, type]) => {
    const cells = Array<string>(width).fill("");
    cells[at] = cellOf(name, sep);
    cells[typeAt] = cellOf(type, sep);
    return cells.join(sep);
  });
  return [header, ...kept, ...added].join("\n") + "\n";
}
