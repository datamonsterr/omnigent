import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useAccountConfigSection } from "./AccountConfigSection";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/** Pool payload the account API returns. */
const POOLS = {
  object: "list",
  data: [
    {
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
    },
  ],
};

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function mockPools() {
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async () =>
      new Response(JSON.stringify(POOLS), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
  );
}

describe("useAccountConfigSection", () => {
  it("offers Auto and the pool's enabled accounts", async () => {
    mockPools();
    const { result } = renderHook(
      () =>
        useAccountConfigSection({
          harness: "codex-native",
          selectedAccountId: null,
          onSelect: vi.fn(),
          testId: "account-section",
        }),
      { wrapper },
    );
    await waitFor(() => expect(result.current).not.toBeNull());
    const keys = result.current!.choices.map((choice) => choice.key);
    expect(keys).toEqual(["__auto__", "codex-acc1"]);
  });

  it("checks the selected account", async () => {
    mockPools();
    const { result } = renderHook(
      () =>
        useAccountConfigSection({
          harness: "codex-native",
          selectedAccountId: "codex-acc1",
          onSelect: vi.fn(),
          testId: "account-section",
        }),
      { wrapper },
    );
    await waitFor(() => expect(result.current).not.toBeNull());
    const chosen = result.current!.choices.find((choice) => choice.key === "codex-acc1");
    expect(chosen?.checked).toBe(true);
  });

  it("yields no section for a harness without a pool", async () => {
    mockPools();
    const { result } = renderHook(
      () =>
        useAccountConfigSection({
          harness: "claude-native",
          selectedAccountId: null,
          onSelect: vi.fn(),
          testId: "account-section",
        }),
      { wrapper },
    );
    await waitFor(() => expect(result.current).toBeNull());
  });
});
