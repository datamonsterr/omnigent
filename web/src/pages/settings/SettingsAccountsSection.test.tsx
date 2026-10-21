import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SettingsAccountsSection } from "./SettingsAccountsSection";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

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
      ],
    },
  ],
};

function TestWrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function mockFetch(handler: (url: string, init?: RequestInit) => Response) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) =>
    handler(typeof input === "string" ? input : input.toString(), init),
  );
}

describe("SettingsAccountsSection", () => {
  it("lists the pools, their accounts, and the usage totals", async () => {
    mockFetch(
      () =>
        new Response(JSON.stringify(POOLS), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    render(
      <TestWrapper>
        <SettingsAccountsSection />
      </TestWrapper>,
    );
    await waitFor(() => expect(screen.getByTestId("accounts-pool-codex-native")).toBeTruthy());
    expect(screen.getByTestId("accounts-account-codex-acc1")).toBeTruthy();
    expect(screen.getByTestId("accounts-active-codex-acc1").textContent).toContain("2 active");
    expect(screen.getByTestId("accounts-sessions-codex-acc1").textContent).toContain("5 sessions");
    expect(screen.getByTestId("accounts-cost-codex-acc1").textContent).toContain("$1.50");
    expect(screen.getByTestId("accounts-quota-codex-acc1").textContent).toContain("5h 80%");
    expect(screen.getByTestId("accounts-total-count").textContent).toContain("1 accounts");
  });

  it("stays usable on a narrow viewport", async () => {
    mockFetch(
      () =>
        new Response(JSON.stringify(POOLS), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    // A mobile viewport: the section must not depend on a wide layout.
    window.innerWidth = 390;
    window.dispatchEvent(new Event("resize"));
    render(
      <TestWrapper>
        <SettingsAccountsSection />
      </TestWrapper>,
    );
    await waitFor(() => expect(screen.getByTestId("accounts-account-codex-acc1")).toBeTruthy());
    // The account row stacks on a narrow screen (single column) and keeps its
    // usage facts and controls.
    const row = screen.getByTestId("accounts-account-codex-acc1");
    expect(row.className).toContain("sm:flex-row");
    expect(screen.getByTestId("accounts-toggle-codex-acc1")).toBeTruthy();
    expect(screen.getByTestId("accounts-remove-codex-acc1")).toBeTruthy();
    expect(screen.getByTestId("accounts-add-account-codex-native")).toBeTruthy();
  });

  it("disables an account", async () => {
    const calls: { url: string; method: string }[] = [];
    mockFetch((url, init) => {
      calls.push({ url, method: init?.method ?? "GET" });
      if (init?.method === "PATCH") {
        return new Response(JSON.stringify({ ...POOLS.data[0].accounts[0], enabled: false }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify(POOLS), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    render(
      <TestWrapper>
        <SettingsAccountsSection />
      </TestWrapper>,
    );
    await waitFor(() => expect(screen.getByTestId("accounts-toggle-codex-acc1")).toBeTruthy());
    await userEvent.click(screen.getByTestId("accounts-toggle-codex-acc1"));
    await waitFor(() =>
      expect(calls.some((call) => call.method === "PATCH" && call.url.includes("codex-acc1"))).toBe(
        true,
      ),
    );
  });
});
