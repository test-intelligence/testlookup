---
name: testlookup-backend-quality
description: Review and improve TestLookup FastAPI route, service, and domain tests. Use for backend changes to request and response contracts, authorization behavior, project scope, release decisions, and persisted state.
---

# TestLookup Backend Quality

## Locate the contract

- Identify the candidate ref and changed behavior. Start with `docs/architecture/codebase-map.md` and `docs/reference/api-index.md`, then follow `backend/app/bootstrap.py`, the mounted router, schemas, guards, service and serializer to affected Browser, CLI, SDK and MCP consumers.
- Identify the actor, selected project, resource authority, flag state and durable store. Check route precedence when adding literal paths alongside parameterized routes. Apply shared guards and status/error conventions from sibling routes. Trace any changes to pagination, filters, enums or defaults to existing clients and persisted records.
- Trace the successful change and its side effects across commits, data stores and queued work. Check that an authorization or validation refusal preserves the relevant state. Keep SQL operations on a shared `AsyncSession` sequential.

## Select proof

- Use a focused backend pytest case for parsing, rules, validation and shape. From `backend`, run `python -m pytest tests/<selected-test>.py` with the selected fixtures. Read `docs/operations/testing.md` and the test markers before selecting real-service integration cases for transaction boundaries, concurrent writes or query semantics. Check route and guard wiring through the mounted app when the contract depends on it.
- Use synthetic projects, identities and data to verify a permitted mutation and the expected refusal with unchanged state. Read committed results in a new session. For delayed work, trace acceptance through the worker or outbox to a terminal result. For repeated submissions, verify identity and side effects against the operation's idempotency contract.
- Keep contract artifacts in sync by using `scripts/generate_reference.py` and `scripts/check_reference_docs.py` when a mounted endpoint, schema or configuration changes. Trace critical cross-client behavior into the E2E catalogue.
- Report the revision, contract change, tests and stores exercised, the observed results, fixtures that blocked execution, and any remaining consumer or release obligation.
