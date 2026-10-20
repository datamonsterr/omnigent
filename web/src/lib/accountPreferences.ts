/**
 * The account the user last picked for a harness.
 *
 * The picker remembers the choice per harness so a returning operator does
 * not re-pick, and so the same account keeps serving the same harness.
 */

const STORAGE_KEY = "omnigent:last-account-by-harness";

/** Read the remembered account id for a harness, or `null`. */
export function readLastAccount(harness: string | null): string | null {
  if (!harness) return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const value = parsed[harness];
    return typeof value === "string" && value ? value : null;
  } catch {
    return null;
  }
}

/** Remember the account picked for a harness. */
export function writeLastAccount(harness: string | null, accountId: string | null): void {
  if (!harness) return;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
    const next: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(parsed)) {
      if (key !== harness) next[key] = value;
    }
    if (accountId) next[harness] = accountId;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // A browser that blocks local storage just loses the memory.
  }
}

/** Forget a remembered account — used when the server rejects the pin. */
export function clearLastAccount(harness: string | null): void {
  writeLastAccount(harness, null);
}
