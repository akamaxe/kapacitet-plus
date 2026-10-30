import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useT } from "../lib/i18n";

/**
 * A word of the trade with its meaning one hover, focus or tap away. The first definition
 * waits a moment, so a pointer crossing the screen does not set them off; once one has
 * been open, the next opens at once and without its entrance (the tooltip rule of the
 * motion grammar).
 */
let lastClosed = 0;
const WARM_MS = 600;
const DELAY_MS = 350;

export function Term({ id, children }: { id: string; children: ReactNode }) {
  const t = useT();
  const pop = useId();
  const [open, setOpen] = useState(false);
  const [instant, setInstant] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const show = (now: boolean) => {
    window.clearTimeout(timer.current);
    const warm = Date.now() - lastClosed < WARM_MS;
    if (now || warm) {
      setInstant(warm);
      setOpen(true);
    } else {
      timer.current = window.setTimeout(() => {
        setInstant(false);
        setOpen(true);
      }, DELAY_MS);
    }
  };
  const hide = () => {
    window.clearTimeout(timer.current);
    if (open) lastClosed = Date.now();
    setOpen(false);
  };

  return (
    <span className="term-wrap">
      <button
        type="button"
        className="term"
        aria-describedby={open ? pop : undefined}
        aria-expanded={open}
        onPointerEnter={(e) => { if (e.pointerType === "mouse") show(false); }}
        onPointerLeave={(e) => { if (e.pointerType === "mouse") hide(); }}
        onFocus={() => show(true)}
        onBlur={hide}
        onClick={() => (open ? hide() : show(true))}
        onKeyDown={(e) => { if (e.key === "Escape" && open) { e.stopPropagation(); hide(); } }}
      >
        {children}
      </button>
      {open && (
        <span role="tooltip" id={pop} className="term-pop" data-instant={instant || undefined}>
          {t(`term.${id}`)}
        </span>
      )}
    </span>
  );
}
