## 1. Client layer

- [x] 1.1 Add `web/src/lib/accountPoolsApi.ts` with the typed pool/account shapes and the list/create/update/delete calls
- [x] 1.2 Add `web/src/hooks/useAccountPools.ts` with a query key per harness and a refetch-on-open option
- [x] 1.3 Add `web/src/lib/accountPreferences.ts` for the per-harness last-account memory, mirroring `harnessPreferences.ts`

## 2. Shared account menu pieces

- [x] 2.1 Add an account row component in `web/src/components/composer/HarnessMenuRow.tsx` (id, quota summary, active sessions, auto entry)
- [x] 2.2 Add the account submenu and the mobile account page to `web/src/components/composer/HarnessPicker.tsx`
- [x] 2.3 Cover the degraded state: no pool, fetch failure, and empty pool

## 3. Harness dropdown surfaces

- [x] 3.1 Composer harness menu (`web/src/pages/ChatPage.tsx` gear menu) — account entry for the running session harness
- [x] 3.2 New-chat landing (`web/src/shell/NewChatDialog.tsx` `AgentHarnessPicker`) — account entry per harness row
- [x] 3.3 Scheduled task dialog (`web/src/components/scheduled/CreateScheduledTaskDialog.tsx`) — account entry
- [x] 3.4 Project settings (`web/src/shell/ProjectSettingsDialog.tsx`) — account entry

## 4. Session create wiring

- [x] 4.1 Carry the chosen account id in the session create payload
- [x] 4.2 Carry the chosen account id in the scheduled task create payload
- [x] 4.3 Clear the remembered pin when the server rejects a deleted account

## 5. Tests

- [x] 5.1 Account row unit tests (known quota, unknown quota, exhausted, active sessions)
- [x] 5.2 Picker tests (auto default, account listed, no pool, fetch failure)
- [x] 5.3 Mobile drill-in page test
- [x] 5.4 Session create payload test asserting the account id is sent
