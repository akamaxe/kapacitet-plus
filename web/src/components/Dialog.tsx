/**
 * The one dialog primitive: the command palette, the shortcut sheet and the presenter
 * drawer all use it, so keyboard behaviour cannot drift between them.
 *
 * Not `<dialog>`: its `::backdrop` and top layer sit outside the token system, and the
 * drawer needs the same Escape and focus handling without a scrim or a modal trap.
 */
import { useCallback, useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

const FOCUSABLE =
  'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

/**
 * Open layers, newest last. Escape closes only the newest, so a palette opened over the
 * notes drawer closes alone; and while any modal layer is open the global shortcuts stay
 * quiet, so a digit typed under the key sheet does not change the screen behind it.
 */
const layers: { id: number; modal: boolean }[] = [];
let nextLayer = 0;

export function modalOpen(): boolean {
  return layers.some((l) => l.modal);
}

/**
 * Escape closes, and focus goes back where it came from. `modal` additionally keeps Tab
 * inside the panel. Used on its own by anything that opens without a scrim.
 */
export function useDismissable(open: boolean, onClose: () => void, modal: boolean) {
  const panel = useRef<HTMLDivElement>(null);
  const returnTo = useRef<HTMLElement | null>(null);
  // Callers pass an inline arrow, so a dependency on it would tear this effect down and
  // set it up again on every render — re-capturing the element to return focus to, and
  // by the end capturing the dialog's own input. Hold it in a ref instead.
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    if (!open) return;
    const layer = { id: ++nextLayer, modal };
    layers.push(layer);
    returnTo.current = document.activeElement as HTMLElement | null;
    const first = panel.current?.querySelector<HTMLElement>(FOCUSABLE) ?? panel.current;
    first?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (layers[layers.length - 1] !== layer) return;
      if (e.key === "Escape") {
        e.stopImmediatePropagation();
        close.current();
        return;
      }
      if (!modal || e.key !== "Tab" || !panel.current) return;
      const items = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
        (el) => el.offsetParent !== null,
      );
      if (items.length === 0) return;
      const edge = e.shiftKey ? items[0] : items[items.length - 1];
      if (document.activeElement === edge) {
        e.preventDefault();
        (e.shiftKey ? items[items.length - 1] : items[0]).focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      layers.splice(layers.indexOf(layer), 1);
      // By teardown the panel is usually already detached and focus has fallen to the
      // body, so asking whether focus is still inside it never says yes. Give it back
      // unless something else has deliberately taken it.
      const now = document.activeElement;
      const loose = !now || now === document.body || panel.current?.contains(now);
      const back = returnTo.current;
      if (loose && back?.isConnected) back.focus();
    };
  }, [open, modal]);

  return panel;
}

export function Dialog({
  open,
  onClose,
  title,
  labelledBy,
  width = 560,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  labelledBy?: string;
  width?: number;
  children: ReactNode;
}) {
  const panel = useDismissable(open, onClose, true);
  const onScrim = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === e.currentTarget) onClose();
    },
    [onClose],
  );
  if (!open) return null;
  return createPortal(
    <div className="scrim" onMouseDown={onScrim}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-label={labelledBy ? undefined : title}
        aria-labelledby={labelledBy}
        ref={panel}
        tabIndex={-1}
        style={{ maxWidth: width }}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
