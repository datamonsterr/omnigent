# ADR 0001: Native Multi-Account Pools (database-backed)

## Status
Accepted

## Context

Omnigent agent workflows frequently use multiple sub-agent harnesses (Codex,
Antigravity, OpenCode, Claude) and benefit from distributing workload across
multiple subscription accounts. A deployment may hold five Codex accounts and
five Antigravity accounts, each with its own credential home, its own quota
window, and its own share of the machine's running sessions.

The first implementation of this feature (fork-only, `omnigent/account_pools/`)
kept the inventory in a server config block:

```yaml
account_pools:
  codex-native:
    accounts:
      - id: codex-acc1
        codex_home: /home/user/.codex
```

That worked for the selection algorithm, but it made the inventory
operationally invisible: adding an account meant editing a YAML file and
restarting the server, the same list had to be mirrored into
`~/.omnigent/config.yaml` as `providers:` entries (two sources of truth that
drift), and nothing in the API surface exposed accounts — so the web UI, the
mobile clients, and the orchestrating agent could not see or choose one.

The inventory is operational state: it has identity, a credential home, a
quota, and a usage history. It belongs in the database next to the other
server-owned state, behind the same store/route/UI pattern the rest of
Omnigent uses.

## Decisions

### 1. Account Pools Live in the Database

Three new tables (Alembic revision `mn1a2b3c4d5e`):

- `account_pools` — a named group of accounts that serves one harness family
  (`id`, `name`, `harness`, `description`).
- `account_pool_accounts` — one account: `id`, `pool_id`, `home_path`,
  `home_kind`, `enabled`.
- `account_quota_snapshots` — the last observed quota per account and model
  family, with its observation time.

An account row stores a *path* to a credential home (`codex_home`,
`gemini_dir`), never a token, password, or key. The `account_pool_store`
module follows the existing store pattern (abstract interface in
`omnigent/stores/account_pool_store/__init__.py`, SQLAlchemy implementation
beside it), scoped by the ambient workspace id like every other table.

### 2. The YAML Block Is Removed, With an Explicit Import Path

Keeping both a YAML block and database rows would leave two sources of truth —
the drift this change exists to remove. The `account_pools:` key is no longer
read: the server logs a startup warning naming the import command, and the
command converts a legacy block into rows.

`omnigent accounts import --config <file>` is idempotent (an account that
already exists with the same id, home path, and enabled flag is left
untouched), reports what it created and skipped, and drops any secret-looking
key from a legacy entry.

`omnigent accounts add` creates one account, and `scripts/setup_accounts.py`
provisions a whole spec file (pools created as needed, home paths verified).

### 3. Selection Algorithm Is Unchanged

Selection still runs at session creation and keeps its ranking:

1. **Explicit pin first** — the session's `omnigent.account.id` label (or a
   scheduled task's `account_id`) pins the account. A known id with a
   conflicting home is rejected.
2. **Fewest active sessions** — running and waiting sessions with a fresh
   runner heartbeat, plus pending launch reservations.
3. **Known quota before unknown quota** on equal session counts.
4. **Most remaining subscription quota** — five-hour window first, weekly
   second.
5. **Deterministic tie-break** — the account id lexicographically.

Accounts with exhausted or blocked quota are skipped, and a pool with no
eligible account fails the create instead of silently running unassigned.
The in-process reservation tracker is kept; cross-process coordination is not
supported. The reservation is released only after the new session's row is
verifiably readable by the counter (read-your-write): releasing it earlier
opens a window where a concurrent create sees neither the reservation nor the
row and picks the same account twice. A fresh store snapshot (under 120
seconds old) is used without probing, and the accounts of one pool are
probed concurrently, so a session create does not pay a probe per account.

### 4. Quota Is a Snapshot Table, Written by the Probe

Quota is a per-observation fact with its own freshness window (a snapshot
older than 120 seconds reads as unknown), so it gets its own table rather
than columns on the account. Codex accounts are probed through the official
`account/rateLimits/read` app-server RPC; Antigravity accounts read the local
quota JSON under the account's `gemini_dir`. A missing probe, an unsupported
provider, or a probe error leaves the quota unknown — no quota is inferred
from local attempts.

### 5. Clients Choose an Account; the Server Still Decides by Default

Every picker that offers a harness also offers an account for that harness
(new-chat landing, scheduled task, project settings). `Auto` is the default
and leaves the pick to the server. A chosen account rides the existing
`omnigent.account.id` label on session create, and the scheduled-task path
persists its pin in the new `scheduled_tasks.account_id` column.

The account inventory and its usage are rendered in a new Settings › Accounts
section, which is also where accounts are created, edited, disabled, and
removed.

### 6. Architectural Non-Goals

- No background rebalance daemon.
- No failover or retry scheduler.
- No cross-process reservation coordination.
- No credential storage — only credential home paths.
- No model-routing changes; smart routing picks the harness, the pool picks
  the account.

## Upgrade & Compatibility

- **Fork Baseline**: developed on `datamonsterr/omnigent`. The account-pool
  feature stays fork-local until it is upstreamed.
- **Migration**: run `omnigent accounts import --config <server.yaml>` against
  the deployment, confirm the pool list matches, then remove the
  `account_pools:` key from the config file. The import writes rows only, so
  dropping the tables and restoring the YAML block returns the deployment to
  the previous behaviour.

## Verification & Manual Testing Steps

### 1. Automated Verification

```bash
pytest tests/test_account_pools.py tests/test_account_pools_import.py \
       tests/test_setup_accounts_script.py tests/stores/test_account_pool_store.py \
       tests/server/test_account_pools_routes.py \
       tests/server/integration/test_account_pools_integration.py \
       tests/db/test_migration_account_pools.py -v
```

### 2. Manual Verification

1. Import the legacy block and list the accounts:
   ```bash
   omnigent accounts import --config ~/.config/omnigent/server.yaml
   omnigent accounts list
   ```
2. Start the server and open **Settings › Accounts**: the pools, their
   accounts, the quota windows, and the per-account usage are listed.
3. Start a session from the new-chat landing, open the harness config flyout,
   and pick an account. The created session's labels carry
   `omnigent.account.id` and the matching credential home.
4. Leave the account on `Auto` and start two sessions for the same harness:
   they land on different accounts, because the second one sees the first as
   active.
