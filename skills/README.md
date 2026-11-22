# TestLookup quality skills

Use the applicable entry point in a TestLookup checkout. Name the skill or ask the agent to read its linked `SKILL.md`. Select by the behavior under review and use the source and workflows at the candidate revision.

| Skill | Use it for |
| --- | --- |
| [E2E quality](testlookup-e2e-quality/SKILL.md) | Cross-component journeys, coverage history, defect verification, nightly results, and release readiness |
| [Backend](testlookup-backend-quality/SKILL.md) | FastAPI contracts, services, authorization behavior, and durable outcomes |
| [Frontend](testlookup-frontend-quality/SKILL.md) | React routes, state, accessibility, forms, charts, and browser regressions |
| [MCP](testlookup-mcp-quality/SKILL.md) | MCP tools, stdio and SSE transports, identity, and REST parity |
| [WebSocket and realtime](testlookup-websocket-quality/SKILL.md) | Live events, activity SSE, reconnect, ordering, and persisted completion |
| [Ingestion and workers](testlookup-ingestion-worker-quality/SKILL.md) | Report formats, Celery/beat, queue routing, retries, and outbox recovery |
| [Data and storage](testlookup-data-storage-quality/SKILL.md) | PostgreSQL, Alembic, MongoDB, Redis, object storage, retention, and recovery |
| [AI and agents](testlookup-ai-quality/SKILL.md) | Inference, retrieval, agent workflows, action approval, evaluation, and evidence |
| [CLI](testlookup-cli-quality/SKILL.md) | Commands, installed behavior, JSON output, receipts, and CI verdict exit status |
| [SDKs and reporters](testlookup-sdk-quality/SKILL.md) | Python, JavaScript, Java, Go, and native framework integrations |
| [Integrations](testlookup-integration-quality/SKILL.md) | External connectors, inbound notifications, outbound delivery, and reconciliation |
| [Deployment and operations](testlookup-deployment-quality/SKILL.md) | Profile-specific deployment, migrations, readiness, upgrade, and restore |
| [Observability](testlookup-observability-quality/SKILL.md) | Metrics, logs, traces, health probes, alert rules, and fleet-wide signals |
| [CI and release engineering](testlookup-release-engineering/SKILL.md) | CI triggers, pinned artifacts, release gates, and promotion evidence |
| [Contracts and documentation](testlookup-contract-docs-quality/SKILL.md) | Generated references, shared contracts, diagrams, and guides |

## Architecture quality attributes

| Skill | Use it for |
| --- | --- |
| [Security](testlookup-security-quality/SKILL.md) | Trust boundaries, actor and resource scopes, protected data, and defensive regression evidence |
| [Performance](testlookup-performance-quality/SKILL.md) | Timing, request and payload budgets, profiling, and comparable baseline measurements |
| [Auditability](testlookup-auditability/SKILL.md) | Attributable event history, decision evidence, export integrity, and retention |
| [Scalability](testlookup-scalability/SKILL.md) | Capacity, worker and pool headroom, skew, backpressure, and recovery |

## Apply the skills together

Start with the owning component and add the architecture skill for the changed constraint. Use E2E verification when the requirement crosses deployed boundaries.

| Change | Skills to combine | Evidence to retain |
| --- | --- | --- |
| Project switching or access rules | Frontend, Backend, Security | Scoped readback, expected permission result, and cache behavior after a switch |
| Live ingestion topology or worker concurrency | WebSocket, Ingestion and workers, Scalability, Observability | Resume result, durable test counts, queue recovery, and fleet headroom |
| AI-assisted release decision or compliance export | AI and agents, Backend, Auditability, E2E quality | Policy and evidence versions, approval record, and verified report outcome |
| Slow Runs page or ingestion processing | Frontend or Ingestion and workers, Backend, Performance | Comparable baseline and candidate, limiting stage, and a regression check |

For each gap, record the observed evidence, impact, proposed change, owner and measurable acceptance check. When implementing a fix, repeat the original comparison and include any outstanding verification. For release work, link the exact candidate, runner results and affected obligations in the [coverage ledger template](testlookup-e2e-quality/assets/coverage-ledger.md) or the existing ledger. Refresh the guide when a test entry point or verification contract changes.
