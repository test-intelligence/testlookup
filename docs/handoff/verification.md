# Baseline, analysis scope and verification

[Documentation home](../README.md)

## Provenance

- Source repository: `https://github.com/test-intelligence/testlookup.git`.
- Source: GitHub `main`, commit `44be1f2023d50bbbd9554bc91ce668c5c0789db5`, merge dated 2026-09-18.
- Isolated clone: `C:\Users\anand\Downloads\Projects\testlookup_docs`.
- Documentation branch: `codex/documentation-handoff`.
- The concurrently used `testlookup_new` checkout was not switched or edited by this task.
- Only documentation, documentation tooling and scoped documentation ignore rules changed; no runtime application code, migrations or deployment configuration was modified; the independent review adds a documentation-only CI validation step.

The generator structurally inventories 2,841 tracked source/config/test files, parses 1,931 Python modules, exports 550 HTTP operations / 460 paths / 471 OpenAPI schemas, 139 SQLAlchemy tables, and 67 MCP tools / 11 resources / 7 prompts. The checker, exporter and documentation-tool test scripts are included in the structural inventory; the reference generator and review evidence artifacts are excluded. These counts are not a coverage percentage or a claim every branch was manually reviewed.

The per-file pass examined core routes, settings, models, persistence, worker/graph/retry/report/identity boundaries and clients. The cross-file pass traced principal flows and compared deployment/default/documentation contracts. [Per-file evidence](../reviews/2026-09-18-per-file-analysis.md), [cross-file checks](../reviews/2026-09-18-integration-checks.md).

## Checks

| Check | Result | Practical meaning |
|---|---|---|
| Application import and OpenAPI generation | Passed | Full registered app schema can be generated without entering lifespan or contacting services |
| `generate_handoff_reference.py --check` | Passed | Reconstructs generated contracts and compares exact text; settings isolated from local .env |
| `check_handoff_docs.py` | Passed: 122 pages / 4,448 links | New-suite local links/source lines, schema refs, operation coverage/counts and representative ingest examples |
| Selected backend tests | 124 passed | Run grading, retry policy, retry config, upload task status, route shadowing and architectural authorization |
| `gen_schema_docs.py --check` | Passed: 139 declared / 139 documented / 0 missing | Legacy schema entry coverage repaired; no live migration executed |
| `scripts/release/check_image_drift.py` | Passed | Compose/Kubernetes/OpenShift/mirror inventory agrees |
| `scripts/quality_gate.py` | Passed: 43 guards | Existing repository invariant ratchets passed; ratchets can retain baseline debt |
| Mermaid checker | Passed: 52/52 diagrams | Existing checker parsed all tracked diagrams after new diagrams were staged |
| Wiki export | Passed: 26 pages plus sidebar/footer | Rewrites narrative links and verifies local wiki targets; no remote wiki publication |
| `git diff --check` | Passed | Whitespace/error-marker hygiene only |

Selected test command (from repo root, backend on `PYTHONPATH`):

```bash
python -m pytest backend/tests/test_run_status.py \
  backend/tests/services/test_retry_policy.py \
  backend/tests/services/test_pipeline_retry_config.py \
  backend/tests/regression/test_upload_status.py \
  backend/tests/test_architectural_route_shadowing.py \
  backend/tests/test_architectural_authorization.py -q
```

Python 3.11.15 and existing installed backend dependencies were reused read-only; bytecode/cache writes were disabled or directed to task scratch. Runtime imports used the isolated clone's backend source. The existing Mermaid validator and its installed dependencies were pointed at this clone. Neither check used the other agent's application source as the system under documentation.

## Limits

No full backend/frontend/CLI/MCP suite, browser journey, fresh dependency installation, actual database migration, backup/restore, external connector, queue-consumption proof, load test or real-model evaluation was run. No deployment, push, pull request or wiki publication is implied by these checks. Existing historical QA evidence was not relabeled as new verification.

195 operations expose at least one unstructured response schema; the [gap inventory](../reference/api-contract-gaps.md) links handlers and records source return expressions without inventing wire schemas. All declared contracts are exported; undeclared business/error behavior still requires the service/handler and runtime contract tests. The [handoff acceptance checklist](README.md) is the next step for validating a specific environment.

## Final validation

The completed suite passed link/schema/example checks, deterministic regeneration, wiki local-link validation, and working-tree/staged whitespace checks. New documentation helper scripts also passed Ruff lint/format checks. The generated data dictionary renders partial-index predicates as SQL text, not process-specific object addresses. Top-level legacy guides now link to the current modular documentation.


## Independent documentation review

The follow-up [multi-pass documentation review](../reviews/2026-09-18-documentation-review-summary.md) inspected the delivered documentation at `fa38afbf`, cross-checked critical claims against the same application baseline, and corrected seven documentation/tooling/CI issues. It found two additional application reporting gaps, now documented explicitly. Earlier checks above remain historical evidence of the original delivery.

