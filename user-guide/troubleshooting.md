# Troubleshooting (operators)

Symptoms that need someone with access to the deployment, configuration or settings pages. For symptoms an end user can check alone (an empty page, a project selector on All Projects, a narrow time window, counts that differ between pages), send them to **Troubleshooting** in the in-app guide (`/docs/troubleshooting`, source [troubleshooting.md](../frontend/src/content/guide/troubleshooting.md)).

## First checks

- **`/health/details`** on the API (for example `http://localhost:8000/health/details`) reports every dependency and the running build. `/health/ready` is the readiness probe; `/health/live` only says the process is up; `/health/version` is the build identity without probes.
- **Settings → Integrations → Integration health** shows whether each configured integration is reachable and delivering.
- **Settings → Security & access → Audit dashboard** shows whether an event or a settings change was recorded at all.
- The observability stack (metrics, traces, alerts) is described in [architecture/OBSERVABILITY.md](../architecture/OBSERVABILITY.md).

## A run shows totals but no per-test rows

The run finalised its totals but the per-test rows lagged or failed.

- **Just now?** Wait a few seconds; processing is asynchronous.
- **Persistently?** Per-test rows are queued to an ingestion **shard** queue, and every ingestion worker must subscribe to **all** shard queues. A worker listening only to the plain `ingestion` queue produces exactly this symptom. See [architecture/INGESTION_SCALE.md](../architecture/INGESTION_SCALE.md). A recovery task re-stages such runs within a couple of minutes once a worker picks the queue up.

## Suites or Coverage are empty while Runs is populated

Same root cause as above: the shard-queue subscription on the workers. It is not a problem with the upload.

## The Upload button is missing on Runs

The `manual_upload` feature flag is off. It ships on for a new install; an instance upgraded from an older version keeps its stored value. Turn it on under **Settings → System → Feature flags** (administrators only). See [Connecting CI and test frameworks](getting-results-in.md#1-upload-from-the-dashboard).

## A workflow page says it is turned off

Several workflows ship behind feature flags that are **off** on a new install: quarantine proposals (`flaky_auto_quarantine`), outbound webhooks (`outbound_webhooks`), GitHub checks (`github_checks`) and GitLab (`gitlab`). LLM cost-budget enforcement (`llm_cost_budget`), release phase enforcement (`release_phase_gate_enforcement`) and compliance packs (`release_compliance_pack`) are **on** for a new install but keep their stored value on an upgraded one, where they may still be off. The [feature-flag reference](../docs/operations/feature-flags.md) lists every flag and what it gates.

A flag that "does nothing" after you set it is usually one of:

- `enabled_global` is the master switch. A project list on its own does not turn the flag on.
- `rollout_percent` is 0.
- The flag value is cached for about 30 seconds; give it a moment.

## A webhook, Jira issue or chat notification did not fire

1. **Offline mode.** `AI_OFFLINE_MODE` defaults to `true`, and it is a hard switch above every integration setting: in a default install nothing outbound fires. Turn it off deliberately, or use the narrow allow-lists described in the [integrations reference](../docs/operations/integrations.md).
2. **The feature flag** for that integration (`outbound_webhooks`, `github_checks`, `gitlab`).
3. **Integration health** for reachability and the last error.
4. **The audit dashboard** for whether the event was recorded.

## AI features are unavailable

Rules and the ML classifier run offline by default. The **language-model** tier (Ask AI, narrative reports, generated test cases, investigation narratives) needs a reachable model:

- **No local model running.** On Compose, start the local-LLM profile (`make dev-llm`); on an air-gapped install, side-load the models ([Offline model pack](offline-model-pack.md)).
- **Analysis mode is Rules or ML.** Ask AI says so and stays hidden from the sidebar until **Settings → AI → AI configuration** uses LLM or Auto mode.
- **A hosted provider is configured while `AI_OFFLINE_MODE` is on.** Offline mode only lets the app reach a model endpoint whose address is loopback or private, so a hosted API is refused.

Analysis never hard-fails on a missing model; it falls back from the language model to ML to rules and records why. See [AI configuration](ai-features.md).

## Generated test cases look wrong or do not appear

- **Generation needs the language-model tier.** Offline without a model, it returns a stub.
- **Low faithfulness** is the signal to reject the draft: it is not supported by its citations. Read the cited passages.
- **No relevant results**: the wrong project, or the source has not been indexed yet (check its freshness). See [Knowledge base & test generation](knowledge-base.md).

## Changes are not showing in the app

A stale browser bundle. Hard-refresh. If it persists, confirm the frontend image actually updated (`/health/version` on the API reports the backend build; the Help menu shows the frontend build).

## Sign-in problems for everyone at once

- **`503` "MFA is enabled but its stored secret cannot be read"**: `APP_SECRET_KEY` was rotated without carrying the old key in `APP_SECRET_KEY_PREVIOUS`. See [Administration → If MFA stops working for everyone at once](administration.md#if-mfa-stops-working-for-everyone-at-once).
- **`503` on every request after sign-in**: the token-revocation store (Valkey) is unreachable. Revocation fails closed by design; restore the cache service.

## Related

- [Administration & settings](administration.md)
- [Sizing & capacity](sizing.md)
- [Offline model pack](offline-model-pack.md)
