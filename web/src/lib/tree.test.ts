import { describe, expect, it } from "vitest";
import { tFor } from "./i18n";
import { SCREENS } from "./store";
import { buildTree, byAddress, keyMove, openAtFirst, pathTo, rowsOf, treeHouses, type TreeNode } from "./tree";

// The project tree is data first: what the project holds, which rows show, and where a key
// takes the focus. The component only draws it.

const t = tFor("en");
const features = [
  { properties: { layer: "house", cid: 0, address: "Storgatan 10", feeder: 1, pv_status: "firm" } },
  { properties: { layer: "house", cid: 1, address: "Storgatan 9", feeder: 1, pv_status: "nonfirm" } },
  { properties: { layer: "house", cid: 2, address: "Storgatan 71A", feeder: 1, pv_status: "none" } },
  { properties: { layer: "house", cid: 3, address: "Ängsvägen 2", feeder: 0, pv_status: "firm" } },
  { properties: { layer: "house", cid: 4, address: null, feeder: 0 } },
  { properties: { layer: "cable", line: 0 } },
  { properties: { layer: "substation" } },
];
const root = buildTree(t, { site: "Test network", trafo: "0.4 MVA 10/0.4 kV", houses: treeHouses(features) });
const all = (n: TreeNode): TreeNode[] => [n, ...(n.children ?? []).flatMap(all)];
const labels = (rows: ReturnType<typeof rowsOf>) => rows.map((r) => r.node.label);

describe("what the tree holds", () => {
  it("reads the houses of a network answer, and nothing else", () => {
    expect(treeHouses(features).map((h) => h.cid)).toEqual([0, 1, 2, 3, 4]);
    expect(treeHouses(undefined)).toEqual([]);
    expect(treeHouses([{ properties: { layer: "house", cid: 1.5 } }, {}])).toEqual([]);
  });

  it("is the project, with its network, its studies, its results and the reference", () => {
    expect(root.label).toBe("Test network");
    expect(root.children!.map((c) => c.label)).toEqual(["Network", "Studies", "Results", "Reference"]);
  });

  it("lists every screen once, under its own name", () => {
    const screens = all(root).filter((n) => n.kind === "screen");
    expect(screens.map((s) => s.screen).sort()).toEqual([...SCREENS].sort());
    for (const s of screens) expect(s.label).toBe(t(`tab.${s.screen}`));
  });

  it("puts each house on its feeder, in the order a street has them", () => {
    const network = root.children![0];
    expect(network.children!.map((c) => c.label)).toEqual(["Neighbourhood", "Transformer, 0.4 MVA 10/0.4 kV", "Feeder 1", "Feeder 2"]);
    const [f1, f2] = network.children!.slice(2);
    expect([f1.count, f2.count]).toEqual([2, 3]);
    // a house without an address is named by its number; 9 comes before 10 and 71A
    expect(f1.children!.map((h) => h.label)).toEqual(["Ängsvägen 2", "House 4"]);
    expect(f2.children!.map((h) => h.label)).toEqual(["Storgatan 9", "Storgatan 10", "Storgatan 71A"]);
    expect(f2.children!.map((h) => h.status)).toEqual(["nonfirm", "firm", "none"]);
    expect(f1.children![1].status).toBe("none");
  });

  it("orders addresses by name, then by number", () => {
    expect(["B 10", "B 9", "A 2", "B 71A"].sort(byAddress)).toEqual(["A 2", "B 9", "B 10", "B 71A"]);
  });

  it("names the transformer plainly when the network does not say its type", () => {
    const bare = buildTree(t, { site: "x", trafo: null, houses: [] });
    expect(bare.children![0].children!.map((c) => c.label)).toEqual(["Neighbourhood", "Transformer"]);
  });

  it("lists a house that is on no feeder, under a name that says so", () => {
    const odd = buildTree(t, { site: "x", houses: [{ cid: 7, address: "A 1", feeder: -1 }] });
    expect(odd.children![0].children!.at(-1)!.label).toBe("On no feeder");
  });
});

