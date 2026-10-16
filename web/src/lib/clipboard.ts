/**
 * Copy, and always say what happened.
 *
 * `navigator.clipboard.writeText` has three ways to leave a user with no answer: it is
 * absent outside a secure context, it rejects without permission, and — measured here —
 * it can stay pending forever when the page has no transient user activation. A promise
 * that never settles is the worst of the three, because the interface simply goes quiet.
 * So the write races a timer and the caller always gets a verdict.
 */
export function copyText(text: string, timeoutMs = 1500): Promise<void> {
  const write = navigator.clipboard?.writeText(text);
  if (!write) return Promise.reject(new Error("clipboard unavailable"));
  return Promise.race([
    write,
    new Promise<never>((_, reject) =>
      setTimeout(() => reject(new Error("clipboard timed out")), timeoutMs),
    ),
  ]);
}
