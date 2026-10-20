## Why

Account selection is currently invisible to the person driving the product. A
session is created with a harness (the coding CLI) and nothing else — the
server picks a subscription account behind the scenes. The operator cannot
see which accounts exist, cannot see their quota or load, and cannot pin a
specific account for a session.

The web app is also the mobile app (`web/src` is shared by the web, Electron,
iOS, and Android builds), so one picker covers every client. Every dropdown
that already offers a harness must also offer an account for that harness,
otherwise the account choice is only reachable through a raw API call.

## What Changes

- New account-pool client and query hook in the web app, following the
  existing `web/src/lib/*Api.ts` + `web/src/hooks/use*.ts` pattern.
- Every harness dropdown gains an account choice: the composer harness menu,
  the new-chat landing harness picker, the scheduled-task "Runs with" picker,
  and the project-settings agent picker.
- The account choice is a per-harness selection with an `Auto` entry that
  defers to the server's ranking (fewest active sessions, then known quota,
  then most remaining quota).
- Session creation sends the chosen account id, so the server pins that
  account instead of choosing one.
- Account rows show the account id, the home path, the enabled state, the
  last known quota windows, and the active session count.
- The picker degrades cleanly: a harness with no pool shows no account row,
  and a fetch failure leaves the harness picker fully usable.

## Capabilities

### New Capabilities

- `account-selection-ui`: account selection inside every harness dropdown,
  with auto-selection, quota and load display, and a degraded state when
  accounts are unavailable.

### Modified Capabilities

- None.

## Impact

- **Code**: new `web/src/lib/accountPoolsApi.ts`, new
  `web/src/hooks/useAccountPools.ts`, `web/src/components/composer/HarnessPicker.tsx`,
  `web/src/components/composer/HarnessMenuRow.tsx`,
  `web/src/shell/NewChatDialog.tsx`,
  `web/src/components/scheduled/CreateScheduledTaskDialog.tsx`,
  `web/src/shell/ProjectSettingsDialog.tsx`, and the session-create payload
  builders in `web/src/lib/sessionsApi.ts` / `web/src/shell/NewChatDialog.tsx`.
- **API**: the web app reads `/v1/account-pools`; session create gains an
  `account_id` field.
- **Mobile**: the same components render in the native builds; the account
  row is a menu item on mobile and a submenu on desktop, matching the
  existing harness/mobile split.
- **Tests**: picker tests for the account row, the auto entry, the quota
  summary, the degraded state, and the session-create payload.
