import type { AccountPool } from "@/lib/accountPoolsApi";

/** A pool serving `harness` with one enabled account per id; quota and usage stay empty. */
export function testAccountPool(harness: string, accountIds: readonly string[]): AccountPool {
  const poolId = `pool-${harness}`;
  return {
    id: poolId,
    object: "account_pool",
    name: harness,
    harness,
    description: null,
    created_at: 1,
    updated_at: null,
    accounts: accountIds.map((id) => ({
      id,
      object: "account",
      pool_id: poolId,
      pool_name: harness,
      harness,
      home_path: `/home/user/.${id}`,
      home_kind: "codex_home",
      enabled: true,
      created_at: 1,
      updated_at: null,
      quota: null,
      usage: { active_sessions: 0, sessions: 0, cost_usd: null, last_used_at: null },
    })),
  };
}
