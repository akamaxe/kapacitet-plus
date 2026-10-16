/**
 * Confirmations and failures of things the user did.
 *
 * Deliberately not a feed: background fetch failures belong in the region that failed,
 * where the retry is. A console that posts three notices a minute is noise, so only an
 * action a person took speaks here.
 */
import { useEffect } from "react";
import { create } from "zustand";
import { Icon } from "./Icon";

export type ToastKind = "info" | "error";
type Toast = { id: number; kind: ToastKind; text: string };

interface ToastState {
  toasts: Toast[];
  push: (kind: ToastKind, text: string) => void;
  dismiss: (id: number) => void;
}

let seq = 0;

export const useToasts = create<ToastState>((set) => ({
  toasts: [],
  push: (kind, text) =>
    set((s) => ({ toasts: [...s.toasts, { id: ++seq, kind, text }].slice(-3) })),
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

/** Say it from anywhere, including outside React. */
export const toast = {
  info: (text: string) => useToasts.getState().push("info", text),
  error: (text: string) => useToasts.getState().push("error", text),
};

function Item({ t }: { t: Toast }) {
  const dismiss = useToasts((s) => s.dismiss);
  useEffect(() => {
    // an error waits to be read and dismissed; a confirmation does not
    if (t.kind === "error") return;
    const id = window.setTimeout(() => dismiss(t.id), 6000);
    return () => window.clearTimeout(id);
  }, [t.id, t.kind, dismiss]);
  return (
    <div className={`toast ${t.kind}`} role={t.kind === "error" ? "alert" : "status"}>
      <Icon name={t.kind === "error" ? "alert" : "check"} size={16} />
      <span>{t.text}</span>
      <button className="toast-close" onClick={() => dismiss(t.id)} aria-label="Dismiss">
        <Icon name="close" size={14} />
      </button>
    </div>
  );
}

export function Toasts() {
  const toasts = useToasts((s) => s.toasts);
  if (toasts.length === 0) return null;
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <Item key={t.id} t={t} />
      ))}
    </div>
  );
}
