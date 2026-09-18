# Cross-file integration checks: documentation handoff

Date: 2026-09-18. [Review summary](2026-09-18-review-summary.md).

“Source-aligned” below means contracts/wiring were read together and the documentation matches them. It is not a claim that the flow was run against live dependencies.

## Flow checks

| Flow | Files/boundaries checked | Result | Gap/risk and documented action |
|---|---|---|---|
| JSON/file ingestion | Router → schemas → shared identity/persistence → tasks → upload/run state | Source-aligned; representative typed examples and upload/status tests pass | 202 can precede persistence; file and JSON status/field parity must not be assumed |
| Live execution | Stream/live routers → Redis consumer/fan-out → drainer/recovery → canonical run navigation | Source-aligned wiring | No live multi-process/Redis delivery proof in this task; document lag and IDs |
| Run grading | File/live finalized aggregate use → run_status → model states | Source-aligned, selected tests pass | Empty/skipped/unknown are not equivalent to passed execution |
| AI execution | Registry/catalog → planner/config → graph → task/queue → stage/checkpoint/state | Source-aligned | No real model/child-worker execution; diagram compresses optional/parallel topology |
| Retry/resume | Enum/state service → retry policy/config → graph authority | Source-aligned, selected retry tests pass | Config changes require linked rerun where authority incompatible |
| Reports | Summary/decision projections → review policy → reports/export/share consumers | Source-aligned contracts | Observation mode is not enforcement; some responses lack structured schema |
| Release | Summary denominators → run/release attribution → append-only decision service | Source-aligned | Run verdict, release verdict and review projection differ; no live SQL evaluation proof |
| Identity | Bootstrap dual auth → resource guards → role/key binding → UI/CLI/MCP entrypoints | Structural guard tests pass | Tests are ratchets with explicit exemptions, not security certification |
| Feature flags | Scoped service → legacy facade → defaults/migrations → UI claims | Source-aligned | Different unknown-flag behavior; no blanket “all experimental off” claim |
| Deployment | Settings → Makefile/env generation → Compose variants → workers/schedules → homelab notes | Source-aligned; image drift check passes | Separate clone does not isolate fixed Docker services; confirm all actual consumers |
| Docs/contracts | Runtime OpenAPI → domain pages/index/schemas → ORM dictionary → legacy guide | Generated coverage with explicit untyped gaps | Regenerate after changes; source import is not DB migration proof |

## Contract checks

| Contract | Producer | Consumer | Observation |
|---|---|---|---|
| Public pipeline status | `workflow_run_state.public_status` | Agent/invocation clients | Pending/running/retry_wait map to in_progress; degraded completion uses metadata |
| Ingest status | `IngestResponse` and upload service | UI/CLI/SDK | Accepted IDs and file task lifecycle are separate from durable run outcome |
| Current AI review | Review envelope/distribution policy | Export/share/notification/CI/MCP | Preserve subject/state and current authority at outward delivery |
| JSON arrays | FastAPI Query declarations | Shared Axios client | Repeated bare keys, not bracketed names |
| API docs routing | main.py `/api-docs/openapi.json` | Ingress and Swagger | Keep schema under routed API-docs prefix; frontend owns `/docs` |
| Table inventory | ORM metadata | Dictionary and legacy schema guide | All 139 represented after 27-entry legacy repair |
| MCP/CLI inventory | Source registrations | README and integration guide | 67/11/7 MCP; 13 CLI groups plus standalone verdict |

## Integration findings

DOC-01 and DOC-02 corrected confirmed documentation drift. DOC-03 records incomplete typed response contracts without asserting runtime failure. DOC-04 proposes CI drift automation. The remaining operational checklist is a deployment-specific validation task, not hidden unfinished implementation work in this documentation change.