describe("the rows on show", () => {
  it("open on everything but the feeders, whose houses would bury the rest", () => {
    const open = openAtFirst(root);
    expect([...open].sort()).toEqual(["group:network", "group:reference", "group:results", "group:studies", "project"]);
    expect(labels(rowsOf(root, open))).toEqual([
      "Test network", "Network", "Neighbourhood", "Transformer, 0.4 MVA 10/0.4 kV", "Feeder 1", "Feeder 2",
      "Studies", "Static vs envelopes", "Connection request", "Operation", "2030 scenarios",
      "Results", "Annual report", "Method & data", "Reference", "Sweden",
    ]);
  });

  it("say each row's depth and its place among its siblings", () => {
    const rows = rowsOf(root, new Set([...openAtFirst(root), "feeder:1"]));
    const of = (label: string) => rows.find((r) => r.node.label === label)!;
    expect([of("Test network").level, of("Network").level, of("Feeder 2").level, of("Storgatan 9").level]).toEqual([1, 2, 3, 4]);
    expect([of("Storgatan 10").pos, of("Storgatan 10").size, of("Storgatan 10").parent]).toEqual([2, 3, "feeder:1"]);
    expect(rowsOf(root, new Set())).toHaveLength(1);
  });

  it("know what must be open for a house to show", () => {
    expect(pathTo(root, "house:2")).toEqual(["project", "group:network", "feeder:1"]);
    expect(pathTo(root, "project")).toEqual([]);
    expect(pathTo(root, "house:99")).toBeNull();
  });
});

describe("the keys", () => {
  const open = openAtFirst(root);
  const rows = rowsOf(root, open);

  it("walk the rows on show and stop at the ends", () => {
    expect(keyMove(rows, "project", "ArrowDown", open)).toEqual({ focus: "group:network" });
    expect(keyMove(rows, "project", "ArrowUp", open)).toEqual({ focus: "project" });
    expect(keyMove(rows, "screen:national", "ArrowDown", open)).toEqual({ focus: "screen:national" });
    expect(keyMove(rows, "screen:twin", "Home", open)).toEqual({ focus: "project" });
    expect(keyMove(rows, "screen:twin", "End", open)).toEqual({ focus: "screen:national" });
  });

  it("open a closed node with Right, and step into an open one", () => {
    expect(keyMove(rows, "feeder:0", "ArrowRight", open)).toEqual({ toggle: "feeder:0" });
    expect(keyMove(rows, "group:network", "ArrowRight", open)).toEqual({ focus: "screen:twin" });
    expect(keyMove(rows, "screen:twin", "ArrowRight", open)).toEqual({});
  });

  it("close an open node with Left, and step out of a row to its parent", () => {
    expect(keyMove(rows, "group:studies", "ArrowLeft", open)).toEqual({ toggle: "group:studies" });
    expect(keyMove(rows, "screen:compare", "ArrowLeft", open)).toEqual({ focus: "group:studies" });
    expect(keyMove(rows, "feeder:0", "ArrowLeft", open)).toEqual({ focus: "group:network" });
    const shut = new Set(["group:network"]);
    expect(keyMove(rowsOf(root, shut), "project", "ArrowLeft", shut)).toEqual({});
  });

  it("jump to the next row a letter starts, round the end, and leave a letter no row starts with to the frame", () => {
    expect(keyMove(rows, "project", "s", open)).toEqual({ focus: "group:studies" });
    expect(keyMove(rows, "group:studies", "S", open)).toEqual({ focus: "screen:compare" });
    expect(keyMove(rows, "screen:national", "n", open)).toEqual({ focus: "group:network" });
    expect(keyMove(rows, "project", "2", open)).toEqual({ focus: "screen:scenarios" });
    expect(keyMove(rows, "project", "?", open)).toBeNull();
    expect(keyMove(rows, "project", "z", open)).toBeNull();
    expect(keyMove(rows, "project", " ", open)).toBeNull();
    expect(keyMove(rows, "project", "F5", open)).toBeNull();
  });

  it("start from the top when the focus was on a row that is gone", () => {
    expect(keyMove(rows, "house:2", "ArrowDown", open)).toEqual({ focus: "project" });
    expect(keyMove([], "project", "ArrowDown", open)).toBeNull();
  });
});
