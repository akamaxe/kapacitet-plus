/**
 * What the API says when it refuses something, in the reader's language.
 *
 * A refusal carries `{code, message, params}` in its detail (api/main.py `api_error`, the queue's
 * `QueueError`): the code picks `err.<code>` in the dictionaries and the parameters fill it; the
 * English `message` is the fallback for a code this version has no words for, and a plain-text
 * detail is shown as it came.
 */
import type { TFn } from "./i18n/core";

export interface Refusal {
  code?: string;
  message?: string;
  params?: Record<string, string | number>;
}

/** The detail of an error response body, whatever shape it came in. */
export function refusalOf(body: unknown): Refusal {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object") return detail as Refusal;
  if (typeof detail === "string") return { message: detail };
  return {};
}

/** The refusal as one sentence: translated when its code is known, else the server's words. */
export function refusalText(t: TFn, r: Refusal, fallback = ""): string {
  if (r.code) {
    const key = `err.${r.code}`;
    const text = t(key, r.params);
    if (text !== key) return text;
  }
  return r.message ?? fallback;
}

/** Read a failed response into one sentence. */
export async function responseRefusal(t: TFn, r: Response): Promise<string> {
  const body = await r.json().catch(() => null);
  return refusalText(t, refusalOf(body), String(r.status));
}
