# Exploratory execution evidence templates

Use with [the package](EXPLORATORY_EXECUTION_PACKAGE.md),
[missions](EXPLORATORY_MISSIONS.md), and [runbook](EXPLORATORY_RUNBOOK.md).
Everything below is a template: **no execution results exist yet**.

Keep raw sensitive traces, HAR, auth storage, logs and environment files in a
private local evidence directory (for example under ignored `docs/`, with
restricted access). Commit only sanitized summaries/fixtures to
`architecture/verification/exploratory-<run-id>/` after reviewing contents.
Do not commit a template filled with fabricated timestamps, successes or URLs.
Create that result directory at execution time, not to imply work has run.

## 1. Programme/environment manifest

```yaml
programme_id: null
state: NOT_STARTED
coordinator: GPT-5.6 Sol
branch: codex/exploratory-quality-release
base_sha: null
candidate_sha: null
candidate_tree: null
planning_snapshot: 34110eace555423a4326b15e3ba502ac3ac2e4d4
started_at_utc: null
last_checkpoint_at_utc: null
environment:
  kind: null # disposable / homelab
  kube_context_name: null # name only, no credentials
  namespace: null
  frontend_origin: null
  backend_origin: null
  image_tag: null
  image_digests: {} # backend, frontend, MCP and each worker
  schema_head: null
  resources: {} # CPU/RAM/disk/model memory; no secret configuration
  fixture_version: null
  synthetic_project_ids: []
  account_role_aliases: {} # no passwords, tokens or real personal data
  review_enforcement: null
  draft_policy: null
  offline_mode: null
  model_tags_and_digests: {}
  feature_flags: {}
  external_test_sinks: {} # aliases/URLs only if safe to disclose
  backup_restore_evidence: null
baseline:
  source_sha: null
  commands_and_exit_codes: []
  pass_fail_skip_counts: {}
  coverage: {}
  mypy_baseline: null
  guards: null
  blockers: []
inventory:
  ui_route_to_mission: {}
  api_router_to_mission: {}
  cli_command_to_mission: {}
  mcp_tool_to_mission: {}
  unmatched_surfaces: []
progress:
  mission_ledger: missions.csv
  defect_ledger: defects.csv
  next_exact_action: null
  active_agent_file_ownership: {}
  injected_faults_to_restore: []
  global_settings_to_restore: []
release:
  pr_number: null
  latest_head_ci_evidence: null
  merge_sha: null
  final_homelab_evidence: null
  completion: NOT_STARTED
```

## 2. Mission ledger seed

Copy to `missions.csv` in the execution record. A row links to a mission record
and all its variants; one PASS row cannot hide missing variants. Distinguish
NOT_RUN, RUNNING, PASS, FAIL and BLOCKED. A disabled-feature exclusion needs
configuration/code evidence and rationale in its record; it is not live proof.

```csv
mission,priority,state,record,defects,blocker
M01,P0,NOT_RUN,,,
M02,P0,NOT_RUN,,,
M03,P0,NOT_RUN,,,
M04,P0,NOT_RUN,,,
M05,P0,NOT_RUN,,,
M06,P0,NOT_RUN,,,
M07,P0,NOT_RUN,,,
M08,P0,NOT_RUN,,,
M09,P1,NOT_RUN,,,
M10,P1,NOT_RUN,,,
M11,P0,NOT_RUN,,,
M12,P0,NOT_RUN,,,
M13,P1,NOT_RUN,,,
M14,P0,NOT_RUN,,,
M15,P1,NOT_RUN,,,
M16,P1,NOT_RUN,,,
M17,P1,NOT_RUN,,,
M18,P1,NOT_RUN,,,
M19,P1,NOT_RUN,,,
M20,P0,NOT_RUN,,,
M21,P0,NOT_RUN,,,
M22,P1,NOT_RUN,,,
M23,P0,NOT_RUN,,,
M24,P1,NOT_RUN,,,
M25,P0,NOT_RUN,,,
M26,P0,NOT_RUN,,,
```

### Per-session record

```text
Mission / iteration / charter:
Agent and start/end UTC:
Base/candidate/deployed SHA, image digests and schema:
Persona / role / project IDs / fixture hashes / browser / viewport:
Preconditions satisfied (evidence), missing dependencies:
Contract oracle (code/requirement reference):
Variants: happy, refusal, boundary, race, outage, recovery, accessibility:
Steps and exact command/test IDs:
Expected outcomes, including forbidden side effects:
Actual assertion results and first-attempt outcome:
Correlation / request / pipeline / task / subject / decision IDs:
Sanitized logs, trace, screenshot, sink payload and durable-state evidence:
Performance samples and measurement conditions (if applicable):
Fault/settings cleanup and verified recovery:
Defects / duplicate-of / product-gap decisions:
Result PASS / FAIL / BLOCKED:
Untested variants and next hypothesis:
```

