import { useEffect, useRef, useState } from "react";
import { loadingColor, tokens } from "../lib/colors";
import { hourLabel, monthNames } from "../lib/format";
import { useStore } from "../lib/store";

/**
 * The year at a glance and the time control in one: a day × hour grid of transformer loading.
 * Click or drag to move to an hour; arrow keys step one hour (shift: one day), Home and End go
 * to the ends. It is the screen's only hour slider, so it carries the slider role and value.
 *
 * The grid only changes with the data, the theme or the size, so it is painted once into an
 * offscreen canvas; an hour change copies it back and draws the marker (rendering).
 */
export function YearStrip({ values, hours, year, label, describedBy }: {
  values: number[]; hours: number; year: number; label: string; describedBy?: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const grid = useRef<HTMLCanvasElement | null>(null);
  const hour = useStore((s) => s.hour);
  const setHour = useStore((s) => s.setHour);
  const setPlaying = useStore((s) => s.setPlaying);
  const theme = useStore((s) => s.theme);
  const lang = useStore((s) => s.lang);
  const days = Math.floor(hours / 24);
  const [size, setSize] = useState({ w: 0, h: 0 });

  // one observer for the life of the strip
  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    // only a real change of size may re-render; a new object every time would loop
    const ro = new ResizeObserver(() => {
      const w = cv.clientWidth;
      const h = cv.clientHeight;
      setSize((s) => (s.w === w && s.h === h ? s : { w, h }));
    });
    ro.observe(cv);
    return () => ro.disconnect();
  }, []);

  // the grid: once per data, theme and size
  useEffect(() => {
    const { w, h } = size;
    if (!w || !h) return;
    const tk = tokens();
    const dpr = window.devicePixelRatio || 1;
    const off = grid.current ?? document.createElement("canvas");
    off.width = Math.round(w * dpr);
    off.height = Math.round(h * dpr);
    const ctx = off.getContext("2d")!;
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
    grid.current = off;
  }, [values, theme, days, size]);

  // the marker: every hour
  useEffect(() => {
    const cv = ref.current;
    const off = grid.current;
    const { w, h } = size;
    if (!cv || !off || !w || !h) return;
    const tk = tokens();
    const dpr = window.devicePixelRatio || 1;
    if (cv.width !== off.width || cv.height !== off.height) {
      cv.width = off.width;
      cv.height = off.height;
    }
    const ctx = cv.getContext("2d")!;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.drawImage(off, 0, 0);
    ctx.scale(dpr, dpr);
    const cw = w / days;
    const ch = h / 24;
    const d = Math.floor(hour / 24);
    const hh = hour % 24;
    // current hour: a vertical hairline through its day and a ring round its cell
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
  }, [hour, values, theme, days, size]);

  const pick = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const cv = ref.current!;
    const r = cv.getBoundingClientRect();
    const d = Math.max(0, Math.min(days - 1, Math.floor(((e.clientX - r.left) / r.width) * days)));
    const hh = Math.max(0, Math.min(23, Math.floor(((e.clientY - r.top) / r.height) * 24)));
    const next = d * 24 + hh;
    // a drag inside one cell is not a change
    if (next !== useStore.getState().hour) setHour(next);
  };

  const onKey = (e: React.KeyboardEvent) => {
    const now = useStore.getState().hour;
    const step = e.shiftKey ? 24 : 1;
    let next: number;
    if (e.key === "ArrowRight") next = now + step;
    else if (e.key === "ArrowLeft") next = now - step;
    else if (e.key === "ArrowDown") next = now + 1;
    else if (e.key === "ArrowUp") next = now - 1;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = hours - 1;
    else return;
    e.preventDefault();
    setPlaying(false);
    setHour(Math.max(0, Math.min(hours - 1, next)));
  };

  const months = monthNames(lang);
  return (
    <>
      <canvas
        ref={ref}
        tabIndex={0}
        role="slider"
        aria-label={label}
        aria-describedby={describedBy}
        aria-valuemin={0}
        aria-valuemax={hours - 1}
        aria-valuenow={hour}
        aria-valuetext={hourLabel(lang, year, hour)}
        onPointerDown={(e) => {
          (e.target as HTMLCanvasElement).setPointerCapture(e.pointerId);
          setPlaying(false);
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
