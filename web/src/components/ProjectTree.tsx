/**
 * The project tree: what the open project holds, where an engineering program keeps it, down the
 * left side. The network (its map, its transformer, its feeders and the houses on each), the
 * studies, the results and the reference. A study opens its screen; a house is selected, as the
 * search and the map select it.
 *
 * It is a tree to the keyboard too: Up and Down walk the rows, Right opens or steps in, Left
 * closes or steps out, a letter jumps, Enter does what a click does. One row is in the tab order.
 * Folded, it is a rail of the eight screens' icons.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useApi } from "../lib/api";
import { useT } from "../lib/i18n";
import { SCREENS, useStore, type Screen } from "../lib/store";
import { buildTree, keyMove, openAtFirst, pathTo, rowsOf, treeHouses, type TreeNode } from "../lib/tree";
import { Icon } from "./Icon";

/** What was open, kept while the page lives: folding the tree to its rail does not close its feeders. */
let remembered: Set<string> | null = null;

/** The screens in the tree's own order, for the rail: the network, the studies, the results, the reference. */
const RAIL: Screen[][] = [["twin"], ["compare", "request", "operation", "scenarios"], ["report", "method"], ["national"]];

function useTree(): TreeNode | null {
  const t = useT();
  const net = useApi<any>("/network");
  const meta = useApi<any>("/meta");
  return useMemo(() => {
    if (!net.data || !meta.data) return null;
    return buildTree(t, { site: meta.data.site, trafo: meta.data.grid?.trafo_type ?? null, houses: treeHouses(net.data.features) });
  }, [t, net.data, meta.data]);
}