## 3. Defect ledger and record

```csv
bug,mission,requirement,severity,state,unit_test,e2e_test,red_evidence,green_evidence,mutation,review,commit,homelab_retest
```

No dummy bug rows. States: CAPTURED → REPRODUCED → TESTS_RED → FIXED_LOCAL →
TESTS_GREEN → MUTATIONS_KILLED → REVIEW_APPROVED → COMMITTED → HOMELAB_VERIFIED.
These are evidence workflow labels, **not new application statuses**. If review
requests changes, return to FIXED_LOCAL and refresh all affected evidence.

```text
EXP-BUG-nnn — concise observed failure:
Mission / requirement / risk heuristic tags:
Severity and concrete user impact:
Discovered build and environment:
Minimal fixture and actor preconditions:
Reproduction numbered steps:
Expected (contract reference) versus actual:
Frequency / first failure / race ordering:
Evidence paths and sanitized trace/request IDs:
Root cause and affected callers/stores/interfaces:
Fix owner and exclusive file ownership:
Unit test path::name (smallest real seam):
Real E2E path::name (public entry → persisted/delivered outcome):
Why the E2E is not a mocked boundary-only test:
RED unit: source SHA/diff hash, command, exit, intended assertion:
RED E2E: source/image digest, command, exit, intended assertion:
Fix description, compatibility and failure-mode analysis:
GREEN unit: source/diff hash, command, result:
GREEN E2E: source/image digest, command, result:
Mutation: exact target/applied assertion/failure/restoration hash per mutant:
Adjacent risk retests and full regression manifest:
Docs/changelog/API/migration/guard updates (as applicable):
Independent reviewer and exact diff hash, verdict, resolved findings:
Commit SHA:
Homelab image digest, mission and retest result:
Remaining dependencies/decisions (none before complete):
```

Every confirmed defect needs both test fields and meaningful RED/GREEN evidence.
If the old build cannot be reproduced, investigate the environment until the
failure is understood; label inconclusive. Do not invent RED evidence or mark
the defect closed just because a speculative patch passes.

## 4. Review record

```text
Reviewer (different from patch author):
Bug IDs / exact branch HEAD plus uncommitted diff hash:
Files reviewed individually:
Cross-file flow reviewed:
Auth/scope, transactions, states, retry, versioning, data compatibility:
Unit test strength and meaningful fail-before proof:
Real E2E coverage, fixture ownership, skip/cleanup behavior:
Mutation application and killed assertion evidence:
Migration/guard/API/eval-attestation implications:
Findings (severity, file/line, trigger, impact, concrete correction):
Resolved findings with follow-up diff/result:
Verdict APPROVE / CHANGES_REQUIRED / BLOCKED:
Approved diff hash and UTC:
```

Approval is invalidated by changes to reviewed behavior/tests. Re-review the
affected diff before commit. No unresolved P0/P1 and no hidden P2/P3 defect
deferrals under an “all discovered defects fixed” claim.

## 5. Deployment, rollback and release evidence

```text
Candidate commit/tree and clean working-tree evidence:
Confirmed target context/namespace/URLs and ownership:
Previous images/digests/replicas/config/schema:
Backup and disposable restore verification:
Expected new migration head, compatibility/downgrade proof:
Build command, engine, generated tag, registry digest map:
Deploy command, exit, sanitized log path:
Migration job result and actual head:
Every application deployment/container expected image vs serving imageID:
Readiness/liveness/ingress/frontend/MCP/worker/beat results:
New E2E results and full mission/variant/browser matrix:
Performance baseline/candidate samples, declared budgets and recovery:
Alert fire/clear and queue reconciliation:
Rollback rehearsal trigger, exact digests, schema handling, RPO/RTO, smoke:
Final restored candidate proof:
Known blocked dependencies and owner decisions:
PR URL/head, all required CI/Codacy checks and review status:
Merge SHA/tree versus candidate tree:
Post-merge main CI and homelab smoke:
Programme complete / incomplete (precise reason):
```

## 6. Session handoff and final report

At each interruption save: current branch/HEAD, dirty file owners, active
processes, injected faults, last completed mission variant, next command,
pending review/decision and exact evidence pointers. Restore faults before
yielding unless a controlled experiment explicitly needs them and its owner
remains active. Never leave a shared environment degraded between sessions.

Final report should link the sanitized manifest and state:

1. PR/merge SHA and actual homelab image/schema.
2. Missions/variants executed, test/skip counts, coverage before/after.
3. Defects fixed and their unit + E2E + review/mutation evidence.
4. Live functional, integration, performance and recovery outcomes.
5. Deviations, disabled/blocked features, residual limitations and completion
   state. “No bugs found” in a session is not “all unknown defects eliminated.”
