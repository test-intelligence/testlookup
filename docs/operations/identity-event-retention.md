# Instance identity event retention

Identity events have no project owner. Their expiry uses a separate instance policy, disabled until a signed-in instance administrator approves it. Enabling the existing instance or project retention policies does not enable identity expiry. No database migration is required: the versioned policy and independent hold registry live in the `identity_event_retention_policy` application setting.

Only the following records can expire:

| Record class | Eligibility |
|---|---|
| Routine authentication | `SSO_LOGIN`, `SSO_LOGOUT`, or `MFA_VERIFY_SUCCESS`, **and** `success = true`, strictly older than the approved cutoff. |
| SCIM authority | Every `SCIM_USER_CREATED` record stays retained. These records participate in SCIM user scope and SSO-managed MFA authorization. |
| Protected identity | Every other type, unknown future type, and unsuccessful routine record stays retained. This includes security failures, account and federation changes, and break-glass events. |

The permitted clock is 365–3650 days; the initial, disabled policy uses 2555 days, matching the existing audit-retention default. These are product bounds, not a determination of an organization's required retention period. Review security investigation, contractual and other applicable requirements before approving a clock. OWASP's [log disposal guidance](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html#disposal-of-logs) calls for retaining records for the required period and removing them when that period ends. This narrow policy does not expire protected identity records, settings audit receipts, backups, exports or copies in external logging systems; those still require their own lifecycle controls.

## Approval and one manual batch

The base path is `/api/v1/identity/retention`. An instance `ADMIN` may read policy and count previews. Mutations require that administrator's interactive JWT session. API keys cannot approve, disable, hold, release or purge; project-bound keys cannot access these instance endpoints at all. Keep reasons free of credentials and unnecessary personal information: they are permanent audit evidence.

1. `GET /policy` reads the policy, revision and independent holds. Missing policy means disabled; reading never creates an enabled policy.
2. `GET /preview?retention_days=365` returns aged counts by the three record classes, `observed_at`, a `preview_hash`, and exact confirmation phrases. It returns no event bodies, IP addresses or subject identifiers.
3. Submit `POST /policy` using the chosen days and the returned observation, hash and enable phrase. For example, the body shape for a 365-day approval is:

   ```json
   {
     "retention_days": 365,
     "observed_at": "<copy from preview>",
     "preview_hash": "<copy from preview>",
     "confirmation": "enable routine identity retention (365 days)",
     "reason": "Approved routine authentication retention policy"
   }
   ```

4. Preview again after approval, because policy approval advances its revision. To execute one manual batch, submit `POST /purge` with that fresh `observed_at` and `preview_hash`, the returned purge phrase, a reason, and `batch_size` between 1 and 500:

   ```json
   {
     "observed_at": "<copy from fresh preview>",
     "preview_hash": "<copy from fresh preview>",
     "confirmation": "purge routine identity events older than 365 days",
     "reason": "Execute approved routine authentication expiry",
     "batch_size": 500
   }
   ```

A preview is valid for ten minutes. Changes to policy, holds or the aged class counts invalidate it. A disabled policy, any live identity hold, unreadable policy, invalid confirmation or insufficient authority refuses deletion. `batch_full = true` means this batch reached its limit; it does not establish that all eligible records have been removed. Preview again to assess a backlog. Use `POST /disable` with `{"reason":"Suspend routine authentication expiry"}` to stop future batches without releasing holds or restoring already deleted records.

## Independent instance identity holds

Project holds do not apply to these unscoped events. Place a separate instance identity hold with `POST /holds` and a reason. `expires_at` is optional; an omitted expiry holds indefinitely. An expiry must be in the future and no more than 3650 days away. The response includes the hold UUID and its permanent placement audit UUID. Every live identity hold blocks all routine identity expiry, including scheduled batches.

Release a particular hold with `POST /holds/{hold_id}/release`:

```json
{
  "reason": "Investigation resolved and release approved",
  "confirmation": "release this identity retention hold"
}
```

Releasing one hold does not release any other hold. Expired holds stop blocking at their UTC expiry. Placement, release and policy changes each advance the policy revision and write an attributable settings audit receipt. The bounded current registry supports up to 100 live holds. Expired entries may be compacted on subsequent changes; their placement and release evidence remains in `settings_audit_log`, which TestLookup does not purge. Reaching the hold limit refuses an additional hold and preserves the existing holds.

All policy, hold and purge transactions lock the same policy row. An uncommitted hold serializes with a competing purge; once the hold commits, that competing purge reads the held policy and deletes nothing. A hold takes effect on commit and cannot recover a deletion that committed earlier. Do not treat merely submitting a hold request as proof it committed.

## Scheduled maintenance and storage safety

The existing whole-instance hourly retention task calls `identity_retention_service.run_scheduled_batch()`. It uses the existing instance `purge_window_start_utc` hour, default `02:00` UTC; midnight (`0`) is valid. It is independent of project retention enablement and never runs from an explicit project purge. Each eligible invocation attempts at most 500 routine events. It does not drain an arbitrarily large backlog in a loop. Scheduler or worker failure leaves records retained until another approved batch runs.

On PostgreSQL, the batch sets transaction-local lock and statement limits of two and five seconds. It locks the policy, rechecks all holds, selects a finite candidate set with `FOR UPDATE SKIP LOCKED`, and repeats the positive-success/type/cutoff predicates in the final deletion. Its permanent purge receipt and actual deletion share a SQL transaction. Failure before commit rolls both back. A malformed setting or unsupported record-class definition fails closed rather than widening deletion eligibility. The API commits before returning a successful purge result.

Inspect `settings_audit_log` under the `identity_event_retention_policy` and `identity_event_retention_purge` keys for policy revision, actor, reason, hold metadata and committed deletion counts. Scheduled purge receipts identify the service execution with `scheduled = true` and have no interactive actor; they use the previously approved policy revision. Receipts do not copy deleted event bodies, subject IDs or IP addresses. Existing direct-database permissions and backups remain operator responsibilities; these application controls do not make the database immutable.

Rolling back application code requires no schema downgrade. Earlier code does not run this new expiry path. Retain the setting and audit receipts for later inspection; disabling the policy explicitly before a rollback makes the intended state clear.

## Verification

From `backend`, run:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest -q -p pytest_asyncio.plugin tests/test_identity_retention.py tests/test_subject_data_inventory.py tests/regression/test_scim_unbound_token_scope.py
```

`tests/integration/test_identity_retention_postgres.py` additionally requires an explicitly configured disposable `TESTLOOKUP_POSTGRES_TEST_DSN` with the three existing source tables and permission to create a temporary test schema. It copies table definitions with PostgreSQL `LIKE`, inserts only synthetic rows into that generated schema, proves hold/purge serialization and SCIM preservation, reads committed audit receipts from a fresh session, and removes its schema. It does not modify public rows or verify account foreign keys. Never point this test at an application database containing real data.

The local native proof used PostgreSQL 16.15 with three synthetic source tables compiled from the current ORM definitions, without account foreign keys. It proves PostgreSQL row-lock behavior and bounded deletion/audit atomicity for the fixture, not migration compatibility, full account-schema behavior, backup lifecycle or a deployed scheduler. CI runs the integration test against its disposable migrated PostgreSQL database.
