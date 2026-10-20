import { useMemo } from "react";
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
  const { data: pools } = useAccountPools();
  return useMemo<ComposerConfigSection | null>(() => {
    if (!harness || !pools) return null;
    const pool = poolForHarness(pools, harness);
    const accounts = selectableAccounts(pool);
    if (!pool || accounts.length === 0) return null;
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
          title: ACCOUNT_AUTO_SUMMARY,
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
  }, [harness, pools, selectedAccountId, onSelect, testId]);
}
