import { beforeEach, describe, expect, it } from "vitest";
import {
  NO_SUMMARY, buildProgress, forgetRecent, lastScreen, projectFileUrl, projectsOf, readRecents, sortProjects, touchRecent,
  type Listing, type Project,
} from "./projects";

// A project is one network with what was computed for it. The start window lists what the server
// says, in the order someone looking for a project expects: the one in use, then the ones opened
// here last.

const listing: Listing = {
  current: "reference",
  active: "reference",
  supervised: true,
  reference: {
    id: "reference", name: "Smedslätten, Bromma (Stockholm)",
    summary: { customers: 120, feeders: 11, trafo_type: "0.4 MVA 10/0.4 kV", computed_at: "2026-11-06T10:45:53+00:00", quick: false, static_kw: 413.4, hourly_kw: 608.8, years: [2015, 2025] },
  },
  items: [
    { id: "kerber-a1", name: "Kerber Landnetz", created: "2026-11-12T10:00:00+00:00", status: "ready", quick: true, customers: 13,
      origin: { kind: "template", template: "kerber-land-ol", name: "Kerber Landnetz", synthetic: true }, summary: { customers: 13, hourly_kw: 77.5 } },
    { id: "export-b2", name: "An export", created: "2026-11-13T09:00:00+00:00", status: "building", building: true, customers: 58,
      steps: [{ name: "import", status: "done" }, { name: "households", status: "done" }, { name: "profiles", status: "running" }, { name: "compute", status: "waiting" }] },
    { id: "broken-c3", name: "Broken", created: "2026-11-11T09:00:00+00:00", status: "failed", error: "it stopped",
      steps: [{ name: "import", status: "done" }, { name: "households", status: "failed", error: "no pool" }] },
    { id: "kept-d4", name: "Kept for later", created: "2026-11-10T09:00:00+00:00", status: "uploaded" },
  ],
};

beforeEach(() => localStorage.clear());

describe("the projects of a listing", () => {
  const projects = projectsOf(listing);
  const by = (id: string) => projects.find((p) => p.id === id)!;

  it("are the reference network and every other one", () => {
    expect(projects.map((p) => p.id)).toEqual(["reference", "kerber-a1", "export-b2", "broken-c3", "kept-d4"]);
    expect(projects.filter((p) => p.reference).map((p) => p.id)).toEqual(["reference"]);
    expect(projects.filter((p) => p.current).map((p) => p.id)).toEqual(["reference"]);
  });

  it("say how each one stands", () => {
    expect(projects.map((p) => p.state)).toEqual(["ready", "ready", "building", "failed", "waiting"]);
    // the reference network before anything was computed for it
    const fresh = projectsOf({ ...listing, reference: { id: "reference", name: "x" } })[0];
    expect(fresh.state).toBe("empty");
    expect(fresh.summary).toEqual(NO_SUMMARY);
  });

  it("read what the server knows of a network, and nothing it does not", () => {
    expect(by("reference").customers).toBe(120);
    expect(by("reference").origin).toEqual({ kind: "reference" });
    expect(by("kerber-a1").summary.hourly_kw).toBe(77.5);
    expect(by("kerber-a1").summary.static_kw).toBeNull();
    expect(by("kerber-a1").origin.synthetic).toBe(true);
    // a network uploaded with its size, before any summary of it was computed
    expect(by("export-b2").customers).toBe(58);
    // a record that names no origin is an export
    expect(by("kept-d4").origin).toEqual({ kind: "csv" });
    expect(by("kept-d4").customers).toBeNull();
  });

  it("know which project the server is on", () => {
    const moved = projectsOf({ ...listing, current: "kerber-a1" });
    expect(moved.filter((p) => p.current).map((p) => p.id)).toEqual(["kerber-a1"]);
  });
});

describe("a build's steps", () => {
  it("count what is done and name the one running, or the one that failed", () => {
    const [, , building, broken] = projectsOf(listing);
    expect(buildProgress(building.steps)).toMatchObject({ done: 2, total: 4, running: { name: "profiles" } });
    expect(buildProgress(building.steps).failed).toBeUndefined();
    expect(buildProgress(broken.steps)).toMatchObject({ done: 1, total: 2, failed: { name: "households", error: "no pool" } });
    expect(buildProgress([])).toEqual({ done: 0, total: 0, running: undefined, failed: undefined });
  });
});

describe("the order of the list", () => {
  const projects = projectsOf(listing);
  const ids = (list: Project[]) => list.map((p) => p.id);

  it("puts the one in use first, then the newest", () => {
    expect(ids(sortProjects(projects, {}))).toEqual(["reference", "export-b2", "kerber-a1", "broken-c3", "kept-d4"]);
  });

  it("puts the ones opened here last before the others", () => {
    expect(ids(sortProjects(projects, { "kept-d4": 200, "broken-c3": 100 }))).toEqual(["reference", "kept-d4", "broken-c3", "export-b2", "kerber-a1"]);
    const moved = projectsOf({ ...listing, current: "kept-d4" });
    expect(ids(sortProjects(moved, { "kerber-a1": 5 }))[0]).toBe("kept-d4");
  });

  it("does not change the list it was given", () => {
    const before = ids(projects);
    sortProjects(projects, { "kept-d4": 1 });
    expect(ids(projects)).toEqual(before);
  });
});

describe("what this browser opened last", () => {
  it("is kept by project, and forgotten with the project", () => {
    expect(readRecents()).toEqual({});
    touchRecent("kerber-a1", 100);
    touchRecent("export-b2", 200);
    touchRecent("kerber-a1", 300);
    expect(readRecents()).toEqual({ "kerber-a1": 300, "export-b2": 200 });
    forgetRecent("export-b2");
    expect(readRecents()).toEqual({ "kerber-a1": 300 });
  });

  it("is nothing when what was stored is not a record of times", () => {
    for (const bad of ["[1, 2]", "nonsense", '"text"', "null", '{"a": "yesterday", "b": null}']) {
      localStorage.setItem("kap.recent", bad);
      expect(readRecents()).toEqual({});
    }
    localStorage.setItem("kap.recent", '{"a": 5, "b": "x"}');
    expect(readRecents()).toEqual({ a: 5 });
  });
});

describe("opening a project", () => {
  it("returns to the screen it was left on, or to the opening one", () => {
    expect(lastScreen()).toBe("compare");
    localStorage.setItem("kap.screen", "operation");
    expect(lastScreen()).toBe("operation");
    localStorage.setItem("kap.screen", "nowhere");
    expect(lastScreen()).toBe("compare");
  });

  it("downloads its file from the server", () => {
    expect(projectFileUrl("kerber-a1")).toBe("/api/v1/datasets/kerber-a1/project.zip");
  });
});
