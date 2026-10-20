## ADDED Requirements

### Requirement: Database-backed account pools

The system SHALL persist account pools and their accounts in the database so
that account inventory survives a server restart and can be edited at runtime
without editing a configuration file.

A pool SHALL have a stable id, a name, a harness family it serves (for
example `codex-native` or `antigravity-native`), and a list of accounts. An
account SHALL have a stable id, a pool id, a credential home path, and an
enabled flag. A credential home path SHALL point at a directory on disk
(`codex_home` or `gemini_dir`); the system SHALL NOT store tokens, passwords,
or other secrets on an account row.

#### Scenario: Pool and account survive a restart

- **WHEN** a pool with two accounts is created through the API
- **THEN** the accounts are readable from the database after the server
  restarts, with the same ids and home paths

#### Scenario: Account id is unique per pool

- **WHEN** two accounts with the same id are added to one pool
- **THEN** the second add is rejected with an invalid-input error

#### Scenario: Account home path is normalized

- **WHEN** an account is created with the home path `~/.codex-acc2`
- **THEN** the stored path is the expanded, resolved absolute path

### Requirement: Account pool management API

The system SHALL expose a `/v1/account-pools` API for listing, creating,
updating, and deleting pools, and a `/v1/accounts` API for listing, updating,
and deleting individual accounts.

List responses SHALL include each pool's harness, each account's id, home
path, enabled flag, and — when known — the last observed quota windows and
the account's active session count.

#### Scenario: List pools with their accounts

- **WHEN** `GET /v1/account-pools` is called
- **THEN** the response contains every pool with its accounts, each account
  carrying `id`, `home_path`, `enabled`, and the last known quota windows

#### Scenario: Create a pool

- **WHEN** `POST /v1/account-pools` is called with a name and a harness
- **THEN** the pool is created and returned with its generated id

#### Scenario: Delete a pool removes its accounts

- **WHEN** a pool is deleted
- **THEN** its accounts are no longer returned by the pools API and are no
  longer eligible for session selection

#### Scenario: Account home must exist

- **WHEN** an account is created with a home path that does not exist on disk
- **THEN** the request is rejected with an invalid-input error naming the path

### Requirement: Session-time account selection from stored pools

At session creation the system SHALL select an account from the stored pool
that matches the session's harness. The selection SHALL honour an explicitly
pinned account id first. When no pin is given, the system SHALL rank the
pool's enabled accounts by: fewest active sessions (including pending launch
reservations), then known quota before unknown quota, then greatest remaining
five-hour quota, then greatest remaining weekly quota, then the account id
lexicographically. Accounts with exhausted or blocked quota SHALL be skipped,
and a session SHALL record the chosen account id and the account's home path
on the conversation.

#### Scenario: Explicit pin wins

- **WHEN** a session is created with an explicit account id that belongs to the
  pool for the session's harness
- **THEN** that account is used and its home path is applied to the session

#### Scenario: Unknown pin is rejected

- **WHEN** a session is created with an account id that does not exist
- **THEN** the request is rejected with an invalid-input error naming the id

#### Scenario: Pin conflicting with a home path is rejected

- **WHEN** a session is created with an account id whose home path differs
  from the home path already pinned on the session
- **THEN** the request is rejected with an invalid-input error

#### Scenario: Least-loaded account is chosen

- **WHEN** a pool has two enabled accounts and one of them already has two
  active sessions
- **THEN** the account with no active sessions is selected

#### Scenario: Known quota is preferred over unknown quota

- **WHEN** two accounts have the same active session count and one has a known
  quota while the other has an unknown quota
- **THEN** the account with known quota is selected

#### Scenario: Exhausted accounts are skipped

- **WHEN** every enabled account in a pool reports exhausted quota
- **THEN** session creation fails with an error naming the pool

### Requirement: Quota snapshots

The system SHALL record the last observed quota for an account and model
family, with the observation time, and SHALL treat a snapshot older than 120
seconds as unknown. Codex accounts SHALL be probed through the Codex
`account/rateLimits/read` app-server RPC; Antigravity accounts SHALL be read
from the local quota JSON under the account's `gemini_dir`. A missing probe
result, an unsupported provider, or a probe error SHALL leave the account's
quota unknown rather than inferring a value.

#### Scenario: Fresh snapshot is reported

- **WHEN** a quota snapshot is written for an account less than 120 seconds ago
- **THEN** the account's quota is reported as known with its five-hour and
  weekly remaining percentages

#### Scenario: Stale snapshot reads as unknown

- **WHEN** a quota snapshot for an account is older than 120 seconds
- **THEN** the account's quota is reported as unknown and the account is still
  eligible for selection

#### Scenario: Probe failure does not raise

- **WHEN** the quota probe for an account fails
- **THEN** the account's quota stays unknown and no error is raised to the
  caller

### Requirement: Per-account usage data

The system SHALL expose per-account usage so that account consumption can be
tracked. For each account the usage response SHALL include the number of
active sessions, the total number of sessions that used the account, the
total cost attributed to those sessions when cost tracking is enabled, the
last time the account was used, and the last known quota windows.

#### Scenario: Usage counts sessions by account label

- **WHEN** three sessions were created with the account id `codex-acc1`
- **THEN** the usage row for `codex-acc1` reports three sessions

#### Scenario: Unknown account reports zero usage

- **WHEN** an account has never been used by a session
- **THEN** its usage row reports zero sessions and no cost

### Requirement: Import of the legacy YAML account pool block

The system SHALL provide a command that reads a server configuration file
containing the legacy `account_pools:` block and writes those pools and
accounts into the database. The command SHALL be idempotent — an account that
already exists with the same id, home path, and enabled flag SHALL be left
untouched — and SHALL report the pools created, the accounts skipped, and any
parse error. The import SHALL NOT store secrets: a legacy entry carrying a
token-like key SHALL have that key dropped.

#### Scenario: Import creates pools and accounts

- **WHEN** the command runs against a config file with two codex accounts
- **THEN** the codex pool exists in the database with both accounts and the
  command reports two accounts created

#### Scenario: Re-running the import changes nothing

- **WHEN** the command runs twice against the same config file
- **THEN** the second run reports the accounts as skipped and no duplicate
  account rows exist

#### Scenario: Secret-bearing legacy entries are sanitized

- **WHEN** a legacy account entry carries an `api_token` field
- **THEN** the imported account has no token field and the command reports the
  dropped key

### Requirement: Removal of the YAML account pool configuration

The server configuration SHALL no longer accept an `account_pools:` block as a
source of account pools. When a configuration file still contains the key,
the server SHALL start normally, log a warning naming the import command, and
serve no pools from that key.

#### Scenario: Legacy key does not create pools

- **WHEN** the server starts with a config file containing an `account_pools:`
  block
- **THEN** no pool from that block is served and a warning naming the import
  command is logged

#### Scenario: Pools in the database are served regardless of config

- **WHEN** the server starts with no `account_pools:` key and the database
  holds one pool
- **THEN** that pool is served and eligible for session selection
