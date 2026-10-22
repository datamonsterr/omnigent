# Subscription accounts

Accounts settings groups subscription credentials by harness and shows account
status, active sessions, and usage for Today (UTC), 7 days, or 30 days.

## Sub-features

- `inventory`: pools, enabled accounts, quota status, freshness, and usage totals.
- `period`: separate cached usage for Today, 7 days, and 30 days.
- `manage`: add pools/accounts, enable/disable, confirmed removal, pending and failure states.
- `pick`: Auto or an enabled account in custom-agent creation and composer configuration.
- `recovery`: visible loading, empty pools, failed fetch details, Retry, and retained failed drafts.
- `retired`: deployment-configured exact IDs excluded from new-session catalog and session-discovered choices.

## How to get to it (user POV)

- Desktop and mobile: Settings → Accounts; direct link `/settings/accounts`.
- New session: open the agent picker, Other…, Create custom agent, then choose Harness and Account.
- Settings → Custom agents → Create (or `/settings/custom-agents/new`) also saves an account default.
- New-session composer: open the selected harness configuration and its Account section.

## Driving it with the repro environment

Preconditions: follow [Verify Omnigent](skills/verify-omnigent/SKILL.md). Seed test
account pools using the admin account-pool API with disposable credential-home
directories. Enable `custom_agents_settings_ui` for Settings creation.

- Desktop and mobile Accounts: inspect groups and totals; change Today → 7 days
  → 30 days and verify each request includes its selected period. Simulate a
  failed inventory request, verify its detail, restore connectivity, and Retry.
  Try an invalid credential path: the error and draft remain until a successful retry.
- New-session creation: `tests/e2e_ui/start_session/test_create_custom_agent.py::test_create_agent_submits_multipart_bundle`
  covers desktop/mobile selection of the fifth account and the multipart account label.
- Settings creation: save a uniquely named custom agent with an account, reload
  its detail, and check its stored bundle default and a new registered-agent
  session's `omnigent.account.id` label. The settings save must create no session.
- Composer menus: verify Auto, enabled account choices, loading/error/Retry and
  an empty pool. Changing the custom-agent harness resets its account to Auto.
- Retired entries: configure `OMNIGENT_AGENT_PICKER_EXCLUDE_IDS` as comma-separated
  exact registry IDs before starting the server. Excluded catalog and previously
  discovered entries must disappear from the new-session picker, including pinned
  choices. Existing session retrieval and registry records must remain accessible.
- Focused components: `pnpm --filter web test src/pages/settings/SettingsAccountsSection.test.tsx src/components/composer/AccountConfigSection.test.tsx src/shell/CreateAgentDialog.test.tsx src/hooks/useAvailableAgents.test.tsx src/lib/accountPoolsApi.test.ts src/lib/agentBundle.test.ts`.

## Gotchas

- Profile IDs and credential account IDs are different inventories. Hiding a
  profile never deletes its credentials, historical sessions, or schedules.
- Picker exclusions are presentation configuration, not access controls. They
  do not remove agents from Settings management or block existing sessions.
- Agy thinking level is part of its model ID; keep all five credential accounts.
- A healthy API response does not establish the root cause of an earlier browser
  fetch failure. Record failed URL/status and distinguish injected faults from live failures.
- Existing sessions retain their assigned account; their configuration menu does not repin accounts. Start a new session to choose another account.
- Native quota discovery and hosted provider compatibility require separate proof.
