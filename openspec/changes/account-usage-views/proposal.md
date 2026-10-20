## Why

Accounts are operational assets: each one carries a subscription, a quota
window, and a share of the machine's running sessions. Today that state lives
only inside a YAML file and inside the session-create path, so nobody can
answer "which account is busy", "which account is exhausted", or "what did
this account cost this month" without reading logs.

The web app is also the mobile app, so one settings surface and one usage
view cover every client. The account inventory needs somewhere to live in the
product, not only in a config file.

## What Changes

- New Settings section "Accounts" listing pools and their accounts, with
  add, edit, enable/disable, and delete for both pools and accounts.
- The section shows per-account state: enabled flag, home path, active
  sessions, total sessions, cost, last used, and the last known quota
  windows.
- A usage view summarises account consumption: sessions per account, cost per
  account, quota windows, and the accounts that are exhausted or blocked.
- Both surfaces are responsive and usable on a mobile viewport, since the
  native clients share `web/src`.
- Pool and account creation from the UI writes to the same database tables
  the CLI and the import command use, so there is one source of truth.

## Capabilities

### New Capabilities

- `account-usage-views`: account inventory management and per-account usage
  reporting in the web and mobile clients.

### Modified Capabilities

- None.

## Impact

- **Code**: new `web/src/pages/settings/SettingsAccountsSection.tsx`, a
  registration in `web/src/shell/settingsNav.tsx`, account usage components
  under `web/src/components/usage/`, and the API client calls from the
  `db-backed-account-pools` change.
- **API**: reads `/v1/account-pools` (with usage fields) and writes through
  the pool and account CRUD routes.
- **Mobile**: the same components, laid out for a narrow viewport.
- **Tests**: settings section tests (list, add, edit, disable, delete) and
  usage rendering tests (sessions, cost, quota, exhausted state).
