import { useEffect, useRef } from "react";
import { loadingColor, tokens } from "../lib/colors";
import { monthNames } from "../lib/format";
import { useStore } from "../lib/store";

/**
 * The year at a glance and the time control in one: a day × hour grid of transformer loading.
 * Click or drag to move to an hour; arrow keys step one hour (shift: one day).
 */
export function YearStrip({ values, hours, label }: { values: number[]; hours: number; label: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const hour = useStore((s) => s.hour);
  const setHour = useStore((s) => s.setHour);
  const theme = useStore((s) => s.theme);
  const lang = useStore((s) => s.lang);
  const days = Math.floor(hours / 24);

  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const draw = () => {
      const tk = tokens();
      const dpr = window.devicePixelRatio || 1;
      const w = cv.clientWidth;
      const h = cv.clientHeight;
      cv.width = Math.round(w * dpr);
      cv.height = Math.round(h * dpr);
      const ctx = cv.getContext("2d")!;
      ctx.scale(dpr, dpr);
      ctx.fillStyle = tk.inset;
      ctx.fillRect(0, 0, w, h);
      const cw = w / days;
      const ch = h / 24;
      for (let d = 0; d < days; d++) {
        for (let hh = 0; hh < 24; hh++) {
          const v = values[d * 24 + hh];
          if (v === undefined) continue;
          ctx.fillStyle = loadingColor(tk, v);
          // hour 0 at the top, like reading a day from morning down
          ctx.fillRect(d * cw, hh * ch, Math.ceil(cw), Math.ceil(ch));
        }
      }
      // current hour marker: vertical hairline + cell ring
      const d = Math.floor(hour / 24);
      const hh = hour % 24;
      ctx.strokeStyle = tk.text;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(d * cw + cw / 2, 0);
      ctx.lineTo(d * cw + cw / 2, h);
      ctx.stroke();
      ctx.lineWidth = 2;
      ctx.strokeStyle = tk.surface;
      ctx.strokeRect(d * cw - 3, hh * ch - 3, Math.max(cw, 2) + 6, ch + 6);
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = tk.text;
      ctx.strokeRect(d * cw - 2, hh * ch - 2, Math.max(cw, 2) + 4, ch + 4);
    };
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(cv);
    return () => ro.disconnect();
  }, [values, hour, theme, days]);

  const pick = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const cv = ref.current!;
    const r = cv.getBoundingClientRect();
    const d = Math.max(0, Math.min(days - 1, Math.floor(((e.clientX - r.left) / r.width) * days)));
    const hh = Math.max(0, Math.min(23, Math.floor(((e.clientY - r.top) / r.height) * 24)));
    setHour(d * 24 + hh);
  };

  const onKey = (e: React.KeyboardEvent) => {
    const step = e.shiftKey ? 24 : 1;
    if (e.key === "ArrowRight") setHour(Math.min(hours - 1, hour + step));
    else if (e.key === "ArrowLeft") setHour(Math.max(0, hour - step));
    else if (e.key === "ArrowDown") setHour(Math.min(hours - 1, hour + 1));
    else if (e.key === "ArrowUp") setHour(Math.max(0, hour - 1));
    else return;
    e.preventDefault();
  };

  const months = monthNames(lang);
  return (
    <>
      <canvas
        ref={ref}
        tabIndex={0}
        aria-label={label}
        onPointerDown={(e) => {
          (e.target as HTMLCanvasElement).setPointerCapture(e.pointerId);
          pick(e);
        }}
        onPointerMove={(e) => {
          if (e.buttons === 1) pick(e);
        }}
        onKeyDown={onKey}
      />
      <div className="yearstrip-axis" aria-hidden>
        {months.map((m) => (
          <span key={m}>{m}</span>
        ))}
      </div>
    </>
  );
}
