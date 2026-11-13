/**
 * The key sheet, generated from the same list the key handler switches on, so the two
 * cannot drift. Until now the only record of these was an eight-pixel string in the
 * status bar.
 */
import { Dialog } from "./Dialog";
import { useT } from "../lib/i18n";
import { SCREENS, useStore } from "../lib/store";

/** key hint, i18n key. The handler in App.tsx walks the same list. */
export const KEYS: [string, string][] = [
  ["0", "keys.start"],
  [`1 – ${SCREENS.length}`, "keys.screens"],
  ["Ctrl + ← →", "keys.step"],
  ["PageUp / PageDown", "keys.page"],
  ["⌘ / Ctrl + K", "keys.palette"],
  ["/", "keys.search"],
  ["T", "keys.tree"],
  ["O", "keys.output"],
  ["F", "keys.fullscreen"],
  ["?", "keys.help"],
  ["Esc", "keys.close"],
];

export function Shortcuts({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useT();
  const singleKeys = useStore((s) => s.singleKeys);
  const setSingleKeys = useStore((s) => s.setSingleKeys);
  return (
    <Dialog open={open} onClose={onClose} title={t("keys.title")} width={460}>
      <div className="sheet">
        <h2>{t("keys.title")}</h2>
        <dl className="keys">
          {KEYS.map(([k, label]) => (
            <div key={k}>
              <dt>
                {k.split(" ").map((part, i) =>
                  part === "+" || part === "/" || part === "–" ? (
                    <span key={i} className="keys-join">
                      {part}
                    </span>
                  ) : (
                    <kbd key={i}>{part}</kbd>
                  ),
                )}
              </dt>
              <dd>{t(label)}</dd>
            </div>
          ))}
        </dl>
        <label className="keys-toggle">
          <input type="checkbox" checked={singleKeys} onChange={(e) => setSingleKeys(e.target.checked)} />
          <span>{t("keys.single")}<small>{t("keys.singleNote", { n: SCREENS.length })}</small></span>
        </label>
      </div>
    </Dialog>
  );
}
