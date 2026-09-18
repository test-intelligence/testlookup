# Baseline, analysis scope and verification

[Documentation home](../README.md)

## Provenance

- Source repository: `https://github.com/test-intelligence/testlookup.git`.
- Source: GitHub `main`, commit `44be1f2023d50bbbd9554bc91ce668c5c0789db5`, merge dated 2026-09-18.
- Isolated clone: `C:\Users\anand\Downloads\Projects\testlookup_docs`.
- Documentation branch: `codex/documentation-handoff`.
- The concurrently used `testlookup_new` checkout was not switched or edited by this task.
- Only documentation, documentation tooling and scoped documentation ignore rules changed; no runtime application code, migrations, deployment configuration or CI workflow was modified.

The generator structurally inventories 2,840 tracked source/config/test files, parses 1,930 Python modules, exports 550 HTTP operations / 460 paths / 471 OpenAPI schemas, 139 SQLAlchemy tables, and 67 MCP tools / 11 resources / 7 prompts. The two new checker/exporter scripts are included in the structural inventory; the reference generator itself is excluded. These counts are not a coverage percentage or a claim every branch was manually reviewed.

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
