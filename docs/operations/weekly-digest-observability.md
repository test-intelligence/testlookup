# Weekly digest delivery observations

Weekly and weekly retro subscriptions are due at their persisted `next_delivery_at`, on any weekday. The normal dispatcher runs daily at 07:00 UTC; the additional Monday pass does not define every subscription's due date. Creation, schedule edits and resume calculate UTC timestamps. There is no subscriber-local timezone or calendar-Monday promise.

Migration `0262` adds nullable observation fields to each existing subscription. An atomic dispatch claim preserves the original unmet due slot in `pending_delivery_since_at`, records an attempt time and installs a unique observation token before advancing the scheduler's next date. A retry preserves that original slot. Generation records a separate content timestamp and `generated` phase. Only a provider call returning successfully can finalize `sent` and advance the existing successful-send watermark/count; generating content does not do so.

A known provider refusal remains `failed` with the original slot unresolved. An interrupted/ambiguous attempt stays unresolved as `generating`, `generated` or `unknown`; the observer never automatically replays it. Intentional unchanged-content or authorization skips finalize `skipped` and resolve the due slot without increasing delivered history. An explicit schedule edit or resume supersedes old attempt ownership and due state. Token checks stop an older completion from clearing the newer slot. Notification audit rows and their relay ownership remain unchanged.

The read-only `digests` scrape collector publishes aggregate snapshots, with bounded `schedule` (`WEEKLY`, `WEEKLY_RETRO`) and state/outcome labels:

| Metric | Meaning |
|---|---|
| `testlookup_weekly_digest_subscriptions{state}` | Counts of enabled/non-paused subscriptions: `enabled`, `due`, `unresolved`, `never_delivered`, `missing_schedule`. These states overlap; do not sum them into a total. |
| `testlookup_weekly_digest_oldest_overdue_seconds` | Age of the oldest effective due slot beyond 25 hours: the normal maximum daily dispatch lag plus one hour for processing. |
| `testlookup_weekly_digest_delivery_outcomes{outcome}` | Current subscriptions by most recent observed phase/outcome: `not_observed`, `generating`, `generated`, `sent`, `failed`, `skipped`, `unknown`. This is a snapshot, not an event counter. |

No subscription, user, project, address, URL or token is a metric label. Snapshots use the freshest successful per-target observation and are deduplicated with `max` across API replicas. A failed/expired collection invalidates its resource samples through the common collection-health contract. No enabled subscriptions yields measured zeros; a future first delivery is quiet even though it has never succeeded. An enabled subscription with no due date is a separate schedule defect.

`TestLookupWeeklyDigestDeliveryOverdue` alerts after a fresh positive overrun persists for 15 minutes. `TestLookupWeeklyDigestScheduleMissing` detects enabled rows without either a next due time or an unresolved claimed slot. Neither alert requires generation traffic or a Monday clock window. The old `testlookup_retro_digest_dispatch_total` remains a content-generation diagnostic for compatibility, with corrected help text.

The 25-hour allowance reflects the existing daily scheduling cadence; it is not a promise that delivery will occur precisely at the stored due timestamp. Provider `sent` means the provider call accepted the notification, not proof of inbox receipt, display or bounce-free email. Alert delivery, provider failures and ambiguous send reconciliation need their own operational evidence.

No historical claim state is backfilled: a future next date written by a pre-upgrade worker cannot prove whether it delivered, skipped or crashed. New claims gain durable observation after the migration and worker rollout. This change detects unresolved work but does not add durable message publication, an automatic unknown-send retry policy, or repair old ambiguous sends.

Validation uses real ephemeral SQLite transactions with synthetic providers to cover generation followed by refusal, successful retry, loss after claim, stale tokens, intentional skips, every weekday, UTC offset normalization, no subscriptions, missing schedules, and nullable migration upgrade/downgrade. Run:

```sh
cd backend
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p pytest_asyncio.plugin tests/regression/test_digest_delivery_observation.py tests/regression/test_digest_failure_retry.py
cd ../infra/monitoring/tests
promtool test rules digest-delivery.yml
```

A migrated disposable PostgreSQL deployment must additionally verify the same persisted claim/phase/completion states, actual `/metrics` samples, alert firing/resolution and a controlled provider acceptance/refusal. Local tests do not establish those deployment outcomes.