export function ProjectTree({ onFold, onLeaf }: {
  /** fold the tree away (to its rail, or closed where it is a drawer) */
  onFold: () => void;
  /** a row that goes somewhere was chosen: a drawer closes on it */
  onLeaf?: () => void;
}) {
  const t = useT();
  const root = useTree();
  const view = useStore((s) => s.view);
  const screen = useStore((s) => s.screen);
  const selectedCid = useStore((s) => s.selectedCid);
  const [open, setOpen] = useState<Set<string>>(() => remembered ?? new Set());
  // the row in the tab order: the one last focused, or the screen on show
  const [active, setActive] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const seeded = useRef(remembered !== null);

  // the first tree of the page opens its groups; after that it is as it was left
  useEffect(() => {
    if (!root || seeded.current) return;
    seeded.current = true;
    setOpen(openAtFirst(root));
  }, [root]);
  useEffect(() => {
    if (seeded.current) remembered = open;
  }, [open]);

  // a house chosen anywhere is shown in the tree: its feeder opens
  useEffect(() => {
    if (!root || selectedCid === null) return;
    const path = pathTo(root, `house:${selectedCid}`);
    if (!path) return;
    setOpen((was) => (path.every((id) => was.has(id)) ? was : new Set([...was, ...path])));
  }, [root, selectedCid]);

  const rows = useMemo(() => (root ? rowsOf(root, open) : []), [root, open]);
  const current = view === "work" ? `screen:${screen}` : null;
  const tabStop = rows.some((r) => r.node.id === active) ? active : rows.some((r) => r.node.id === current) ? current : rows[0]?.node.id ?? null;

  // the selected house is brought into the tree's own view, never the page's
  useEffect(() => {
    if (selectedCid === null) return;
    const el = box.current?.querySelector<HTMLElement>(`[data-id="house:${selectedCid}"]`);
    const pane = box.current;
    if (!el || !pane) return;
    const top = el.offsetTop;
    if (top < pane.scrollTop) pane.scrollTop = top - 8;
    else if (top + el.offsetHeight > pane.scrollTop + pane.clientHeight) pane.scrollTop = top + el.offsetHeight - pane.clientHeight + 8;
  }, [selectedCid, rows]);

  const toggle = (id: string) => setOpen((was) => {
    const next = new Set(was);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });

  const act = (node: TreeNode) => {
    const st = useStore.getState();
    if (node.children?.length) return toggle(node.id);
    if (node.kind === "screen" && node.screen) st.setScreen(node.screen);
    else if (node.kind === "trafo") {
      // the station's own reading is the neighbourhood with no house picked
      st.select(null);
      st.setScreen("twin");
    } else if (node.kind === "house" && node.cid !== undefined) {
      st.select(node.cid);
      // on the request screen a house is the thing being asked about, so it stays
      if (st.screen !== "request" || st.view !== "work") st.setScreen("twin");
    }
    onLeaf?.();
  };

  const focusRow = (id: string) => {
    setActive(id);
    // the row may only exist after the node above it has opened
    requestAnimationFrame(() => box.current?.querySelector<HTMLElement>(`[data-id="${id}"]`)?.focus());
  };

  const onKey = (e: React.KeyboardEvent, node: TreeNode) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      act(node);
      return;
    }
    const move = keyMove(rows, node.id, e.key, open);
    if (!move) return;
    e.preventDefault();
    // a letter typed here is the tree's: it must not reach the frame's one-key shortcuts
    e.stopPropagation();
    if (move.toggle) toggle(move.toggle);
    if (move.focus) focusRow(move.focus);
  };

  return (
    <>
      <div className="tree-head">
        <span id="tree-title">{t("tree.title")}</span>
        <button className="icon-btn tree-fold" onClick={onFold} aria-label={t("tree.fold")} title={`${t("tree.fold")} (T)`}>
          <Icon name="previous" size={16} />
        </button>
      </div>
      <div className="tree" role="tree" aria-labelledby="tree-title" ref={box}>
        {rows.map(({ node, level, pos, size }) => {
          const parent = !!node.children?.length;
          const house = node.kind === "house";
          return (
            <div
              key={node.id}
              role="treeitem"
              data-id={node.id}
              data-kind={node.kind}
              className="tree-row"
              style={{ "--level": level } as React.CSSProperties}
              aria-level={level}
              aria-posinset={pos}
              aria-setsize={size}
              aria-expanded={parent ? open.has(node.id) : undefined}
              aria-current={node.id === current ? "page" : undefined}
              aria-selected={house ? node.cid === selectedCid : undefined}
              aria-label={house ? `${node.label}, ${t(`twin.status.${node.status}`)}` : node.count !== undefined ? `${node.label}, ${t("tree.houses", { n: node.count })}` : undefined}
              tabIndex={node.id === tabStop ? 0 : -1}
              onClick={() => { setActive(node.id); act(node); }}
              onFocus={() => setActive(node.id)}
              onKeyDown={(e) => onKey(e, node)}
            >
              <span className="tree-twist">{parent && <Icon name="next" size={14} />}</span>
              {node.kind === "screen" && node.screen && <Icon name={node.screen} size={15} />}
              {house && <i className={`house-key ${node.status === "none" ? "" : node.status}`} aria-hidden="true" />}
              <span className="tree-label">{node.label}</span>
              {node.count !== undefined && <span className="tree-count num">{node.count}</span>}
            </div>
          );
        })}
        {!root && <p className="tree-wait caption-muted">{t("ui.loading")}</p>}
      </div>
    </>
  );
}

/** The tree folded: the eight screens as icons, in the tree's order, and a way to open it again. */
export function TreeRail({ onOpen }: { onOpen: () => void }) {
  const t = useT();
  const view = useStore((s) => s.view);
  const screen = useStore((s) => s.screen);
  return (
    <nav className="tree-rail" aria-label={t("tree.title")}>
      <button className="rail-btn" onClick={onOpen} aria-label={t("tree.open")} title={`${t("tree.open")} (T)`}>
        <Icon name="tree" size={18} />
      </button>
      {RAIL.map((group, n) => (
        <div className="rail-group" key={n}>
          {group.map((s) => (
            <a key={s} className="rail-btn" href={`#/${s}`} aria-label={t(`tab.${s}`)} title={`${t(`tab.${s}`)} (${SCREENS.indexOf(s) + 1})`}
              aria-current={view === "work" && screen === s ? "page" : undefined}>
              <Icon name={s} size={18} />
            </a>
          ))}
        </div>
      ))}
    </nav>
  );
}
