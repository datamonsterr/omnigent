## Context

The web app is one React codebase that also builds the Electron, iOS, and
Android clients (`web/src` is shared). Harness selection already has a
consistent shape:

- `web/src/components/composer/HarnessPicker.tsx:282` — `HarnessPickerEntry`
  renders one harness row; on desktop the row is a `DropdownMenuSub` whose
  flyout holds the harness config (model, effort, permissions), and on mobile
  (`isMobile`) it is a plain `DropdownMenuItem` that drills into
  `HarnessPickerConfigPage`.
- `web/src/shell/NewChatDialog.tsx:1379` — `AgentHarnessPicker` is the shared
  harness + agent dropdown, reused by the scheduled-task dialog
  (`CreateScheduledTaskDialog.tsx:451`) and the project-settings dialog
  (`ProjectSettingsDialog.tsx:461`).
- `web/src/pages/ChatPage.tsx:4571` — `SessionHarnessPicker` is the in-session
  gear menu (models and effort for the running session).

Selection state and propagation:

- The new-chat landing keeps `pickedHarness` in React state
  (`NewChatDialog.tsx:2583`) and persists the last choice per agent id in
  localStorage (`web/src/lib/harnessPreferences.ts:37`).
- Session create posts `harness_override`, `model_override`,
  `reasoning_effort`, and `labels` (`NewChatDialog.tsx:5786`).
- The server accepts `labels` and `harness_override` on the create request
  (`omnigent/server/schemas.py:1641`, `:1658`).

The server already pins an account when the request carries
`omnigent.account.id` in `labels` (`omnigent/account_pools/manager.py:271`),
so the picker can ship the account id as a label without a server change. A
first-class `account_id` field is still preferred, because a label key is an
internal contract.

The account data comes from the new `/v1/account-pools` endpoint (see the
`db-backed-account-pools` change).

## Goals / Non-Goals

**Goals:**

- One account choice per harness dropdown, on desktop and mobile.
- `Auto` as the default, so the server keeps its ranking unless the user
  pins.
- The picker shows enough state to choose with: account id, home path, quota,
  active sessions.
- A harness with no accounts, or a failed account fetch, never blocks harness
  selection.

**Non-Goals:**

- No account CRUD in the picker — that belongs to the settings surface.
- No live-updating quota in the picker beyond a refetch on open.
- No per-account model or effort choice.
- No change to the server's selection ranking.

## Decisions

### The account choice is a submenu row inside the harness entry, not a sibling row

The account belongs to a harness, so it is rendered inside that harness's
config flyout (desktop) or config drill-in page (mobile). A sibling row would
need its own "which harness does this account belong to" answer.

*Alternative considered*: a flat account list under the harness section.
Rejected — five Codex accounts and five Antigravity accounts in one flat list
is unscannable.

### `Auto` is a real entry, not the absence of a choice

`Auto` is an explicit first row. It states what the server will do (fewest
active sessions, then known quota, then most remaining quota) so the default
is legible instead of implicit.

### Account id rides the existing `labels` channel first

The create request already carries `labels`, and the server already honours
`omnigent.account.id` there. The picker therefore works against the current
server, and the `account_id` field can be added server-side later without a
client change.

*Alternative considered*: wait for a dedicated `account_id` field. Rejected —
it would block the UI on a server release for no behavioural gain.

### Accounts are fetched per harness, once per open

The picker fetches the pool list when the menu opens and caches it by pool
name. A failed fetch renders the harness rows without an account submenu and
logs nothing louder than a warning.

*Alternative considered*: fetch every account on every harness change.
Rejected — a menu open would fire a request per harness row.

### The account choice is remembered per harness in localStorage

`readLastHarness` / `writeLastHarness` already remember the last harness per
agent id. The same pattern stores the last account per harness, so a returning
operator does not re-pick.

## Risks / Trade-offs

- **Stale quota in the picker** → the picker shows the last known snapshot
  with its age; the server's own selection always uses a fresh-enough
  snapshot or treats it as unknown.
- **Extra menu depth on mobile** → the account list is a drill-in page, the
  same shape as the existing model/effort page.
- **A pinned account that no longer exists** → the server rejects the create
  with an invalid-input error naming the account; the picker clears the
  remembered pin on that error.

## Migration Plan

1. Ship the API client and the account row behind the availability of the
   `/v1/account-pools` endpoint.
2. Servers without pools render the picker unchanged.

## Open Questions

- Should the account row show the home path, or only the id and quota? Default:
  id and quota in the menu, home path in the tooltip and the settings view.
