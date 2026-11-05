/**
 * An application's reference code, as the portal reads it: KP-XXXX-XXXX-XXXX-XXXX, 80 bits in
 * Crockford's base32 (no I, L, O or U), or a KP-XXXX-XXXX code issued before those. The server's
 * pattern is the same (api/inbox.py REFERENCE), and so is the reading of what was typed.
 */
export const REF = /^KP-(?:[0-9A-HJKMNP-TV-Z]{4}-){3}[0-9A-HJKMNP-TV-Z]{4}$|^KP-[0-9A-F]{4}-[0-9A-F]{4}$/;

/** A code as typed: upper case, with the letters base32 reads as digits (O as 0, I and L as 1). */
export function normalizeRef(text: string): string {
  return text.trim().toUpperCase().replace(/O/g, "0").replace(/[IL]/g, "1");
}
