# M11 — Configuration precedence and tool authority

## Result

**PARTIAL — the frozen-authority, exact-review-subject, concurrency and runtime
tool checks passed; advertised settings still have capability-specific gaps.**
Exact executable candidate `80be109a` was deployed first as
`build-20260917-155053`. The deploy verifier confirmed that every application
pod digest and the serving backend revision matched the build, and the ingress
health check passed before verification began.

## What was proved

- Default, stored and invocation values compose through tighten-only rules.
  Unknown fields, endpoint or API-key injection, unsupported tools and deadline
  violations are refused. The accepted sanitized config, version and clamps are
  frozen into invocation and workflow metadata.
- A refused endpoint records only a generic endpoint-policy clamp. Frozen and
  returned snapshots contain neither the endpoint URL nor its hostname, even
  when another usable tier lets the invocation proceed.
- Worker start and retry use that frozen authority while reapplying current
  safety ceilings, endpoint policy and eval-drift pins. A changed project config
  refuses invocation retry rather than silently changing authority.
- Root Cause ReAct, Contract, Log Intelligence, Flaky Sentinel and Cluster tool
  calls consult the frozen allowlist. Root Cause cache reuse cannot import
  evidence produced with a broader tool grant, and its prioritized analysis
  honors `max_failures_analyzed`.
- Pipeline mutation requires the proposing agent's current `act` mode and an
  accepted review for the exact project, subject kind, report, subject type and
  pipeline. A merely accepted unrelated review is insufficient.
- AgentConfig writes require an entity tag and reject missing or stale versions.
  The reviewer-quality writer locks the row, uses the same versioned writer and
  abandons its update after a conflict. Investigator and Fixer adapters send the
  quoted version tag.
- Legacy Investigator/Fixer GETs retain their pinned projections. Retired PUTs
  return 405 even for empty or malformed JSON. MCP carries invocation overrides
  and displays the sanitized frozen snapshot; structured backend validation
  details reach the web configuration editor.

## Verification

- Focused backend authority set: **261 passed**; Appendix B backend set:
  **432 passed**.
- Frontend focused tests: **24 passed**; TypeScript passed and full-source ESLint
  had zero errors (19 pre-existing warnings).
- MCP focused: **20 passed**; MCP full: **177 passed**; CLI full: **138 passed,
  2 skipped**.
- Mutation: **25 wrong-behavior mutations killed**, with exact-once replacement
  assertions and byte restoration.
- Ruff passed; mypy held at **369 errors in 114 files** (baseline 369); all
  **43** quality guards and **238** guard self-tests passed; generated Agent API
  documentation matched the OpenAPI schema.

## Remaining gaps

Several capabilities still obtain models from global configuration, so their
accepted tier/endpoint settings are silent runtime no-ops. Ordinary workflow
execution does not yet consume every per-agent retry, timeout, review and budget
field. No durable attempted/denied/executed tool ledger exists for every
specialist, the CLI has no AgentConfig or invoke surface, and the optimistic
write checks were not raced against a real PostgreSQL pair. These gaps keep M11
PARTIAL and belong with the planned E5/E6 runtime integration rather than this
bounded authority repair.
