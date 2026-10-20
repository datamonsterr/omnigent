## 1. Schema and store

- [x] 1.1 Add `SqlAccountPool`, `SqlAccountPoolAccount`, and `SqlAccountQuotaSnapshot` models to `omnigent/db/db_models.py`
- [x] 1.2 Add the Alembic migration creating `account_pools`, `account_pool_accounts`, and `account_quota_snapshots` with their indexes
- [x] 1.3 Add the `AccountPoolStore` abstract interface in `omnigent/stores/account_pool_store/__init__.py` (pool CRUD, account CRUD, harness lookup, quota snapshot read/write, usage rollup)
- [x] 1.4 Implement `SqlAlchemyAccountPoolStore` in `omnigent/stores/account_pool_store/sqlalchemy_store.py` using `make_named_managed_session_maker` with semantic query names
- [x] 1.5 Add the store export to `omnigent/stores/__init__.py`

## 2. Selection manager on store-backed pools

- [x] 2.1 Change `AccountPoolManager` to resolve pools and accounts from `AccountPoolStore` instead of an in-memory `AccountPoolsConfig`
- [x] 2.2 Port the ranking (active sessions, known-quota preference, remaining 5h/weekly quota, id tie-break) unchanged and keep the in-memory reservation tracker
- [x] 2.3 Re-point `omnigent/account_pools/quota.py` probes at store-backed account records and keep the 120 s freshness rule
- [x] 2.4 Update `prepare_session_account` to read pools from the store, honour the `omnigent.account.id` pin, and reject unknown or conflicting pins
- [x] 2.5 Delete `omnigent/account_pools/models.py` YAML parser and the `AccountPoolsConfig` config object

## 3. Server wiring and API

- [x] 3.1 Instantiate the account pool store in the server startup path and expose it on `app.state`
- [x] 3.2 Remove `RuntimeCaps.account_pools` and the `account_pools:` config read in `omnigent/cli.py`
- [x] 3.3 Add the `/v1/account-pools` CRUD router in `omnigent/server/routes/account_pools.py`
- [x] 3.4 Add the `/v1/accounts` router for account CRUD, quota reads, and per-account usage
- [x] 3.5 Update the session-create path in `omnigent/server/routes/_sessions/orchestration.py` to use the store-backed manager
- [x] 3.6 Warn at startup when a config file still contains the `account_pools:` key, naming the import command

## 4. Import command

- [x] 4.1 Add `omnigent accounts import --config <path>` to the CLI, reading the legacy YAML block and upserting pools and accounts
- [x] 4.2 Make the import idempotent and report created, skipped, and dropped-secret entries
- [x] 4.3 Add `omnigent accounts list` to print the stored pools from the CLI
- [x] 4.4 Add `omnigent accounts add --pool <name> --harness <harness> --id <id> --home <path>` to create one account from the CLI
- [x] 4.5 Add `scripts/setup_accounts.py` to bulk-provision accounts from a JSON/YAML spec file, creating pools as needed

## 5. Tests

- [x] 5.1 Store CRUD tests in `tests/stores/` covering pool and account lifecycle and uniqueness
- [x] 5.2 Route tests for the pool and account endpoints, including the invalid-home-path rejection
- [x] 5.3 Port the existing selection tests in `tests/test_account_pools.py` to the store-backed manager
- [x] 5.4 Update `tests/server/integration/test_account_pools_integration.py` for the store-backed path
- [x] 5.5 Migration round-trip test
- [x] 5.6 Import command test covering idempotency and secret sanitization

## 6. Docs

- [x] 6.1 Replace `docs/adr/0001-native-account-pools.md` with the database-backed decision and record the migration steps
- [x] 6.2 Document the API and the import command in `docs/`