The first new backend selection passed **242 tests**; the second passed **225 tests**, for **467 total** with no overlapping test files. The second covers report/notification/comment distribution, archive parsing/upload status, offline egress, retry policy/config and authorization/route-order guards. One dependency deprecation warning occurred in the first selection. Both selections use this clone's backend source, existing Python 3.11.15 dependencies, disabled bytecode/cache output and disposable scratch test directories. Mocked service probes are not live application tests.

New regression checks run with `python scripts/test_handoff_docs.py`: they deliberately break heading/file links, the actual API example and endpoint multiplicity; exercise stale/manual generated files; and verify wiki commit routing and dirty/mismatched snapshot refusal.

No full suite, frontend browser/build, fresh install, real database migration/restore, live queue, external connector, model evaluation or production traffic was exercised in this follow-up.


| Follow-up check | Result / scope |
|---|---|
| Maintained documentation links/contracts/examples | Passed: 129 Markdown pages; all 550 HTTP operations, 471 schemas and 139 tables covered structurally |
| Documentation tooling negative tests | 9 passed; bad headings/files/examples/duplicates, obsolete/manual outputs, wiki commit/snapshot checks |
| Backend boundary selections | 467 passed (242 + 225); mocked/offline source tests, not live stack validation |
| Schema entry inventory | 139 declared / 139 documented / zero missing |
| Image drift | Passed across Compose/Kubernetes/OpenShift/mirror scripts |

The initial quality-gate rerun correctly failed because the new documentation test suite lacked a CI runner. A blocking step was then added to `.github/workflows/ci.yml`, reusing backend dependencies. Hosted CI execution is not implied by local validation.


Final local checks passed: deterministic reference regeneration; **129 pages / 4,831 local links** after adding the CI ledger link; **9 documentation tests** using the exact pytest command added to CI; **43 quality guards**; **52/52 Mermaid blocks** across 435 tracked Markdown files; Ruff lint/format for all four handoff scripts; staged/unstaged whitespace checks. Application source directories and deployment manifests have no diff against the source baseline. The schema and image inventory checks also pass. Wiki rewriting is covered by positive/negative fixture tests; remote publication and remote URL availability are not tested.

To reproduce the two backend selections from the repository root, set `PYTHONPATH` to `backend` and install the backend dependencies. Use disposable scratch/cache directories as appropriate:

```bash
python -m pytest backend/tests/test_summary_report_service.py backend/tests/test_summary_report_router.py backend/tests/regression/test_summary_report_rate_basis.py backend/tests/regression/test_summary_report_suite_window.py backend/tests/regression/test_summary_report_flaky_and_effective_suite.py backend/tests/services/test_workflow_run_state.py backend/tests/test_run_status.py backend/tests/test_agent_invocations.py backend/tests/test_agent_invocation_sync_sse.py backend/tests/test_agent_invocation_retry_cancel.py backend/tests/test_agent_invocation_idempotency.py backend/tests/services/test_feature_flags_service.py -q -p no:cacheprovider

python -m pytest backend/tests/test_distribution_gates.py backend/tests/test_notification_distribution_gates.py backend/tests/test_comment_distribution_gates.py backend/tests/regression/test_archive_upload.py backend/tests/regression/test_upload_status.py backend/tests/services/test_llm_offline_egress_pinning.py backend/tests/services/test_retry_policy.py backend/tests/services/test_pipeline_retry_config.py backend/tests/test_architectural_route_shadowing.py backend/tests/test_architectural_authorization.py -q -p no:cacheprovider
```


## Clean-checkout correction

The first hosted run after the documentation merge failed at reference regeneration because `docs/reference/api/agents.md` was absent from Git. The repository intentionally ignores `AGENTS.md`; with case-insensitive Git matching on the Windows authoring checkout, that also ignored the generated lowercase page. Earlier local checks saw the file on disk and therefore missed this publication gap.

The generated page is now named `agent-operations.md`, the index/review links follow that name, and `generate_handoff_reference.py --check` additionally requires every generated output to be tracked by Git. Drift failures now include a bounded textual diff. A regression test reproduces the original ignore rule with `core.ignorecase=true` in a disposable Git repository, proves the old file is rejected as untracked, and verifies the replacement can be staged and checked. No application behavior changes are part of this correction.

After reference generation passed on Ubuntu, the full backend suite exposed three additional documentation assertions: the main README must explicitly describe automatic model pulls, and both README entrypoints must enumerate all five role tiers. Those details were restored against the Makefile, `UserRole` enum, and authorization hierarchy. The existing model-setup and role-hierarchy regression modules are now included in the documented maintenance checks.
