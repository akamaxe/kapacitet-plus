import { create } from "zustand";

/**
 * The output window's lines: what the program did for the person at it, kept for the session.
 *
 * A notice (Toast.tsx) says a thing once and is gone in six seconds; an evaluation's stages pass
 * in two. The same lines are kept here, with their time, where a planner looks for what the
 * engine did. They live in sessionStorage, so they outlast the reload a change of project makes
 * and end with the tab. Only what a person started is written: a background fetch that fails
 * says so in its own region, where the retry is.
 */
export type LogLevel = "info" | "ok" | "error" | "step";

export interface LogLine {
  id: number;
  /** when, in milliseconds since the epoch */
  at: number;
  level: LogLevel;
  text: string;
}

export const LOG_KEY = "kap.log";
export const LOG_OPEN_KEY = "kap.output";
/** The oldest lines give way past this many. */
export const LOG_MAX = 300;
const LEVELS: readonly string[] = ["info", "ok", "error", "step"];

function valid(x: any): x is LogLine {
  return !!x && Number.isFinite(x.id) && Number.isFinite(x.at) && LEVELS.includes(x.level) && typeof x.text === "string";
}

/** The lines a stored value holds; nothing for a value that is not a list of lines. Pure. */
export function parseLog(raw: string | null): LogLine[] {
  if (!raw) return [];
  try {
    const list = JSON.parse(raw);
    return Array.isArray(list) ? list.filter(valid).slice(-LOG_MAX) : [];
  } catch {
    return [];
  }
}

/** The lines with one more, the oldest dropped past the limit. Pure. */
export function withLine(lines: LogLine[], level: LogLevel, text: string, at: number): LogLine[] {
  const id = lines.reduce((top, l) => Math.max(top, l.id), 0) + 1;
  // one line each: a message that came with line breaks is still one thing that happened
  const line: LogLine = { id, at, level, text: text.replace(/\s+/g, " ").trim() };
  return line.text ? [...lines, line].slice(-LOG_MAX) : lines;
}

function read(storage: () => Storage, key: string): string | null {
  try {
    return storage().getItem(key);
  } catch {
    return null;
  }
}

function write(storage: () => Storage, key: string, value: string) {
  try {
    storage().setItem(key, value);
  } catch {
    /* private mode, or the quota: the lines stay in memory */
  }
}

interface LogState {
  lines: LogLine[];
  /** the output window is open; it never opens by itself */
  open: boolean;
  add: (level: LogLevel, text: string) => void;
  clear: () => void;
  setOpen: (open: boolean) => void;
}

export const useLog = create<LogState>((set, get) => ({
  lines: parseLog(read(() => sessionStorage, LOG_KEY)),
  open: read(() => localStorage, LOG_OPEN_KEY) === "open",
  add: (level, text) => {
    const lines = withLine(get().lines, level, text, Date.now());
    if (lines === get().lines) return;
    write(() => sessionStorage, LOG_KEY, JSON.stringify(lines));
    set({ lines });
  },
  clear: () => {
    write(() => sessionStorage, LOG_KEY, "[]");
    set({ lines: [] });
  },
  setOpen: (open) => {
    write(() => localStorage, LOG_OPEN_KEY, open ? "open" : "closed");
    set({ open });
  },
}));

/** Write a line from anywhere, including outside React. `step` is a part of the line above it. */
export const log = {
  info: (text: string) => useLog.getState().add("info", text),
  ok: (text: string) => useLog.getState().add("ok", text),
  error: (text: string) => useLog.getState().add("error", text),
  step: (text: string) => useLog.getState().add("step", text),
};

/** The lines as text, one per row with its time, for the clipboard. */
export function logText(lines: LogLine[], time: (at: number) => string): string {
  return lines.map((l) => `${time(l.at)}  ${l.level === "step" ? "  " : ""}${l.level === "error" ? "! " : ""}${l.text}`).join("\n");
}
