import type { TFn } from "./i18n";
import type { Screen } from "./store";

/**
 * The project tree, as data: what the open project holds, in the order an engineering program
 * lists it. The network (its map, its transformer, its feeders and the houses on each), the
 * studies, the results and the reference. Pure: the component draws it and moves the focus.
 */
export type NodeKind = "project" | "group" | "screen" | "trafo" | "feeder" | "house";

export interface TreeNode {
  id: string;
  kind: NodeKind;
  label: string;
  /** a screen this entry opens */
  screen?: Screen;
  /** a house this entry selects, and how its solar is connected */
  cid?: number;
  status?: "firm" | "nonfirm" | "none";
  /** how many houses a feeder has, said after its name */
  count?: number;
  children?: TreeNode[];
}

/** A house as the tree needs it, from a feature of `/network`. */
export interface TreeHouse {
  cid: number;
  address: string | null;
  feeder: number;
  pv_status?: string;
}

export interface TreeInput {
  /** the project's name */
  site: string;
  /** the transformer's type, when the network says it */
  trafo?: string | null;
  houses: TreeHouse[];
}

/** The houses of a network answer, as the tree needs them. */
export function treeHouses(features: any[] | undefined): TreeHouse[] {
  const out: TreeHouse[] = [];
  for (const ft of features ?? []) {
    const p = ft?.properties;
    if (p?.layer === "house" && Number.isInteger(p.cid)) {
      out.push({ cid: p.cid, address: p.address || null, feeder: Number.isInteger(p.feeder) ? p.feeder : -1, pv_status: p.pv_status });
    }
  }
  return out;
}

/** Addresses in the order a street has them: by name, then by number ("71A" after "9"). */
export function byAddress(a: string, b: string): number {
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
}

export function buildTree(t: TFn, input: TreeInput): TreeNode {
  const screen = (s: Screen): TreeNode => ({ id: `screen:${s}`, kind: "screen", label: t(`tab.${s}`), screen: s });
  const feeders = new Map<number, TreeHouse[]>();
  for (const h of input.houses) {
    if (!feeders.has(h.feeder)) feeders.set(h.feeder, []);
    feeders.get(h.feeder)!.push(h);
  }
  const feederNodes: TreeNode[] = [...feeders.keys()].sort((a, b) => a - b).map((f) => {
    const houses = feeders.get(f)!;
    const named = (h: TreeHouse) => h.address ?? t("dt.unnamed", { id: h.cid });
    return {
      id: `feeder:${f}`,
      kind: "feeder",
      // a house the network puts on no feeder is still listed, under a name that says so
      label: f < 0 ? t("tree.noFeeder") : t("pal.feeder", { n: f + 1 }),
      count: houses.length,
      children: [...houses].sort((a, b) => byAddress(named(a), named(b)) || a.cid - b.cid).map((h): TreeNode => ({
        id: `house:${h.cid}`,
        kind: "house",
        label: named(h),
        cid: h.cid,
        status: h.pv_status === "firm" || h.pv_status === "nonfirm" ? h.pv_status : "none",
      })),
    };
  });
  return {
    id: "project",
    kind: "project",
    label: input.site,
    children: [
      {
        id: "group:network",
        kind: "group",
        label: t("tree.network"),
        children: [
          screen("twin"),
          { id: "trafo", kind: "trafo", label: input.trafo ? t("tree.trafoOf", { type: input.trafo }) : t("tree.trafo") },
          ...feederNodes,
        ],
      },
      { id: "group:studies", kind: "group", label: t("tree.studies"), children: [screen("compare"), screen("request"), screen("operation"), screen("scenarios")] },
      { id: "group:results", kind: "group", label: t("tree.results"), children: [screen("report"), screen("method")] },
      { id: "group:reference", kind: "group", label: t("tree.reference"), children: [screen("national")] },
    ],
  };
}

/** What is open the first time: everything but the feeders, whose houses would bury the rest. */
export function openAtFirst(root: TreeNode): Set<string> {
  const out = new Set<string>();
  const walk = (n: TreeNode) => {
    if (n.children && n.kind !== "feeder") out.add(n.id);
    n.children?.forEach(walk);
  };
  walk(root);
  return out;
}

export interface Row {
  node: TreeNode;
  level: number;
  parent: string | null;
  /** its place among its siblings, for a screen reader: 1-based, and how many there are */
  pos: number;
  size: number;
}

/** The rows on show, top to bottom: a node, and its children when it is open. */
export function rowsOf(root: TreeNode, open: ReadonlySet<string>): Row[] {
  const out: Row[] = [];
  const walk = (n: TreeNode, level: number, parent: string | null, pos: number, size: number) => {
    out.push({ node: n, level, parent, pos, size });
    if (n.children && open.has(n.id)) n.children.forEach((c, i) => walk(c, level + 1, n.id, i + 1, n.children!.length));
  };
  walk(root, 1, null, 1, 1);
  return out;
}

/** The ids from the root down to the node of that id, the node itself left out: what must be open for it to show. */
export function pathTo(root: TreeNode, id: string): string[] | null {
  if (root.id === id) return [];
  for (const child of root.children ?? []) {
    const below = pathTo(child, id);
    if (below) return [root.id, ...below];
  }
  return null;
}

/**
 * Where a key takes the focus, and what it opens or closes. Up and Down walk the rows on show,
 * Right opens a closed node or steps into an open one, Left closes an open node or steps out to
 * its parent, Home and End go to the ends, and a letter goes to the next row that starts with it.
 * Returns null for a key the tree does not take (a letter no row starts with among them).
 */
export function keyMove(rows: Row[], at: string, key: string, open: ReadonlySet<string>): { focus?: string; toggle?: string } | null {
  const i = rows.findIndex((r) => r.node.id === at);
  if (i < 0) return rows.length ? { focus: rows[0].node.id } : null;
  const row = rows[i];
  const parent = !!row.node.children?.length;
  switch (key) {
    case "ArrowDown": return { focus: rows[Math.min(rows.length - 1, i + 1)].node.id };
    case "ArrowUp": return { focus: rows[Math.max(0, i - 1)].node.id };
    case "Home": return { focus: rows[0].node.id };
    case "End": return { focus: rows[rows.length - 1].node.id };
    case "ArrowRight":
      if (!parent) return {};
      return open.has(row.node.id) ? { focus: rows[i + 1]?.node.id ?? at } : { toggle: row.node.id };
    case "ArrowLeft":
      if (parent && open.has(row.node.id)) return { toggle: row.node.id };
      return row.parent ? { focus: row.parent } : {};
    default: {
      if (key.length !== 1 || key === " ") return null;
      const letter = key.toLocaleLowerCase();
      const order = [...rows.slice(i + 1), ...rows.slice(0, i + 1)];
      const hit = order.find((r) => r.node.label.toLocaleLowerCase().startsWith(letter));
      // a character no row starts with is not the tree's: the frame's own keys still answer it
      return hit ? { focus: hit.node.id } : null;
    }
  }
}
