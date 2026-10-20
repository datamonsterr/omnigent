import { useMemo } from "react";
import { useAccountPools } from "@/hooks/useAccountPools";
import { accountQuotaSummary, poolForHarness, selectableAccounts } from "@/lib/accountPoolsApi";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/** Value of the automatic-selection entry. */
const AUTO_VALUE = "__auto__";

/**
 * Pick the subscription account a session runs under.
 *
 * A harness with no account pool renders nothing, so a deployment without
 * accounts keeps the same surface it had before.
 */
export function AccountSelect({
  harness,
  value,
  onChange,
  testId = "account-select",
  label,
}: {
  /** The harness whose pool the accounts come from. */
  harness: string | null;
  /** The selected account id, or `null` for the server's own pick. */
  value: string | null;
  /** Called with the picked account id, or `null` for automatic selection. */
  onChange: (accountId: string | null) => void;
  /** Test id of the select trigger. */
  testId?: string;
  /** Optional visible label. */
  label?: string;
}) {
  const { data: pools } = useAccountPools();
  const accounts = useMemo(() => {
    if (!harness || !pools) return [];
    return selectableAccounts(poolForHarness(pools, harness));
  }, [harness, pools]);

  if (accounts.length === 0) return null;

  return (
    <div className="flex flex-col gap-1">
      {label && <span className="text-sm text-muted-foreground">{label}</span>}
      <Select
        value={value ?? AUTO_VALUE}
        onValueChange={(next) => onChange(next === AUTO_VALUE ? null : next)}
      >
        <SelectTrigger className="w-full" data-testid={testId}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={AUTO_VALUE} data-testid={`${testId}-auto`}>
            Auto (server picks)
          </SelectItem>
          {accounts.map((account) => (
            <SelectItem key={account.id} value={account.id} data-testid={`${testId}-${account.id}`}>
              <span className="flex flex-col">
                <span>{account.id}</span>
                <span className="text-xs text-muted-foreground">
                  {accountQuotaSummary(account)}
                </span>
              </span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
