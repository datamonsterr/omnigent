import { useMemo } from "react";
import { useServerInfo } from "@/lib/CapabilitiesContext";
import { useAccountPools } from "@/hooks/useAccountPools";
import { accountQuotaSummary, poolForHarness, selectableAccounts } from "@/lib/accountPoolsApi";
import type { ComposerConfigSection } from "./ComposerConfigSections";

/** Label of the "let the server choose" entry. */
export const ACCOUNT_AUTO_LABEL = "Auto";
export const ACCOUNT_AUTO_SUMMARY = "Fewest active sessions, then most quota";

/**
 * The account section of a harness config menu.
 *
 * An account is one subscription credential a harness session runs under.
 * The section lists the pool's accounts for the harness plus an `Auto` entry
 * that leaves the pick to the server. A harness with no pool yields no
 * section, so the picker is unchanged on a deployment without accounts.
 */
export function useAccountConfigSection({
  harness,
  selectedAccountId,
  onSelect,
  testId = "account-section",
}: {
  /** The harness whose pool the accounts come from. */
  harness: string | null;
  /** The account the user picked, or `null` for automatic selection. */
  selectedAccountId: string | null;
  /** Called with the picked account id, or `null` for automatic selection. */
  onSelect: (accountId: string | null) => void;
  /** Test id of the section wrapper. */
  testId?: string;
}): ComposerConfigSection | null {
  const info = useServerInfo();
  const quotaFirst =
    harness?.startsWith("codex") &&
    info !== "loading" &&
    info.codex_account_selection_policy === "quota_first";
  const { data: pools, isLoading, error, refetch } = useAccountPools(harness !== null);
  return useMemo<ComposerConfigSection | null>(() => {
    if (!harness) return null;
    if (error)
      return {
        testId,
        header: "Account",
        choices: [
          {
            key: "error",
            label: `Could not load accounts: ${error.message}`,
            checked: false,
            disabled: true,
          },
          {
            key: "retry",
            label: "Retry",
            checked: false,
            onSelect: () => {
              void refetch();
            },
          },
        ],
      };
    if (isLoading)
      return {
        testId,
        header: "Account",
        choices: [{ key: "loading", label: "Loading accounts…", checked: false, disabled: true }],
      };
    if (!pools) return null;
    const pool = poolForHarness(pools, harness);
    const accounts = selectableAccounts(pool);
    if (!pool) return null;
    if (accounts.length === 0)
      return {
        testId,
        header: "Account",
        choices: [{ key: "empty", label: "No enabled accounts", checked: false, disabled: true }],
      };
    return {
      testId,
      header: "Account",
      choices: [
        {
          key: "__auto__",
          label: ACCOUNT_AUTO_LABEL,
          checked: selectedAccountId === null,
          onSelect: () => onSelect(null),
          testId: `${testId}-auto`,
          title: quotaFirst
            ? "Most remaining fresh quota, then fewest active sessions"
            : ACCOUNT_AUTO_SUMMARY,
        },
        ...accounts.map((account) => ({
          key: account.id,
          label: (
            <span className="flex min-w-0 flex-col">
              <span className="truncate">{account.id}</span>
              <span className="truncate text-[11px] text-muted-foreground/70">
                {accountQuotaSummary(account)}
              </span>
            </span>
          ),
          checked: selectedAccountId === account.id,
          onSelect: () => onSelect(account.id),
          testId: `${testId}-${account.id}`,
          title: account.home_path,
        })),
      ],
    };
  }, [harness, pools, selectedAccountId, onSelect, testId, isLoading, error, refetch, quotaFirst]);
}
