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
  server's own pick.

## How a session gets its account

1. An explicit pin wins: the session carries `omnigent.account.id` (the
   picker, a project default, a scheduled task's `account_id`, or a
   per-dispatch `account`).
2. Otherwise the pool that serves the session's harness ranks its enabled
   accounts: fewest active sessions, then known quota before unknown, then
   the most remaining five-hour and weekly quota, then the account id.
3. Accounts with exhausted or blocked quota are skipped. A pool with no
   eligible account fails the create — a session never runs unassigned.

## Opt-in Codex quota-first selection

Set this non-secret server setting before starting the server:

```yaml
codex_account_selection_policy: quota_first
```

The default is `load_first`. Only Codex automatic selection changes: fresh
known usable quota first, then most remaining five-hour quota, then weekly
quota, then fewest active plus pending sessions, then account ID. Explicit pins
and other harness families retain their existing behavior. Settings → Accounts
and the Codex Auto tooltip show the active policy reported by `/v1/info`.

Quota observations must be no more than 120 seconds old and cannot be dated
in the future to rank as known. Unknown or failed probes remain fallback
candidates after fresh known usable accounts. A failed, unknown, or stale
refresh cannot clear prior exhaustion, blocked, or failed-auth evidence; that
account remains excluded until a fresh known observation permits it. Stale
positive quota is not proof of availability. The probes read quota metadata
without making paid inference calls. Applying server settings requires the
normal deployment lifecycle; do not restart an active instance for this change.

## Per-dispatch accounts

Orchestrators such as Polly and Debby can choose a pool account for each new
child session. For a declared worker, set `args.account` on its first named
`sys_session_send`:

```json
{"agent":"codex","title":"auth-fix","args":{"input":"Fix the auth bug","account":"codex-acc3"}}
```

For an existing registered agent, use `sys_session_create` with exactly one of
`agent` (the exact name from `sys_agent_list`) or `agent_id`. Names include
custom global agents registered from `~/.omnigent/agents` in the server catalog
(for example with `omnigent server --agent ~/.omnigent/agents/designer`).
You can combine the account pin with a model and reasoning effort:

```json
{"agent":"codex-3","account":"codex-acc3","model":"gpt-5.4","reasoning_effort":"high","title":"auth-fix"}
```

The tool writes `account` to the new session's `omnigent.account.id` label;
the server validates that the account exists, is enabled, serves the child's
harness, and has no known exhausted or blocked quota. Invalid explicit pins
fail creation without falling back to another account. The account's credential
home overrides the agent spec's saved home; conflicting homes explicitly
supplied in the same request are rejected. Omit `account` to let
the server choose from the pool. `config_path` creates do not accept `account`.

Drive a created child with `sys_session_send` using its returned
`conversation_id` as `session_id` and `args` as the message. Account pins apply
only at creation: both named continuations and sends by `session_id` reject
`args.account`. Continue without the pin, or close and create a new child to
select another account. The same creation-only rule applies to model and effort.

Named `sys_session_send` dispatches to Claude native and SDK workers also
accept the family aliases `sonnet`, `haiku`, and `opus`, plus `claude-sonnet`,
`claude-haiku`, and `claude-opus`. Dispatch normalizes these prefixed family
aliases to bare aliases. Versioned model IDs pass through unchanged, and Claude
workers still reject GPT models.

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
