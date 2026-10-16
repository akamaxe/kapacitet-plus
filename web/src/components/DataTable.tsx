/**
 * A table you can search, sort and select a row in.
 *
 * The eight tables in this project were static markup: no `aria-sort` anywhere, no sticky
 * header, no way to find a row. An operator scanning a feeder needs all three, and none of
 * them needs a library at this size.
 */
import { useId, useMemo, useState, type ReactNode } from "react";
import { Icon } from "./Icon";
import { useT } from "../lib/i18n";

export interface Col<T> {
  key: string;
  label: string;
  /** The sortable, searchable value. Numbers sort numerically, strings by locale. */
  value: (row: T) => string | number | null | undefined;
  /** What the cell shows, when that is not just the value. */
  cell?: (row: T) => ReactNode;
  /** Right-align and use tabular figures: §6 for anything numeric. */
  numeric?: boolean;
  /** Shown once in the header, not repeated in every cell (§6). */
  unit?: string;
}

export function DataTable<T>({
  rows,
  cols,
  rowKey,
  caption,
  searchLabel,
  selected,
  onSelect,
  initialSort,
}: {
  rows: T[];
  cols: Col<T>[];
  rowKey: (row: T) => string | number;
  /** Named for a screen reader, and it says where the numbers come from. */
  caption: string;
  searchLabel?: string;
  selected?: string | number | null;
  onSelect?: (row: T) => void;
  initialSort?: string;
}) {
  const t = useT();
  const id = useId();
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(
    initialSort ? { key: initialSort, dir: 1 } : null,
  );

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    let out = rows;
    if (needle) {
      out = rows.filter((r) =>
        cols.some((c) => String(c.value(r) ?? "").toLowerCase().includes(needle)),
      );
    }
    if (sort) {
      const col = cols.find((c) => c.key === sort.key);
      if (col) {
        out = [...out].sort((a, b) => {
          const x = col.value(a);
          const y = col.value(b);
          if (x == null) return 1;
          if (y == null) return -1;
          const d = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
          return d * sort.dir;
        });
      }
    }
    return out;
  }, [rows, cols, q, sort]);

  // asc, then desc, then back to the order the data came in
  const cycle = (key: string) =>
    setSort((s) => (s?.key !== key ? { key, dir: 1 } : s.dir === 1 ? { key, dir: -1 } : null));

  return (
    <div className="dt">
      <div className="dt-bar">
        <label className="dt-search">
          <Icon name="search" size={16} />
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={searchLabel ?? t("dt.search")}
            aria-label={searchLabel ?? t("dt.search")}
            aria-describedby={`${id}-count`}
          />
        </label>
        <span className="caption-muted" id={`${id}-count`} aria-live="polite">
          {t("dt.showing", { n: shown.length, total: rows.length })}
        </span>
      </div>
      <div className="dt-scroll">
        <table className="data">
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr>
              {cols.map((c) => {
                const on = sort?.key === c.key;
                return (
                  <th
                    key={c.key}
                    className={c.numeric ? "r" : undefined}
                    aria-sort={on ? (sort!.dir === 1 ? "ascending" : "descending") : "none"}
                  >
                    <button className="dt-sort" onClick={() => cycle(c.key)}>
                      <span>
                        {c.label}
                        {c.unit ? <span className="dt-unit"> {c.unit}</span> : null}
                      </span>
                      <Icon name="sort" size={13} />
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const k = rowKey(r);
              return (
                <tr
                  key={k}
                  aria-selected={selected != null && k === selected}
                  tabIndex={onSelect ? 0 : undefined}
                  onClick={onSelect ? () => onSelect(r) : undefined}
                  onKeyDown={
                    onSelect
                      ? (e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            onSelect(r);
                          }
                        }
                      : undefined
                  }
                >
                  {cols.map((c) => (
                    <td key={c.key} className={c.numeric ? "r" : undefined}>
                      {c.cell ? c.cell(r) : (c.value(r) ?? "–")}
                    </td>
                  ))}
                </tr>
              );
            })}
            {shown.length === 0 && (
              <tr>
                <td colSpan={cols.length}>
                  <div className="dt-none">
                    <span>{t("dt.none", { q })}</span>
                    <button className="btn ghost" onClick={() => setQ("")}>
                      {t("dt.clear")}
                    </button>
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
