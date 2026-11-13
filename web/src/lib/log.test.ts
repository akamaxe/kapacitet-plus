import { beforeEach, describe, expect, it } from "vitest";
import { toast, useToasts } from "../components/Toast";
import { LOG_KEY, LOG_MAX, LOG_OPEN_KEY, log, logText, parseLog, useLog, withLine, type LogLine } from "./log";

// The output window keeps what a notice says for six seconds and what an evaluation's stages say
// for two: each a line with its time, for the session, the oldest giving way past a limit.

const line = (id: number, text = `line ${id}`, level: LogLine["level"] = "info"): LogLine => ({ id, at: 1_000 * id, level, text });

beforeEach(() => {
  useLog.getState().clear();
  useLog.getState().setOpen(false);
  useToasts.setState({ toasts: [] });
});

describe("the lines", () => {
  it("get the next number, their time and one row each", () => {
    const one = withLine([], "info", "first", 5);
    expect(one).toEqual([{ id: 1, at: 5, level: "info", text: "first" }]);
    const two = withLine(one, "error", "second\n  on two rows ", 9);
    expect(two[1]).toEqual({ id: 2, at: 9, level: "error", text: "second on two rows" });
    // a line of nothing is not a line
    expect(withLine(two, "info", "  \n ", 10)).toBe(two);
  });

  it("drop the oldest past the limit, and keep counting", () => {
    let lines: LogLine[] = [];
    for (let i = 0; i < LOG_MAX + 5; i++) lines = withLine(lines, "info", `n${i}`, i);
    expect(lines).toHaveLength(LOG_MAX);
    expect(lines[0].text).toBe("n5");
    expect(lines.at(-1)!.id).toBe(LOG_MAX + 5);
  });

  it("are read back from what was stored, and anything else is nothing", () => {
    const stored = JSON.stringify([line(1), line(2, "bad", "step"), { id: "x" }, null, { ...line(3), level: "loud" }]);
    expect(parseLog(stored).map((l) => l.id)).toEqual([1, 2]);
    expect(parseLog(null)).toEqual([]);
    expect(parseLog("{")).toEqual([]);
    expect(parseLog('{"lines": []}')).toEqual([]);
    const many = JSON.stringify(Array.from({ length: LOG_MAX + 20 }, (_, i) => line(i + 1)));
    expect(parseLog(many)).toHaveLength(LOG_MAX);
  });

  it("copy as rows of text, a step under its line and an error marked", () => {
    const text = logText([line(1, "Request for A 1"), line(2, "Static study: 0.4 s", "step"), line(3, "It failed", "error")], (at) => `t${at / 1000}`);
    expect(text).toBe("t1  Request for A 1\nt2    Static study: 0.4 s\nt3  ! It failed");
  });
});

describe("the output window's store", () => {
  it("writes each line to the session, and clears it", () => {
    log.info("Project X is open.");
    log.step("a stage");
    log.ok("done");
    log.error("no");
    expect(useLog.getState().lines.map((l) => [l.level, l.text])).toEqual([
      ["info", "Project X is open."], ["step", "a stage"], ["ok", "done"], ["error", "no"],
    ]);
    expect(parseLog(sessionStorage.getItem(LOG_KEY))).toHaveLength(4);
    useLog.getState().clear();
    expect(useLog.getState().lines).toEqual([]);
    expect(parseLog(sessionStorage.getItem(LOG_KEY))).toEqual([]);
  });

  it("is closed until asked for, remembers that, and a line never opens it", () => {
    expect(useLog.getState().open).toBe(false);
    log.error("something failed");
    expect(useLog.getState().open).toBe(false);
    useLog.getState().setOpen(true);
    expect(localStorage.getItem(LOG_OPEN_KEY)).toBe("open");
    useLog.getState().setOpen(false);
    expect(localStorage.getItem(LOG_OPEN_KEY)).toBe("closed");
  });

  it("keeps every notice as a line: a notice is gone in a moment", () => {
    toast.info("Link copied");
    toast.error("The API did not answer.");
    expect(useToasts.getState().toasts.map((x) => x.text)).toEqual(["Link copied", "The API did not answer."]);
    expect(useLog.getState().lines.map((l) => [l.level, l.text])).toEqual([["info", "Link copied"], ["error", "The API did not answer."]]);
  });
});
