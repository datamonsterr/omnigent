## ADDED Requirements

### Requirement: Account inventory in settings

The system SHALL provide a settings section that lists the configured account
pools and their accounts, and SHALL allow an operator to create, edit, and
remove pools and accounts from that section. The section SHALL be reachable
in the web client and in the mobile clients.

#### Scenario: Pools and accounts are listed

- **WHEN** the operator opens the Accounts settings section
- **THEN** every pool is listed with its harness and its accounts

#### Scenario: An account is created from the UI

- **WHEN** the operator creates an account with a pool, an id, and a home path
  that exists on disk
- **THEN** the account is stored and appears in the list

#### Scenario: An account with a missing home path is rejected

- **WHEN** the operator creates an account whose home path does not exist
- **THEN** the UI reports the error and no account is stored

#### Scenario: An account is disabled

- **WHEN** the operator disables an account
- **THEN** the account stays listed, is marked disabled, and is no longer
  eligible for session selection

#### Scenario: An account is deleted

- **WHEN** the operator deletes an account
- **THEN** the account leaves the inventory and is no longer eligible for
  session selection, while its past sessions keep the account label

#### Scenario: An account is disabled instead

- **WHEN** the operator disables an account
- **THEN** the account stays listed with its usage history and is no longer
  eligible for session selection

#### Scenario: A pool is removed

- **WHEN** the operator removes a pool
- **THEN** the pool and its accounts disappear from the inventory and the
  accounts are no longer eligible for session selection

### Requirement: Per-account usage display

Each account row SHALL show the account's active session count, the number of
sessions that used it in the selected period, the cost attributed to those
sessions when cost tracking is enabled, the time the account was last used,
and the last known quota windows.

#### Scenario: Usage is shown per account

- **WHEN** an account ran three sessions in the period
- **THEN** its row shows three sessions and the attributed cost

#### Scenario: An unused account shows zero

- **WHEN** an account has never run a session
- **THEN** its row shows zero sessions, no cost, and no last-used time

#### Scenario: Quota state is shown

- **WHEN** an account has a fresh quota snapshot
- **THEN** its row shows the remaining five-hour and weekly percentages, and
  marks the account as exhausted when either window is exhausted

#### Scenario: The period can be changed

- **WHEN** the operator changes the usage period
- **THEN** the session counts and costs update to that period

### Requirement: Usable on a mobile viewport

The Accounts settings section and the account usage display SHALL be usable
on a mobile viewport: the layout collapses to a single column, every control
remains reachable, and the account rows show the same facts as on desktop.

#### Scenario: Narrow viewport layout

- **WHEN** the section is rendered at a mobile width
- **THEN** the pools, accounts, and usage facts are readable and the create,
  edit, and delete controls are reachable without horizontal scrolling

### Requirement: Account setup scripts

The system SHALL provide a script that provisions accounts from a
declarative spec file, creating pools as needed, and reporting what it
created or skipped. The script SHALL refuse to store secrets and SHALL
verify that each home path exists before creating the account.

#### Scenario: Spec file provisions accounts

- **WHEN** the script runs against a spec file declaring one pool with two
  accounts
- **THEN** the pool and both accounts exist and the script reports two
  accounts created

#### Scenario: Missing home path is reported

- **WHEN** a spec file names a home path that does not exist
- **THEN** the script reports that account as failed and creates the other
  accounts
