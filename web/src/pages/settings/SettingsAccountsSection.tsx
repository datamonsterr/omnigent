import { useMemo, useState } from "react";
import { useServerInfo } from "@/lib/CapabilitiesContext";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { PlusIcon, Trash2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAccountPools } from "@/hooks/useAccountPools";
import {
  accountQuotaSummary,
  createAccount,
  createAccountPool,
  deleteAccount,
  deleteAccountPool,
  updateAccount,
} from "@/lib/accountPoolsApi";
import type { AccountPool } from "@/lib/accountPoolsApi";

const USAGE_PERIODS: readonly { label: string; days: number }[] = [
  { label: "Today", days: 0 },
  { label: "7 days", days: 7 },
  { label: "30 days", days: 30 },
];

/**
 * The harness subscription accounts, with their usage.
 *
 * A pool groups the accounts that serve one harness family; an account is one
 * credential home a session can run under. The rows carry the account's load
 * (active sessions), the sessions it ran in the selected period, its cost, and
 * the last known quota — the facts needed to decide which account to use.
 */
export function SettingsAccountsSection() {
  const queryClient = useQueryClient();
  const info = useServerInfo();
  const [periodDays, setPeriodDays] = useState(30);
  const { data: pools, isLoading, error, refetch, isFetching } = useAccountPools(true, periodDays);
  const [newPoolName, setNewPoolName] = useState("");
  const [newPoolHarness, setNewPoolHarness] = useState("");
  const [accountDrafts, setAccountDrafts] = useState<Record<string, { id: string; path: string }>>(
    {},
  );

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["account-pools"] });
  };

  const createPool = useMutation({
    mutationFn: () =>
      createAccountPool({ name: newPoolName.trim(), harness: newPoolHarness.trim() }),
    onSuccess: () => {
      setNewPoolName("");
      setNewPoolHarness("");
      invalidate();
    },
  });
  const removePool = useMutation({
    mutationFn: (poolId: string) => deleteAccountPool(poolId),
    onSuccess: invalidate,
  });
  const addAccount = useMutation({
    mutationFn: ({
      poolId,
      id,
      path,
      harness,
    }: {
      poolId: string;
      id: string;
      path: string;
      harness: string;
    }) =>
      createAccount(poolId, {
        id: id.trim(),
        home_path: path.trim(),
        home_kind: harness.startsWith("codex")
          ? "codex_home"
          : /^(antigravity|gemini)/.test(harness)
            ? "gemini_dir"
            : "generic",
      }),
    onSuccess: (_account, { poolId }) => {
      setAccountDrafts((current) => ({ ...current, [poolId]: { id: "", path: "" } }));
      invalidate();
    },
  });
  const toggleAccount = useMutation({
    mutationFn: ({ accountId, enabled }: { accountId: string; enabled: boolean }) =>
      updateAccount(accountId, { enabled }),
    onSuccess: invalidate,
  });
  const removeAccount = useMutation({
    mutationFn: (accountId: string) => deleteAccount(accountId),
    onSuccess: invalidate,
  });

  const totals = useMemo(() => {
    let accounts = 0;
    let active = 0;
    let sessions = 0;
    let cost = 0;
    for (const pool of pools ?? []) {
      for (const account of pool.accounts) {
        accounts += 1;
        active += account.usage.active_sessions;
        sessions += account.usage.sessions;
        cost += account.usage.cost_usd ?? 0;
      }
    }
    return { accounts, active, sessions, cost };
  }, [pools]);

  return (
    <div className="flex flex-col gap-6" data-testid="settings-accounts-section">
      <header className="flex flex-col gap-1">
        <h2 className="text-lg font-medium">Accounts</h2>
        <p className="text-sm text-muted-foreground">
          Subscription accounts a harness session can run under. The server picks one by load and
          quota unless a session pins it. Today uses UTC.
        </p>
        {info !== "loading" && info.codex_account_selection_policy === "quota_first" && (
          <p className="text-sm text-muted-foreground">
            Codex Auto: most remaining fresh quota, then fewest active sessions. Unknown quota
            follows known usable quota.
          </p>
        )}
      </header>

      <div className="flex flex-wrap items-center gap-3 text-sm" data-testid="accounts-totals">
        <span data-testid="accounts-total-count">{totals.accounts} accounts</span>
        <span data-testid="accounts-total-active">{totals.active} active sessions</span>
        <span data-testid="accounts-total-sessions">{totals.sessions} sessions in period</span>
        <span data-testid="accounts-total-cost">${totals.cost.toFixed(2)} in period</span>
        <Select value={String(periodDays)} onValueChange={(value) => setPeriodDays(Number(value))}>
          <SelectTrigger
            aria-label="Usage period"
            className="w-40"
            data-testid="accounts-period-select"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {USAGE_PERIODS.map((period) => (
              <SelectItem key={period.days} value={String(period.days)}>
                {period.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {isLoading && <p className="text-sm text-muted-foreground">Loading accounts…</p>}
      {error && (
        <div role="alert" className="text-sm text-destructive" data-testid="accounts-load-error">
          Could not load the accounts: {error.message}
          <Button
            type="button"
            variant="outline"
            onClick={() => void refetch()}
            disabled={isFetching}
          >
            Retry
          </Button>
        </div>
      )}

      {pools?.map((pool) => (
        <PoolBlock
          key={pool.id}
          pool={pool}
          draft={accountDrafts[pool.id] ?? { id: "", path: "" }}
          onDraftChange={(draft) =>
            setAccountDrafts((current) => ({ ...current, [pool.id]: draft }))
          }
          onAddAccount={() => {
            const draft = accountDrafts[pool.id];
            if (!draft?.id.trim() || !draft.path.trim()) return;
            addAccount.mutate({
              poolId: pool.id,
              id: draft.id,
              path: draft.path,
              harness: pool.harness,
            });
          }}
          onToggleAccount={(accountId, enabled) => toggleAccount.mutate({ accountId, enabled })}
          onRemoveAccount={(accountId) => {
            if (window.confirm(`Remove account ${accountId}? Credential files are kept.`))
              removeAccount.mutate(accountId);
          }}
          pending={
            addAccount.isPending ||
            toggleAccount.isPending ||
            removeAccount.isPending ||
            removePool.isPending
          }
          onRemovePool={() => {
            if (
              window.confirm(
                `Remove pool ${pool.name} and its account registrations? Credential files are kept.`,
              )
            )
              removePool.mutate(pool.id);
          }}
        />
      ))}

      {!isLoading && !error && pools?.length === 0 && <p>No account pools configured yet.</p>}
      {Object.entries({
        createPool: createPool.error,
        removePool: removePool.error,
        addAccount: addAccount.error,
        toggleAccount: toggleAccount.error,
        removeAccount: removeAccount.error,
      })
        .filter(([, failure]) => failure)
        .map(([operation, failure]) => (
          <p role="alert" key={operation} className="text-sm text-destructive">
            {failure?.message}
          </p>
        ))}
      <div className="flex flex-col gap-2 border-t pt-4">
        <h3 className="text-sm font-medium">New pool</h3>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            className="w-48"
            aria-label="Pool name"
            placeholder="Pool name (e.g. codex-native)"
            value={newPoolName}
            onChange={(event) => setNewPoolName(event.target.value)}
            data-testid="accounts-new-pool-name"
          />
          <Input
            className="w-48"
            aria-label="Pool harness"
            placeholder="Harness (e.g. codex-native)"
            value={newPoolHarness}
            onChange={(event) => setNewPoolHarness(event.target.value)}
            data-testid="accounts-new-pool-harness"
          />
          <Button
            onClick={() => createPool.mutate()}
            disabled={createPool.isPending || !newPoolName.trim() || !newPoolHarness.trim()}
            data-testid="accounts-create-pool"
          >
            <PlusIcon className="size-4" /> {createPool.isPending ? "Adding…" : "Add pool"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function PoolBlock({
  pool,
  draft,
  onDraftChange,
  onAddAccount,
  onToggleAccount,
  onRemoveAccount,
  onRemovePool,
  pending,
}: {
  pool: AccountPool;
  pending: boolean;
  draft: { id: string; path: string };
  onDraftChange: (draft: { id: string; path: string }) => void;
  onAddAccount: () => void;
  onToggleAccount: (accountId: string, enabled: boolean) => void;
  onRemoveAccount: (accountId: string) => void;
  onRemovePool: () => void;
}) {
  return (
    <section
      className="flex min-w-0 flex-col gap-3 rounded-xl border p-4"
      data-testid={`accounts-pool-${pool.name}`}
    >
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-medium">{pool.name}</h3>
        <span className="text-xs text-muted-foreground">{pool.harness}</span>
        <Button
          variant="ghost"
          size="icon"
          disabled={pending}
          onClick={onRemovePool}
          aria-label={`Remove pool ${pool.name}`}
          data-testid={`accounts-remove-pool-${pool.name}`}
        >
          <Trash2Icon className="size-4" />
        </Button>
      </div>

      <div className="flex flex-col gap-2">
        {pool.accounts.length === 0 && (
          <p className="text-sm text-muted-foreground">No accounts in this pool yet.</p>
        )}
        {pool.accounts.map((account) => (
          <div
            key={account.id}
            className="flex flex-col gap-1 rounded-lg border px-3 py-2 sm:flex-row sm:items-center sm:justify-between"
            data-testid={`accounts-account-${account.id}`}
          >
            <div className="flex min-w-0 flex-col">
              <span className="truncate text-sm font-medium">
                {account.id}
                {account.enabled ? "" : " · disabled"}
              </span>
              <span className="truncate text-xs text-muted-foreground" title={account.home_path}>
                {account.home_path}
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
              <span
                title={[
                  account.quota?.observed_at
                    ? `Checked ${new Date(account.quota.observed_at * 1000).toLocaleString()}`
                    : "Never checked",
                  account.quota?.five_hour.reset_at
                    ? `5h resets ${account.quota.five_hour.reset_at}`
                    : "",
                  account.quota?.weekly.reset_at
                    ? `Weekly resets ${account.quota.weekly.reset_at}`
                    : "",
                ]
                  .filter(Boolean)
                  .join(" · ")}
                data-testid={`accounts-quota-${account.id}`}
              >
                {accountQuotaSummary(account)}
              </span>
              <span data-testid={`accounts-active-${account.id}`}>
                {account.usage.active_sessions} active
              </span>
              <span data-testid={`accounts-sessions-${account.id}`}>
                {account.usage.sessions} sessions
              </span>
              <span data-testid={`accounts-cost-${account.id}`}>
                {account.usage.cost_usd === null
                  ? "no cost data"
                  : `$${account.usage.cost_usd.toFixed(2)}`}
              </span>
              <Button
                variant="ghost"
                size="sm"
                disabled={pending}
                onClick={() => onToggleAccount(account.id, !account.enabled)}
                data-testid={`accounts-toggle-${account.id}`}
              >
                {account.enabled ? "Disable" : "Enable"}
              </Button>
              <Button
                variant="ghost"
                size="icon"
                disabled={pending}
                onClick={() => onRemoveAccount(account.id)}
                aria-label={`Remove account ${account.id}`}
                data-testid={`accounts-remove-${account.id}`}
              >
                <Trash2Icon className="size-4" />
              </Button>
            </div>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Input
          className="w-40"
          aria-label={`Account id for ${pool.name}`}
          placeholder="Account id"
          value={draft.id}
          onChange={(event) => onDraftChange({ ...draft, id: event.target.value })}
          data-testid={`accounts-new-account-id-${pool.name}`}
        />
        <Input
          className="w-full sm:w-72"
          aria-label={`Credential home path for ${pool.name}`}
          placeholder="Credential home path"
          value={draft.path}
          onChange={(event) => onDraftChange({ ...draft, path: event.target.value })}
          data-testid={`accounts-new-account-path-${pool.name}`}
        />
        <Button
          variant="secondary"
          onClick={onAddAccount}
          disabled={pending || !draft.id.trim() || !draft.path.trim()}
          data-testid={`accounts-add-account-${pool.name}`}
        >
          <PlusIcon className="size-4" /> Add account
        </Button>
      </div>
    </section>
  );
}
