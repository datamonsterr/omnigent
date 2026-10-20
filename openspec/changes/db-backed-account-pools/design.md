## Context

`omnigent/account_pools/` is a fork-only module. It parses a YAML block from
the server config (`omnigent/cli.py:4488` → `parse_account_pools`), keeps the
result on `RuntimeCaps.account_pools`, and the session-create path
(`omnigent/server/routes/_sessions/orchestration.py:11653`) builds an
`AccountPoolManager` from that config.

Today the operator edits `~/.config/omnigent/server-polly.yaml` by hand to add
an account, then restarts the server. The same list also has to be mirrored
into `~/.omnigent/config.yaml` as `providers:` entries. Nothing in the API
surface exposes accounts, so the web UI and the native clients cannot show
them, and no client can request a specific account.

The rest of Omnigent already stores server-owned state in the database behind
an abstract store (`omnigent/stores/`) and a router
(`omnigent/server/routes/`), and renders it in the web app (which is also the
mobile app — `web/src` is shared by the web, Electron, iOS, and Android
builds). Accounts should follow that shape rather than inventing a new one.

Constraints:

- Sessions are created on the server; the runner only receives
  `omnigent.codex.codex_home` / `omnigent.antigravity.gemini_dir` labels.
- Account identity must survive a server restart, so it cannot live in
  process memory.
- The selection algorithm is already validated by
  `tests/test_account_pools.py` and `tests/server/integration/test_account_pools_integration.py`;
  the behaviour must not change, only where the pool data comes from.
- Credentials never enter the database: a row stores a *path* to a
  credential home (`codex_home`, `gemini_dir`), never a token.

## Goals / Non-Goals

**Goals:**

- Accounts and pools are database rows, editable at runtime.
- Session-time selection reads from the database and keeps today's ranking.
- A CRUD API for pools, accounts, quota, and usage.
- The legacy YAML block imports into the database once, then the YAML path is
  removed.
- A single account list replaces the duplicated `providers:` block.

**Non-Goals:**

- No web/mobile UI in this change (follow-up changes).
- No change to the quota probe protocols or their timeouts.
- No rebalancing daemon, no failover scheduler, no cross-process reservation
  coordination.
- No credential storage — only credential *home paths*.
- No change to the runner's label contract.

## Decisions

### Store shape: one `AccountPoolStore` over two tables, not a generic key/value store

A pool is a named group of accounts bound to a harness family. Two tables
(`account_pools`, `account_pool_accounts`) keep pool metadata separable from
account rows and make the harness lookup a single indexed query.

*Alternative considered*: store pools as a JSON blob in `SqlPreference`.
Rejected — the picker needs per-account reads, and quota snapshots need their
own write cadence.

### Accounts are workspace-scoped rows, not conversation labels only

The session's chosen account still lands in the conversation label
`omnigent.account.id` (unchanged runner contract), but the *catalog* of
accounts lives in its own table. Deriving the catalog from labels would make
an account disappear when no session ever used it.

### Quota is a snapshot table, not a column on the account

Quota is a per-observation fact with its own freshness window (the current
code treats a snapshot older than 120 s as unknown). A separate
`account_quota_snapshots` table lets the probe write on its own cadence and
lets the selection path read the latest snapshot per account without
re-probing inside a request.

*Alternative considered*: probe on demand during selection. Rejected — the
Codex probe is an app-server RPC with a bounded timeout, and doing it inside
the create path would add seconds to every session create.

### Selection keeps its ranking; only the pool source changes

`AccountPoolManager` becomes store-backed: `select_account` loads the pool's
accounts from the store instead of an in-memory config, then applies the
unchanged ranking (active sessions, then known-quota preference, then
remaining 5 h/weekly quota, then id tie-break). The in-memory reservation
tracker stays — it protects against concurrent creates inside one process.
The reservation is released only after the new session's row is verifiably
readable by the counter, so a concurrent create cannot miss both the
reservation and the row.

### The YAML block is removed, with an explicit import path

Keeping both a YAML block and a database table would leave two sources of
truth. The YAML parser is deleted; a deprecated `account_pools:` key produces
a startup warning that names the import command, and the import command reads
the legacy block and writes rows.

*Alternative considered*: keep YAML as a read-only seed that syncs on boot.
Rejected — it re-creates the drift problem this change exists to remove.

### The `providers:` block in `~/.omnigent/config.yaml` is not migrated

`providers:` describes how a harness authenticates (CLI name, kind); accounts
describe which credential home a session runs under. They stay separate, but
the CLI import command reports the provider entries that look like accounts so
the operator can reconcile them by hand.

## Risks / Trade-offs

- **Concurrent creates across processes** → the reservation tracker is
  per-process, so two server replicas can still pick the same account. Same
  limitation as today; the DB row count is the durable source of truth for
  usage views.
- **Migration of a live deployment** → the import command is idempotent
  (upsert by pool name + account id) and reports what it skipped.
- **Query cost on the create path** → one indexed pool lookup plus one
  conversation scan per candidate account, the same scan the current code does.
- **Stale quota** → snapshots older than 120 s read as unknown, matching the
  current rule, so a stale row never hides an account from selection.

## Migration Plan

1. Ship the tables, store, and routes; keep the YAML path working.
2. Run `omnigent accounts import --config <server.yaml>` on the deployment.
3. Confirm the pool list matches the YAML block.
4. Flip the YAML block to a deprecation warning; remove the parser.

Rollback: the import command writes rows only, so dropping the tables and
restoring the YAML block returns the deployment to the previous behaviour.

## Open Questions

- Should account rows be workspace-scoped (multi-tenant) or machine-scoped
  (single-tenant servers)? Default: workspace-scoped, matching
  `SqlConnection` and `SqlProject`.
- Should a disabled account be retained with history, or deleted? Default:
  retain with a disabled flag so usage history keeps its label target.
