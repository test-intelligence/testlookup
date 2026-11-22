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

### ISO/IEC 25010:2023 product quality characteristics

Select the skill for the affected characteristic and verify its acceptance criteria on the candidate revision. [ISO/IEC 25010 product quality model](https://www.iso.org/standard/78176.html)

| Characteristic | Subcharacteristics | TestLookup skill |
| --- | --- | --- |
| Functional suitability | Functional completeness, functional correctness, functional appropriateness | [Functional suitability](testlookup-functional-suitability/SKILL.md) |
| Performance efficiency | Time behaviour, resource utilization, capacity | [Performance](testlookup-performance-quality/SKILL.md) |
| Compatibility | Co-existence, interoperability | [Compatibility and interoperability](testlookup-compatibility/SKILL.md) |
| Interaction capability | Appropriateness recognizability, learnability, operability, user error protection, user engagement, inclusivity, user assistance, self-descriptiveness | [Interaction capability](testlookup-interaction-capability/SKILL.md) |
| Reliability | Faultlessness, availability, fault tolerance, recoverability | [Reliability and recovery](testlookup-reliability/SKILL.md) |
| Security | Confidentiality, integrity, non-repudiation, accountability, authenticity, resistance | [Security](testlookup-security-quality/SKILL.md) |
| Maintainability | Modularity, reusability, analysability, modifiability, testability | [Maintainability](testlookup-maintainability/SKILL.md) |
| Flexibility | Adaptability, scalability, installability, replaceability | [Flexibility and portability](testlookup-flexibility/SKILL.md), [Scalability](testlookup-scalability/SKILL.md) |
| Safety | Operational constraint, risk identification, fail safe, hazard warning, safe integration | [Safety and fail-safe behaviour](testlookup-safety/SKILL.md) |

### Cross-cutting architecture concerns

| Attribute | Use it for | TestLookup skill |
| --- | --- | --- |
| Resilience and disaster recovery | Dependency failure, degraded operation, replay, and restore objectives | [Reliability and recovery](testlookup-reliability/SKILL.md) |
| Observability and diagnosability | Correlated metrics, logs, traces, actionable alerts, and health probes | [Observability](testlookup-observability-quality/SKILL.md) |
| Operability and supportability | Incident triage, runbooks, maintenance, and operator handoffs | [Operability and supportability](testlookup-operability/SKILL.md) |
| Deployability and releasability | Profile rollout, schema migration, rollback, release evidence, and promotion | [Deployment and operations](testlookup-deployment-quality/SKILL.md), [CI and release engineering](testlookup-release-engineering/SKILL.md) |
| Extensibility and evolvability | Extension points, localized changes, stable test seams, and dependency boundaries | [Maintainability](testlookup-maintainability/SKILL.md) |
| Integrability and backward compatibility | Contract version pairs, adapter parity, coexistence, and supported upgrade paths | [Compatibility and interoperability](testlookup-compatibility/SKILL.md) |
| Elasticity and workload isolation | Scale-out and scale-in, per-project fairness, skew, and backpressure | [Scalability](testlookup-scalability/SKILL.md) |
| Data quality, durability, and consistency | Reconciliation, freshness, duplicate handling, lineage, and accepted data durability | [Data quality and durability](testlookup-data-quality/SKILL.md) |
| Privacy and data governance | Data minimization, redaction, configured egress, retention, holds, export, and erasure | [Privacy and data governance](testlookup-privacy-governance/SKILL.md) |
| Auditability and compliance evidence | Attributable event history, decision replay, export integrity, and retention | [Auditability](testlookup-auditability/SKILL.md) |
| Cost efficiency | Cost per completed operation, provider budgets, and comparable workload and rate evidence | [Cost efficiency](testlookup-cost-efficiency/SKILL.md) |
| Sustainability | Resource and energy use per completed workload for the selected deployment | [Sustainability and resource efficiency](testlookup-sustainability/SKILL.md) |
| AI quality and oversight | Evaluation, evidence grounding, human review, approvals, and safe release decisions | [AI and agents](testlookup-ai-quality/SKILL.md), [Safety and fail-safe behaviour](testlookup-safety/SKILL.md) |

## Apply the skills together

Start with the owning component and add the architecture skill for the changed constraint. Use E2E verification when the requirement crosses deployed boundaries.

| Change | Skills to combine | Evidence to retain |
| --- | --- | --- |
| Project switching or access rules | Frontend, Backend, Security | Scoped readback, expected permission result, and cache behavior after a switch |
| Live ingestion topology or worker concurrency | WebSocket, Ingestion and workers, Scalability, Observability | Resume result, durable test counts, queue recovery, and fleet headroom |
| AI-assisted release decision or compliance export | AI and agents, Backend, Auditability, E2E quality | Policy and evidence versions, approval record, and verified report outcome |
| Slow Runs page or ingestion processing | Frontend or Ingestion and workers, Backend, Performance | Comparable baseline and candidate, limiting stage, and a regression check |
| Live ingestion restart and restoration | Ingestion and workers, Reliability, Data quality | Accepted receipt, recovery timing, and reconciled durable results |
| SDK change with existing clients | SDKs and reporters, Compatibility, Functional suitability | Supported version pairs, shared contract assertions, and persisted result |
| Report export with a changed retention policy | Data and storage, Privacy and data governance, Auditability | Lifecycle policy, redacted output, and attributable event |

For each gap, record the observed evidence, impact, proposed change, owner and measurable acceptance check. When implementing a fix, repeat the original comparison and include any outstanding verification. For release work, link the exact candidate, runner results and affected obligations in the [coverage ledger template](testlookup-e2e-quality/assets/coverage-ledger.md) or the existing ledger. Refresh the guide when a test entry point or verification contract changes.
