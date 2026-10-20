## ADDED Requirements

### Requirement: Account choice in every harness dropdown

Every dropdown that offers a harness SHALL also offer an account choice for
that harness. This includes the composer harness menu, the new-chat landing
harness picker, the scheduled-task "Runs with" picker, and the
project-settings agent picker. The account choice SHALL appear on both the
desktop submenu layout and the mobile drill-in layout.

#### Scenario: Harness with accounts shows an account entry

- **WHEN** the user opens a harness dropdown for a harness that has a pool
  with accounts
- **THEN** the harness entry exposes an account choice listing those accounts

#### Scenario: Harness without a pool shows no account entry

- **WHEN** the user opens a harness dropdown for a harness that has no pool
- **THEN** the harness entry shows no account choice and remains fully usable

#### Scenario: Account choice is reachable on mobile

- **WHEN** the user opens a harness dropdown on a mobile viewport
- **THEN** the account list is reachable from the harness entry's drill-in
  page, not only from the desktop submenu

### Requirement: Automatic account selection entry

The account choice SHALL offer an `Auto` entry that leaves the account
unpinned so the server selects the account. The entry SHALL state the server's
ranking: fewest active sessions, then known quota, then most remaining
quota.

#### Scenario: Auto leaves the account unpinned

- **WHEN** the user selects `Auto` for a harness and then starts a session
- **THEN** the session is created without an account pin and the server
  selects the account by its ranking

#### Scenario: Auto is the default state

- **WHEN** a user has never chosen an account for a harness
- **THEN** the account choice shows `Auto` as selected

### Requirement: Account row detail

Each account row SHALL show the account id and its last known quota windows
when known, and SHALL distinguish an unknown quota from an exhausted one. When
a harness's account is currently serving sessions, the row SHALL show the
account's active session count.

#### Scenario: Known quota is shown

- **WHEN** an account has a fresh quota snapshot
- **THEN** its row shows the five-hour and weekly remaining percentages

#### Scenario: Unknown quota reads as unknown

- **WHEN** an account has no quota snapshot or only a stale one
- **THEN** its row shows the quota as unknown rather than a number

#### Scenario: Active session count is shown

- **WHEN** an account currently has two active sessions
- **THEN** its row shows two active sessions

### Requirement: Selecting an account pins it on the session

The system SHALL carry the chosen account id on the session create request
and the server SHALL use that account instead of running its own selection.
The picker SHALL remember the last chosen account per harness and SHALL clear
a remembered pin that the server rejects as unknown.

#### Scenario: Pinned account reaches the server

- **WHEN** the user picks account `codex-acc3` and starts a session
- **THEN** the create request carries the account id `codex-acc3`

#### Scenario: Remembered account is reused

- **WHEN** the user picks `codex-acc3` for a harness and later opens the same
  harness dropdown
- **THEN** `codex-acc3` is shown as the selected account

#### Scenario: Pinned account that no longer exists is reported

- **WHEN** the user starts a session with a remembered account that was
  deleted from the pool
- **THEN** the server rejects the create with an invalid-input error naming
  the account, and the picker clears the remembered pin

### Requirement: Cached account inventory

The client SHALL read the account inventory through one shared, cached query
so every picker sees the same accounts, and SHALL refresh it after the
settings surface changes an account.

#### Scenario: The inventory is shared

- **WHEN** two pickers are open at once
- **THEN** they read the same cached account list rather than issuing a
  request each

#### Scenario: A settings change refreshes the pickers

- **WHEN** an account is added in the settings surface
- **THEN** the next picker open shows the new account

### Requirement: Degraded account data

When the account data cannot be loaded, the harness dropdown SHALL remain
fully usable: harness rows, model, and effort choices SHALL keep working and
no account choice SHALL be shown.

#### Scenario: Account fetch fails

- **WHEN** the account list request fails
- **THEN** the harness dropdown renders without an account choice and the
  user can still select a harness and a model
