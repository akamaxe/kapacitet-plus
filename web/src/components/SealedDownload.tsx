/**
 * A download that seals what it sends: the evidence for Ei, a day's schedule issued to an inverter.
 * It is posted rather than linked: sealing is a write, which a link (a GET) never is, and the page
 * keeps a receipt once the file is saved: which file, when, the first twelve hex digits of its seal
 * and its entry in the evidence chain. Without it nobody could tell afterwards what was sealed, or
 * match a file to its line in the chain.
 */
import { useState } from "react";
import { toast } from "./Toast";
import { apiWrite } from "../lib/api";
import { responseRefusal } from "../lib/errors";
import { clock } from "../lib/format";
import { useT } from "../lib/i18n";
import { useStore } from "../lib/store";

export interface Receipt {
  file: string;
  digest: string;
  seq: number;
  at: string;
}

const FILENAME = /filename="([^"]+)"/;

/** Post `body` to `path` (under /api/v1), save the answer under the name the server gives, and keep the seal it was given. */
export function useSealedDownload(): { receipt: Receipt | null; busy: boolean; download: (path: string, body: object, fallbackName: string) => Promise<void> } {
  const t = useT();
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [busy, setBusy] = useState(false);
  const download = async (path: string, body: object, fallbackName: string) => {
    setBusy(true);
    const r = await apiWrite(path, body).catch(() => null);
    if (!r) {
      setBusy(false);
      toast.error(t("ui.offline"));
      return;
    }
    if (!r.ok) {
      setBusy(false);
      toast.error(await responseRefusal(t, r));
      return;
    }
    const file = FILENAME.exec(r.headers.get("Content-Disposition") ?? "")?.[1] ?? fallbackName;
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = file;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
    setBusy(false);
    const digest = r.headers.get("X-Seal-Digest");
    if (digest) setReceipt({ file, digest, seq: Number(r.headers.get("X-Seal-Seq")), at: r.headers.get("X-Seal-At") ?? new Date().toISOString() });
  };
  return { receipt, busy, download };
}

/** The receipt of the last sealed download, kept on the page under its button. */
export function SealReceipt({ receipt }: { receipt: Receipt | null }) {
  const t = useT();
  const lang = useStore((s) => s.lang);
  return (
    <p className="seal-receipt caption-muted" role="status">
      {receipt ? t("seal.receipt", { when: clock(lang, receipt.at), seq: receipt.seq, file: receipt.file, digest: receipt.digest.slice(0, 12) }) : null}
    </p>
  );
}
