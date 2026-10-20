## 1. Settings section shell

- [x] 1.1 Add `SettingsAccountsSection.tsx` under `web/src/pages/settings/`
- [x] 1.2 Register the "accounts" section in `web/src/shell/settingsNav.tsx` and gate it on the server capability
- [x] 1.3 Render pools as groups and accounts as rows, with the usage facts inline

## 2. Management actions

- [x] 2.1 Create-pool form (name, harness)
- [x] 2.2 Create-account form (pool, id, home path) with client-side and server-side error surfacing
- [x] 2.3 Edit account (home path, enabled flag)
- [x] 2.4 Disable account
- [x] 2.5 Delete account, refused when the account has recorded sessions
- [x] 2.6 Remove pool

## 3. Usage view

- [x] 3.1 Period selector (today, 7 days, 30 days) driving the usage query
- [x] 3.2 Per-account usage row: active sessions, sessions in period, cost, last used, quota windows
- [x] 3.3 Exhausted and blocked states

## 4. Mobile layout

- [x] 4.1 Single-column layout under the mobile breakpoint
- [x] 4.2 Controls reachable without horizontal scrolling
- [x] 4.3 Mobile viewport test

## 5. Setup script

- [x] 5.1 `scripts/setup_accounts.py` reading a JSON or YAML spec file
- [x] 5.2 Pool auto-creation, home-path verification, secret rejection
- [x] 5.3 Report of created, skipped, and failed entries

## 6. Tests

- [x] 6.1 Settings section tests: list, add, edit, disable, delete refusal, pool removal
- [x] 6.2 Usage rendering tests: sessions, cost, quota, exhausted state, period change
- [x] 6.3 Setup script test
