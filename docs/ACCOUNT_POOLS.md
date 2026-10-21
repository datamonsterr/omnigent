# Harness accounts

A *harness account* is one subscription credential a coding session can run
under — a Codex login, an Antigravity profile, an OpenCode account. Omnigent
keeps the inventory of those accounts in the server database and picks one per
session.

## Where the inventory lives

| What | Where |
|---|---|
| Pools and accounts | `account_pools`, `account_pool_accounts` tables |
| Last observed quota | `account_quota_snapshots` table |
| The account a session used | the conversation label `omnigent.account.id` |
| The credential home the runner launches with | `omnigent.codex.codex_home` / `omnigent.antigravity.gemini_dir` |

A pool groups the accounts that serve one harness family (`codex-native`,
`antigravity-native`, …). An account row stores a *path* to a credential home;
no token, password, or key is ever stored.

## Managing accounts

```bash
# Import the legacy YAML block (idempotent).
omnigent accounts import --config ~/.config/omnigent/server.yaml

# List the pools and their accounts.
omnigent accounts list

# Create a pool, then add an account to it.
omnigent accounts pool --name codex-native --harness codex-native
omnigent accounts add --pool codex-native --id codex-acc6 \
    --home ~/.codex-acc6 --kind codex_home

# Enable / disable / remove.
omnigent accounts enable codex-acc6
omnigent accounts enable codex-acc6 --disabled
omnigent accounts remove codex-acc6
```

Bulk provisioning from a spec file:

```bash
python scripts/setup_accounts.py accounts.yaml --config ~/.config/omnigent/server.yaml
```

The spec file lists the pools and their accounts:

```yaml
pools:
  codex-native:
    harness: codex-native
    accounts:
      - id: codex-acc1
        home: /home/user/.codex
        kind: codex_home
```

The script creates pools as needed, verifies each home path exists, and is
idempotent.

## In the product

- **Settings › Accounts** lists the pools, their accounts, the quota windows,
  and the usage (active sessions, sessions in the period, cost). Accounts are
  created, edited, enabled, disabled, and removed there.
- **Every harness picker** (new-chat landing, scheduled task, project
  settings) offers an account for the picked harness, plus `Auto` — the
  server's own pick. A custom agent on the SDK `codex` harness uses the
  `codex-native` pool.
- **Custom agent forms** (Create custom agent, Settings › Custom agents ›
  New, and the agent's edit page) offer a default account under Harness
  when a pool serves it. It is stored as `executor.config.account_id` in the
  agent bundle (the account id, never a path) and edited with
  `PATCH /v1/agents/{id}` `{"account_id": "<id>" | null}`.

## How a session gets its account

1. An explicit pin wins: the session carries `omnigent.account.id` (the
   picker, a project default, or a scheduled task's `account_id`).
2. Otherwise the agent's default account (`executor.config.account_id`)
   pins the session. This applies to both the agent-id create and the
   multipart bundle create.
3. An explicit unknown, disabled, or wrong-harness account is rejected. A stale
   saved agent default falls back to the pool. A session account or home takes
   precedence over the agent default account and home.
4. Otherwise the pool that serves the session's harness ranks its enabled
   accounts: fewest active sessions, then known quota before unknown, then
   the most remaining five-hour and weekly quota, then the account id.
5. Accounts with exhausted or blocked quota are skipped. A pool with no
   eligible account fails the create — a session never runs unassigned.

The Agents rail shows the selected account, harness-reported model, and reasoning
effort for the main session and child rows. A requested model is not presented
as the child's actual model before the harness reports it.

The runner reads the chosen home from the session labels. Both `codex-native`
and the SDK `codex` harness bridge Codex auth and config from
`omnigent.codex.codex_home`.

## API

| Route | Purpose |
|---|---|
| `GET /v1/account-pools` | The pools with their accounts, quota, and usage (`?period_days=`) |
| `POST /v1/account-pools` | Create a pool |
| `GET/PATCH/DELETE /v1/account-pools/{pool_id}` | Read, update, remove a pool |
| `POST /v1/account-pools/{pool_id}/accounts` | Add an account |
| `GET /v1/accounts` | Every account with its usage |
| `GET/PATCH/DELETE /v1/accounts/{account_id}` | Read, update, remove an account |

See `docs/adr/0001-native-account-pools.md` for the full decision record.
