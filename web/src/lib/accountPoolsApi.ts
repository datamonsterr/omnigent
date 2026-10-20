import { authenticatedFetch } from "@/lib/identity";

/**
 * Client for the harness account pool API (`/v1/account-pools`).
 *
 * An account is one subscription credential a harness session can run under.
 * The inventory is server state, so the client only reads it and sends the
 * chosen account id back on session create.
 */

/**
 * Session label carrying the account a session runs under. The server reads
 * it as an explicit account pin.
 */
export const ACCOUNT_ID_LABEL = "omnigent.account.id";

/** One quota window of an account. */
export interface AccountQuotaWindow {
  remaining_pct: number | null;
  status: string;
  reset_at: string | null;
}

/** The last observed quota of an account. */
export interface AccountQuota {
  status: string;
  known: boolean;
  exhausted: boolean;
  model_family: string | null;
  observed_at: number | null;
  stale: boolean;
  five_hour: AccountQuotaWindow;
  weekly: AccountQuotaWindow;
}

/** Per-account consumption facts. */
export interface AccountUsage {
  active_sessions: number;
  sessions: number;
  cost_usd: number | null;
  last_used_at: number | null;
}

/** One account inside a pool. */
export interface AccountPoolAccount {
  id: string;
  object: "account";
  pool_id: string;
  pool_name: string | null;
  harness: string | null;
  home_path: string;
  home_kind: string;
  enabled: boolean;
  created_at: number;
  updated_at: number | null;
  quota: AccountQuota | null;
  usage: AccountUsage;
}

/** One pool of accounts. */
export interface AccountPool {
  id: string;
  object: "account_pool";
  name: string;
  harness: string;
  description: string | null;
  created_at: number;
  updated_at: number | null;
  accounts: AccountPoolAccount[];
}

interface AccountPoolListWire {
  data?: AccountPool[];
}

/** Usage window in days; `0` means today. */
export const ACCOUNT_USAGE_PERIOD_DAYS = 30;

export async function fetchAccountPools(
  periodDays: number = ACCOUNT_USAGE_PERIOD_DAYS,
): Promise<AccountPool[]> {
  const res = await authenticatedFetch(
    `/v1/account-pools?period_days=${encodeURIComponent(String(periodDays))}`,
  );
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  const body = (await res.json()) as AccountPoolListWire;
  return body.data ?? [];
}

export interface CreateAccountPoolPayload {
  name: string;
  harness: string;
  description?: string | null;
}

export async function createAccountPool(payload: CreateAccountPoolPayload): Promise<AccountPool> {
  const res = await authenticatedFetch("/v1/account-pools", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await errorDetail(res, "Could not create the account pool."));
  return (await res.json()) as AccountPool;
}

export interface UpdateAccountPoolPayload {
  name?: string;
  harness?: string;
  description?: string | null;
}

export async function updateAccountPool(
  poolId: string,
  payload: UpdateAccountPoolPayload,
): Promise<AccountPool> {
  const res = await authenticatedFetch(`/v1/account-pools/${encodeURIComponent(poolId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await errorDetail(res, "Could not update the account pool."));
  return (await res.json()) as AccountPool;
}

export async function deleteAccountPool(poolId: string): Promise<void> {
  const res = await authenticatedFetch(`/v1/account-pools/${encodeURIComponent(poolId)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await errorDetail(res, "Could not delete the account pool."));
}

export interface CreateAccountPayload {
  id: string;
  home_path: string;
  home_kind?: string;
  enabled?: boolean;
}

export async function createAccount(
  poolId: string,
  payload: CreateAccountPayload,
): Promise<AccountPoolAccount> {
  const res = await authenticatedFetch(
    `/v1/account-pools/${encodeURIComponent(poolId)}/accounts`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  if (!res.ok) throw new Error(await errorDetail(res, "Could not create the account."));
  return (await res.json()) as AccountPoolAccount;
}

export interface UpdateAccountPayload {
  home_path?: string;
  enabled?: boolean;
}

export async function updateAccount(
  accountId: string,
  payload: UpdateAccountPayload,
): Promise<AccountPoolAccount> {
  const res = await authenticatedFetch(`/v1/accounts/${encodeURIComponent(accountId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await errorDetail(res, "Could not update the account."));
  return (await res.json()) as AccountPoolAccount;
}

export async function deleteAccount(accountId: string): Promise<void> {
  const res = await authenticatedFetch(`/v1/accounts/${encodeURIComponent(accountId)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await errorDetail(res, "Could not delete the account."));
}

/** Read the server's error message out of a failed response. */
async function errorDetail(res: Response, fallback: string): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown; error?: { message?: unknown } };
    if (typeof body.detail === "string") return body.detail;
    if (typeof body.error?.message === "string") return body.error.message;
  } catch {
    // Fall through to the fallback message.
  }
  return fallback;
}

/**
 * Find the pool that serves a harness.
 *
 * The server normalizes the harness spelling, so a pool named for
 * `codex-native` also serves `codex` and `codex-sdk`.
 */
export function poolForHarness(pools: readonly AccountPool[], harness: string | null): AccountPool | null {
  if (!harness) return null;
  const normalized = harness.toLowerCase().replace(/-(native|sdk|ui)$/, "");
  return (
    pools.find((pool) => pool.harness.toLowerCase().replace(/-(native|sdk|ui)$/, "") === normalized) ??
    pools.find((pool) => pool.name === "default") ??
    null
  );
}

/** Accounts of a pool that may still serve a new session. */
export function selectableAccounts(pool: AccountPool | null): AccountPoolAccount[] {
  if (!pool) return [];
  return pool.accounts.filter((account) => account.enabled);
}

/** One-line quota summary for an account row. */
export function accountQuotaSummary(account: AccountPoolAccount): string {
  const quota = account.quota;
  if (!quota || !quota.known) return "Quota unknown";
  const fiveHour = quota.five_hour.remaining_pct;
  const weekly = quota.weekly.remaining_pct;
  const parts: string[] = [];
  if (typeof fiveHour === "number") parts.push(`5h ${Math.round(fiveHour)}%`);
  if (typeof weekly === "number") parts.push(`weekly ${Math.round(weekly)}%`);
  if (parts.length === 0) return "Quota unknown";
  if (quota.exhausted) return `${parts.join(" · ")} · exhausted`;
  return parts.join(" · ");
}
