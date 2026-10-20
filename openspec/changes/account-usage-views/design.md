## Context

The account data model and its API land with the `db-backed-account-pools`
change:

- `account_pools` — pool id, name, harness family.
- `account_pool_accounts` — account id, pool id, credential home path,
  enabled flag.
- `account_quota_snapshots` — the last observed quota per account and model
  family, with its observation time.

Usage facts come from two different places:

- Session counts and cost come from the conversation tables: the account id
  lives in the conversation label `omnigent.account.id`, the cost lives on the
  conversation row, and the active set is `live_status` plus runner liveness.
- Quota comes from the snapshot table, where a snapshot older than 120
  seconds reads as unknown.

The web app is shared with the Electron, iOS, and Android clients, so the
settings surface and the usage view are the same components at a narrow
viewport. The settings shell already has a section registry
(`web/src/shell/settingsNav.tsx`) with per-section components under
`web/src/pages/settings/`.

## Goals / Non-Goals

**Goals:**

- Manage pools and accounts from the product, not from a YAML file.
- See per-account usage: active sessions, total sessions, cost, last used.
- See quota and exhaustion state at a glance.
- One surface that works on desktop and mobile.

**Non-Goals:**

- No historical charts in this change — the usage view is a current-state
  table with a period total.
- No bulk import from the UI; the CLI import command covers that.
- No credential editing — a credential home path is the only editable field.
- No change to the quota probe protocols.

## Decisions

### One settings section, not a separate usage page

The account inventory and its usage are one operational object. Splitting
them would put "add an account" on one page and "is it busy" on another. The
section lists pools and accounts, and each account row carries its usage
summary inline; a period total sits at the top of the section.

*Alternative considered*: a dedicated usage page under the existing usage
navigation. Rejected — the operator's question is "which account should I
use", which is an inventory question.

### Usage is computed server-side

The server owns the conversation tables and the snapshot table, so the
usage rollup is a server query, not a client-side aggregation over a
paginated list. The route returns one row per account.

*Alternative considered*: the client fetches conversations and aggregates.
Rejected — that leaks a large table read to every client and cannot be
consistent with the server's own liveness rules.

### Deleting an account is a disable, not a destroy

A delete removes the account from the pool so it is no longer selectable, but
the account row is retained with `enabled = false` so historical sessions
keep a resolvable label target. The section exposes both: "Disable" toggles
the flag, "Delete" only appears for an account with no recorded sessions.

*Alternative considered*: hard delete. Rejected — it would orphan the label
target of past sessions and make usage history unreadable.

### The section is read-write on desktop and mobile alike

The forms are simple enough (name, harness, id, path) that a separate
desktop/mobile implementation is not warranted. The layout collapses to one
column on a narrow viewport.

## Risks / Trade-offs

- **Usage queries on a large conversation table** → the rollup is a single
  grouped query over the account label, scoped to the workspace.
- **Deleting a pool that has running sessions** → the sessions keep their
  account label; the pool disappears from the inventory but the running
  sessions are untouched.
- **Quota staleness in the UI** → each row shows the snapshot age; a stale
  snapshot reads as unknown rather than as a number.

## Migration Plan

1. Ship the section hidden behind the same capability flag the server
   advertises for account pools.
2. Operators move their YAML pools in with `omnigent accounts import`, then
  manage them from the UI.

## Open Questions

- Should the usage period be selectable (today, 7 days, 30 days)? Default:
  30 days, with today and 7 days available.
- Should the section be visible to non-admin users in multi-tenant mode?
  Default: admin only — account rows expose host paths.
