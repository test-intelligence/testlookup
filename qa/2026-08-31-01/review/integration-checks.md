# Pass 2 — Cross-file integration checks

## RAG ingestion and staleness

DOCX extraction feeds the knowledge-sync/chunking path, so preserving body order is upstream of embeddings and cited retrieval context. The regression uses the actual `python-docx` object model rather than a mocked paragraph list. Staleness tests verify both citation-row mutation and propagation to `ManagedTestCase`, matching the RAG-12 implementation contract.

## HTML and notification rendering

The report renderer and email templates consume the same persisted executive-panel and release-signal fields. Both now escape the displayed value while retaining their existing allow-listed color behavior. Focused malicious-input tests cover each output surface, preventing a fix in one channel from leaving the other raw.

## Local setup and deployment tests

The shell utility environment is shared by quickstart and deployment regression subprocesses. Native Git Bash selection avoids the WSL launcher; the child PATH contains Git POSIX utilities; the generator’s Python selection is explicit and deterministic. All 20 local shell regression tests pass after these changes.

## Agent tool to Prometheus boundary

`fetch_app_metrics` receives model-generated JSON, builds PromQL, and can issue several range queries to a configured Prometheus instance. Validation now fails inside the tool contract before network access, range and expression fan-out are bounded, service labels are escaped, and timestamp windows are normalized to UTC.

## Application startup to OpenTelemetry boundary

`app.main` invokes tracing setup at import time whenever tracing is enabled, so telemetry initialization is part of the API startup path even though telemetry is optional. Resource, exporter, processor, and provider failures now emit a warning and leave tracing uninitialised instead of aborting import. Collector base URLs and already signal-qualified `/v1/traces` URLs both resolve to one trace endpoint.

## Webhook configuration to worker retry boundary

`WebhookSubscriptionWrite` and the Settings UI both permit `max_retries=0`, and the Celery task retries exactly when `webhook_service.deliver` returns `retry=True`. Delivery previously used a truthiness fallback, so zero became five and the worker scheduled unwanted retries. Network and retryable-HTTP branches now share an explicit budget check; non-retryable 4xx responses remain terminal `FAILED` outcomes.

## Primary workflows to webhook persistence boundary

Ingestion, quarantine, and quota callers already treat webhook fan-out as best-effort, while the direct defect-to-webhook flow relies on `emit_event`'s documented no-raise contract. Session, query, flush, and commit failures could previously violate that contract and fail the primary request. The webhook persistence boundary now logs a payload-free diagnostic and returns zero before dispatch, preserving the caller's operation.

## Committed deliveries to Celery dispatch boundary

`emit_event` commits one `WebhookDelivery` per matching subscription before enqueueing. Those publishes previously shared a single exception boundary, so one rejected publish abandoned all later committed rows as taskless `PENDING` records. Each publish now has its own diagnostic boundary, allowing later subscriptions to proceed.

## Webhook worker to delivery-service failure boundary

The worker previously converted every exception escaping `webhook_service.deliver` into `retry=False`. Because setup/database failures can occur before the delivery row is updated, Celery then acknowledged the task and left a durable `PENDING` row with no future work. Unhandled failures now enter the task's existing five-attempt exponential retry path.

## Secret storage to signed webhook egress boundary

`WebhookSubscription.has_secret` tells customers that signing is configured, while `secret_service.read_secret` deliberately returns `None` for missing or undecryptable values. Delivery previously treated that result like an unsigned subscription and posted without `X-TestLookup-Signature`. A configured-but-unavailable secret now marks the delivery `FAILED`, updates diagnostics/metrics, and blocks HTTP egress.

## Replay API to broker enqueue boundary

Replay commits a fresh audit row before publishing its task so workers cannot race an absent row. Broker rejection previously left that row `PENDING` and still returned its ID, causing the router and UI to report success. The service now reloads and marks the row `FAILED`, returns failure, and the router exposes queue failure through its existing 503 path.

## Integration settings to on-demand probe boundary

Scheduled/batch Slack and Teams probes resolve encrypted global webhooks from `integration_config_service`, but the single-provider API branch called probe functions without that configuration and fell back to environment settings. The handler now uses its request database session to resolve and pass the same runtime configuration authority used by notification delivery.

## Probe configuration to concurrent orchestration boundary

Batch probing loaded Slack/Teams database configuration before `asyncio.gather(return_exceptions=True)`, outside the isolation boundary. A configuration database fault therefore suppressed every independent probe. Resolution is now guarded: Slack/Teams return explicit `down` results without environment fallback, and sibling probes still execute concurrently.

## Probe API input to external fan-out boundary

The optional provider parameter previously used `if known else run_all`, so any non-empty typo triggered all configured external probes and persisted their results. The router now rejects unknown names with 422 and the supported list before invoking probe or persistence services.

## Probe persistence to Prometheus publication boundary

Current-status rows and Prometheus series represent the same probe verdict, but the series previously changed before the database transaction committed. A commit rejection therefore left metrics advertising a state absent from the API's authoritative store. Gauge updates and skipped-provider removals are now staged through the transaction and published only after a successful commit.

## Stored plan text to PDF parser boundary

Plan fields are persisted free text, while ReportLab `Paragraph` treats its input as markup. The strategy PDF already routed equivalent fields through `_pdf_text`; the plan PDF did not, and a stored `<b>` value reproducibly raised a parser `ValueError`. Name, status, description, and objective now use the same escape-and-newline normalization before rendering.

## Generated release metadata to PDF parser boundary

The release-report renderer escaped narrative fields but interpolated executive status, baseline classification, and provenance sources directly into ReportLab markup. Each field independently reproduced an export failure with malformed tags. Those values now use `_safe`, whose truncation order also matches the summary renderer so entity references remain complete.

## Stored test-case text to XLSX formula boundary

openpyxl interprets a Python string beginning with `=` as a workbook formula. The test-case export previously passed user- and AI-authored fields directly into cells, so persisted content could become active when a recipient opened the XLSX. A shared text-cell boundary now prefixes formula-looking values while leaving normal text and numeric cells unchanged.

## Audit event text to CSV record boundary

Audit export interpolated fields into comma-delimited lines without CSV quoting. An actor name containing commas or a newline therefore changed the exported schema and could create a forged-looking second record; a leading formula marker remained active in spreadsheet clients. Standard CSV serialization now preserves exact field boundaries and emits formula-looking text inertly.

## Validation evidence

- Frontend: 173 files and 1,233 tests passed; coverage collection, type-check, and lint passed.
- Backend focused validation: 84 tests passed, including RAG, report/email escaping, release artifacts, prompt registry, quickstart, and deployment regressions.
- Quality gate: all 31 guards passed.
- Mutation verification: DOCX order, report/email escaping, RAG staleness, prompt-attestation exit/fail-closed behavior, Prometheus input handling, tracing fail-open behavior, and OTLP endpoint normalization mutations each caused their regression tests to fail before restoration.

## Runtime limitation

Docker-backed integration/UAT flows were not available locally. Stateful upload, release, quarantine, connector, and account workflows still require a running deployed stack for final end-to-end evidence.
