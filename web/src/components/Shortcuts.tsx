/**
 * The key sheet, generated from the same list the key handler switches on, so the two
 * cannot drift. Until now the only record of these was an eight-pixel string in the
 * status bar.
 */
import { Dialog } from "./Dialog";
import { useT } from "../lib/i18n";

/** key hint, i18n key. The handler in App.tsx walks the same list. */
export const KEYS: [string, string][] = [
  ["1 – 6", "keys.screens"],
  ["Ctrl + ← →", "keys.step"],
  ["⌘ / Ctrl + K", "keys.palette"],
  ["/", "keys.search"],
  ["N", "keys.notes"],
  ["F", "keys.fullscreen"],
  ["?", "keys.help"],
  ["Esc", "keys.close"],
];

export function Shortcuts({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useT();
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
      </div>
    </Dialog>
  );
}
