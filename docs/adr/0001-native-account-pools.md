# ADR 0001: Native Multi-Account Pools and Polly Dispatch Integration

## Status
Accepted

## Context
Omnigent agent workflows frequently use multiple sub-agent harnesses (Codex, Antigravity, OpenCode, Claude) and benefit from distributing workload across multiple subscription accounts.
Historically, credential and harness routing was attempted through an external metaharness (`jev_router_omniagent_mcp`). However:
1. Native Omnigent smart routing is disabled in this environment (`external: false`, `oss: false`).
2. Existing subscription provider aliases do not pick credentials or handle multi-account home environments.
3. Polly native session spawn (`sys_session_send`) provides first-class model and harness routing within the core runtime, eliminating the need for an external dispatch process.

This change introduces native multi-account pools directly into Omnigent's session orchestration without modifying smart routing or requiring schema migrations.

## Decisions

### 1. Account Pools Configuration
Account pools are defined in the server configuration (`server.account_pools`). Agent executor config values are strings in the native loader; nested pool mappings there are not supported.

```yaml
server:
  account_pools:
    codex:
      - id: codex-account-primary
        codex_home: /var/accounts/codex-1
      - id: codex-account-secondary
        codex_home: /var/accounts/codex-2
    antigravity:
      - id: agy-account-alpha
        gemini_dir: /var/accounts/agy-alpha/.gemini
      - id: agy-account-beta
        gemini_dir: /var/accounts/agy-beta/.gemini
```

- **Harness Mapping**: The pool is identified by harness family (`codex`, `antigravity`, or their native harness names).
- **Credential Hygiene**: Pools store local directory pointers (`codex_home`, `gemini_dir`) or credential references. Raw tokens or secrets are never stored in pool configs, conversation labels, or logs.

### 2. Selection Algorithm
Account selection executes strictly at session creation time:
1. **Explicit Pin First**: Explicit account IDs and native `codex_home`/`gemini_dir` paths pin identity. A known ID and conflicting home are rejected; a home absent from the pool remains unchanged without an invented ID. Existing sessions are never reassigned.
2. **Fewest Active Sessions**: Select the account with the minimum number of active sessions (sum of DB active sessions and pending in-flight reservations).
   - Active DB states: `running` and `waiting` require a fresh runner heartbeat. New never-reported/pending launches count for 120 seconds after creation; idle/failed/stale sessions do not count. Existing home-only sessions are matched to configured account paths.
   - Excluded DB states: `idle`, `stopped`, `disconnected`, `closed`, `failed`, `completed`, `archived`, or stale heartbeat.
3. **Known Quota Preference**: On identical active session counts, accounts with known remaining quota are selected before unknown accounts (unknown is not assumed unlimited). Accounts with explicitly exhausted quota or authentication errors are skipped.
4. **Most Remaining Subscription Quota**: Among accounts with known quota, pick by greatest primary remaining quota (5h rolling window), then secondary quota (weekly window).
5. **Deterministic Stable Tie-Break**: Lexicographical order of `account.id`.

### 3. Concurrency and Reservation Lifecycle
To prevent race conditions during concurrent session spawns:
- One manager per server process serializes count, rank, and reservation acquisition before DB persistence.
- The reservation transfers to the persisted startup-window count as soon as the conversation record exists (or releases on failure). Multi-process/server-replica reservation coordination is not supported by this in-memory patch.
- If session creation fails or errors out, the reservation is released immediately.

### 4. Runner Environment Forwarding
The selected account's environment paths are injected into session labels:
- Codex: `omnigent.codex.codex_home`
- Antigravity: `omnigent.antigravity.gemini_dir`
The existing native runner infrastructure forwards these labels to the spawned harness process.

### 5. Architectural Non-Goals
- No background rebalance daemon.
- No failover or retry scheduler.
- No dynamic model routing changes (native smart routing remains untouched).
- No database migrations (uses existing `conversation_labels` and `live_status`).
- No UI alterations.

### 6. Quota inspection and limits
Codex uses the official `account/rateLimits/read` app-server RPC with a bounded timeout and no inference. Antigravity reads fresh local quota JSON only: `<gemini_dir>/quota.json`, `<gemini_dir>/cache/quota.json`, or an explicit account `quota_cache` path. A cache is a mapping of account IDs to `gemini:five_hour`/`gemini:weekly` and `claude:five_hour`/`claude:weekly` remaining percentages, optionally wrapped as `{"ts": <epoch>, "accounts": {...}}`. It must be refreshed externally; this patch does not run an Antigravity quota polling daemon. Snapshots older than 120 seconds are unknown. The selected model family determines which quota applies. Missing probes/cache, unsupported providers (including OpenCode), and probe errors remain unknown; no quota is inferred from local attempts. In-memory probe results expire after 60 seconds and distinguish account paths and model families.

## Upgrade & Compatibility
- **Fork Baseline**: Developed on `datamonsterr/omnigent` against commit `4173ab3d` to preserve existing user patches.
- **Future Omnigent Upgrade**: Upstream Omnigent v0.18+ refactors parts of runner lifecycle and session metadata. Once upstream v0.18 stabilizes, we recommend porting this native account pool pattern rather than rebasing existing working checkouts in place.

## Verification & Manual Testing Steps

### 1. Automated Verification
Run the unit and integration suites:
```bash
pytest tests/test_account_pools.py tests/server/integration/test_account_pools_integration.py -v
```

### 2. Manual Verification
1. Start the server with account pools defined in `server_config.yaml`:
   ```bash
   omnigent server --config server_config.yaml
   ```
2. Spawn a session targeting the `codex` harness family:
   ```bash
   curl -X POST http://localhost:8080/v1/sessions \
     -H "Content-Type: application/json" \
     -d '{"agent_id": "<agent_id>", "title": "Codex Test 1", "harness_override": "codex-native"}'
   ```
3. Inspect the returned JSON labels:
   - `omnigent.account.id` is assigned (e.g. `codex-account-primary`).
   - `omnigent.codex.codex_home` points to `/var/accounts/codex-1`.
4. Spawn a concurrent or active second session:
   - Confirm it selects `codex-account-secondary` due to the active session count on `codex-account-primary`.
5. Spawn a session with explicit account pin:
   ```bash
   curl -X POST http://localhost:8080/v1/sessions \
     -H "Content-Type: application/json" \
     -d '{"agent_id": "<agent_id>", "title": "Pinned Test", "harness_override": "codex-native", "labels": {"omnigent.account.id": "codex-account-primary"}}'
   ```
   - Confirm `omnigent.account.id` remains `codex-account-primary`.
