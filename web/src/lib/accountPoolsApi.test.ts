import { describe, expect, it } from "vitest";
import { accountQuotaSummary, poolForHarness, selectableAccounts } from "./accountPoolsApi";
import type { AccountPool } from "./accountPoolsApi";

const POOL: AccountPool = {
  id: "pool-codex",
  object: "account_pool",
  name: "codex-native",
  harness: "codex-native",
  description: null,
  created_at: 1,
  updated_at: null,
  accounts: [
    {
      id: "codex-acc1",
      object: "account",
      pool_id: "pool-codex",
      pool_name: "codex-native",
      harness: "codex-native",
      home_path: "/home/user/.codex",
      home_kind: "codex_home",
      enabled: true,
      created_at: 1,
      updated_at: null,
      quota: {
        status: "known",
        known: true,
        exhausted: false,
        model_family: "codex",
        observed_at: 1,
        stale: false,
        five_hour: { remaining_pct: 80, status: "known", reset_at: null },
        weekly: { remaining_pct: 60, status: "known", reset_at: null },
      },
      usage: { active_sessions: 2, sessions: 5, cost_usd: 1.5, last_used_at: 2 },
    },
    {
      id: "codex-acc2",
      object: "account",
      pool_id: "pool-codex",
      pool_name: "codex-native",
      harness: "codex-native",
      home_path: "/home/user/.codex-acc2",
      home_kind: "codex_home",
      enabled: false,
      created_at: 1,
      updated_at: null,
      quota: null,
      usage: { active_sessions: 0, sessions: 0, cost_usd: null, last_used_at: null },
    },
  ],
};

describe("poolForHarness", () => {
  it("matches a harness spelling to its pool", () => {
    expect(poolForHarness([POOL], "codex-native")).toBe(POOL);
    expect(poolForHarness([POOL], "codex")).toBe(POOL);
    expect(poolForHarness([POOL], "codex-sdk")).toBe(POOL);
  });

  it("returns null for a harness without a pool", () => {
    expect(poolForHarness([POOL], "claude-native")).toBeNull();
    expect(poolForHarness([POOL], null)).toBeNull();
  });
});

describe("selectableAccounts", () => {
  it("keeps only the enabled accounts", () => {
    expect(selectableAccounts(POOL).map((account) => account.id)).toEqual(["codex-acc1"]);
  });

  it("returns an empty list without a pool", () => {
    expect(selectableAccounts(null)).toEqual([]);
  });
});

describe("accountQuotaSummary", () => {
  it("summarises the quota windows", () => {
    expect(accountQuotaSummary(POOL.accounts[0])).toBe("5h 80% · weekly 60%");
  });

  it("reads unknown when there is no snapshot", () => {
    expect(accountQuotaSummary(POOL.accounts[1])).toBe("Quota unknown");
  });
});
