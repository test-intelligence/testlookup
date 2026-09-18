# Multi-pass review of the documentation

Date: 2026-09-18. Source baseline: `44be1f2023d50bbbd9554bc91ce668c5c0789db5`. Reviewed documentation: `fa38afbf16bb1f12d899cac035447013e17b2344`, followed by the corrections in this review commit. Branch: `codex/documentation-handoff` in the separate `testlookup_docs` clone.

## Findings

The documentation required corrections. A confirmed application defect also prevents a blanket claim that every documented contract matches behavior. No live deployment was contacted.

| ID | Severity | Status | Finding |
|---|---|---|---|
| DREV-01 | P1 | Open | Release-filtered summaries mix scopes: only top-failing tests receive release_id; totals, suites and step metrics do not. |
| DREV-02 | P2 | Open | pass_rate_basis is always unique_tests, although latest mode and window fallback use run aggregates. |
| DREV-03 | P2 | Completed | Reporting prose conflated total-based Pass % with evaluated-only weighted pass rate, and omitted fingerprint deduplication. |
| DREV-04 | P2 | Completed | Legacy schema guide classified Mongo as non-authoritative and could imply a PostgreSQL-only backup preserves report evidence. |
| DREV-05 | P2 | Completed | Link checker skipped same-page headings, three top-level entrypoints and future-dated reviews; request validation used a separate hardcoded payload. |
| DREV-06 | P2 | Completed | Generator compared expected contents but did not detect or remove obsolete domain pages after tag/route changes. |
| DREV-07 | P2 | Completed | Exporter would pin links to changed legacy files such as architecture/DATABASE_SCHEMA.md to the old baseline, and permitted dirty content to link an older commit. This path was reproduced with a fixture; no existing broken wiki link is asserted. |
| DREV-08 | P3 | Completed | Data model guide treated db/mongo.py as the complete collection registry; pipeline_event_log is service-local. |
| DREV-09 | P2 | Open | 195 operations have at least one empty response media schema: 185 application/json and 10 application/scim+json. |
| DREV-10 | P3 | Completed | Added blocking handoff drift/check/test commands to the existing backend CI job after the repository guard required CI coverage. |

Full source links, fix direction and acceptance tests are in the [action register](2026-09-18-documentation-action-register.md). Seven documentation/tooling/CI corrections are completed; three application/API actions remain open. The highest-priority application defect is reproduced at the service-composition boundary, with aggregate/database helpers mocked; its deployed impact was not measured.

## Review method and status

| Area | Status | Scope |
|---|---|---|
| Pass 1: individual files | Completed within stated scope | Read maintained narrative pages, entrypoints and three tooling scripts; inspect generated-file coverage and selected emitted contracts independently |
| Pass 2: integration | Completed within stated scope | Trace reporting route/service/UI/PDF; compare ingestion/state/auth/review/egress/config claims to source and focused behavior tests; verify tool-to-artifact and wiki links |
| Pass 3: consolidation and corrections | Completed | Severity-ranked actions, seven corrections, negative tooling tests and reproducible evidence |
| Runtime release readiness | Unknown | No live DB, worker, frontend browser, restore, external connector or model-provider proof |

The [per-file ledger](2026-09-18-documentation-per-file-analysis.md) distinguishes manual narrative review from structural checking of generated pages. It does not claim manual semantic verification of every one of the 550 endpoint implementations, 471 schemas or 139 table migrations. Generated descriptions/docstrings are evidence of declarations; the release-filter defect demonstrates why they are not sufficient evidence of behavior.

## Verification

The first focused backend selection passed **242 tests** (summary service/router/rate basis/suite behavior, workflow state, run grading, invocation contracts and scoped flags). One dependency deprecation warning was emitted. Additional boundary and documentation checks are recorded in [verification](../handoff/verification.md#independent-documentation-review).

The [isolated probe output](2026-09-18-documentation-probe-results.json) records actual `build_summary_report` output and helper keyword arguments for both modes. For a controlled 100-test fixture it produced `pass_rate_pct=70.0`, `weighted_pass_rate_pct=77.8`, and `pass_rate_basis=unique_tests`. In both modes only `_top_failing_tests` received the requested `release_id`.

No production code, migrations or service configuration was changed. One documentation validation step was added to the existing backend CI job; hosted CI has not been run by this task. Runtime defects remain explicit follow-up actions. Historical reports are preserved; this review supplements their earlier conclusions.

## Next actions

1. Fix release propagation and basis metadata in a separate application change, using the concrete cases in DREV-01/02.
2. Add typed response/media contracts incrementally; verify the new documentation CI step on the next hosted run.
3. Run the [handoff acceptance checklist](../handoff/README.md#handoff-acceptance-checklist) against a disposable deployment before claiming operational readiness.
