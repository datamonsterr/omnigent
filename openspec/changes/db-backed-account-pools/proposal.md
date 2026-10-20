## Why

Account pools today live in a hand-edited YAML block (`account_pools:` in the
server config) and are parsed by fork-only code in `omnigent/account_pools/`.
That makes every account change a server restart plus a hand edit, gives the
web and mobile clients no way to see or choose an account, and duplicates the
same list in two config files (`server-*.yaml` and `~/.omnigent/config.yaml`)
that can silently drift.

The account inventory is real operational state — it has identity, a home
directory, quota, and usage — so it belongs in the database next to the other
server-owned state, behind the same store/route/UI pattern the rest of
Omnigent uses.

## What Changes

- **BREAKING** — `account_pools:` in the server YAML config is removed.
  Pools move to the database; the YAML block is no longer read.
- New `account_pools` and `account_pool_accounts` tables plus a
  `account_quota_snapshots` table for the last observed quota per account.
- New `AccountPoolStore` (abstract + SQLAlchemy implementation) following the
  existing store pattern in `omnigent/stores/`.
- New `/v1/account-pools` CRUD routes and `/v1/accounts` routes for account
  CRUD, quota reads, and per-account usage.
- Account selection at session creation is served from the store instead of
  the parsed YAML config. The selection algorithm (explicit pin, fewest active
  sessions, known quota first, most remaining quota, stable id tie-break) is
  unchanged.
- The quota probes in `omnigent/account_pools/quota.py` (Codex
  `account/rateLimits/read` RPC, Antigravity local quota JSON) are kept and
  re-pointed at store-backed accounts.
- A one-shot import path moves an existing YAML `account_pools:` block into
  the database, so existing deployments do not lose their accounts.
- The fork-only `omnigent/account_pools/models.py` YAML parser
  (`parse_account_pools`) and the `RuntimeCaps.account_pools` config field are
  deleted.

## Capabilities

### New Capabilities

- `account-pools`: database-backed account pools — pool and account records,
  account CRUD API, session-time account selection, quota snapshots, and
  import from the legacy YAML config.

### Modified Capabilities

- None.

## Impact

- **Data**: new tables `account_pools`, `account_pool_accounts`,
  `account_quota_snapshots`; one Alembic migration.
- **Code**: `omnigent/db/db_models.py`, new
  `omnigent/stores/account_pool_store/`, `omnigent/server/routes/account_pools.py`,
  `omnigent/server/routes/_sessions/orchestration.py`,
  `omnigent/account_pools/` (selection manager and quota probes),
  `omnigent/cli.py` (server config wiring), `omnigent/runtime/caps.py`.
- **Config**: the server `account_pools:` YAML key is removed; a deprecated
  key produces a startup warning naming the import command.
- **Clients**: no client change in this change — the picker and usage views
  land in the follow-up changes.
- **Tests**: store CRUD tests, route tests, selection tests against the store,
  and a migration round-trip test.
