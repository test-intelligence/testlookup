# Gate policies and CI gating

How to write the policies the release gate applies, control overrides and release phases, and block a pipeline on the verdict. Reading the gate (GO / CONDITIONAL_GO / NO_GO, the seven risk dimensions, the decision order) and working with releases are covered in the in-app guide: **Releases and gates** (`/docs/releases`, source [releases.md](../frontend/src/content/guide/releases.md)). Internals: [architecture/RELEASE_GATE.md](../architecture/RELEASE_GATE.md).

The verdict is deterministic and auditable: each decision stores a snapshot of every input it read and the policy version it was evaluated with, so a verdict from last month can always be explained in the terms that produced it.

## How the verdict is computed (plain-language version)

Four kinds of signal feed the decision:

1. **Risk dimensions** — seven weighted dimension scores combine into a composite risk score, compared with the policy's GO and NO_GO thresholds. A pass rate below the policy's minimum times its hard-floor factor is NO_GO whatever the composite says.
2. **Policy rules** — each rule evaluates to an action: `BLOCK`, `WARN` or `INFO`. Any `BLOCK` forces **NO_GO**; a `WARN` downgrades GO to **CONDITIONAL_GO**. Rules only ever push the verdict *down*.
3. **Pass-rate bands** — the pass rate is classified into your bands. A low band can drag a green composite down; a green band never lifts a red one.
4. **Flaky intelligence** — tests in active **quarantine** are excluded from the failure signal (when quarantine is on; see [Quarantine policy and CI gating](flaky-tests.md)).

When signals disagree, the gate takes the *worse* answer.

## Configuring policies

**Releases → Gate policies** (`/policies`). Each project resolves to one *effective* policy: its own active policy, or the system default. **Creating, editing, publishing, deactivating and simulating policies need an administrator**; QA leads can read them. (The page shows the editing controls to QA leads too, but the API refuses their changes.)

**New Policy** opens the editor. A policy has:

- **Metadata** — name, project (or **System default (every project)**; fixed once created), description.
- **Thresholds** — **GO Threshold** and **NO_GO Threshold** on the composite risk score (GO must be below NO_GO), **Pass Rate Minimum**, and **Hard Floor Factor** (the catastrophic floor is minimum × factor).
- **Pass-Rate Bands** — the orange, yellow and green lower bounds, increasing.
- **Rules** (**+ Add Rule**) — **Flaky Recurrence Limit**, **Open Defect Limit**, **Dimension Ceiling** (BLOCK or WARN only) and **Override Restrictions**, each with an action of **BLOCK**, **WARN** or **INFO**. Start new rules at `WARN`, watch a few runs, then promote them to `BLOCK`. `INFO` rules are recorded without changing the verdict — free telemetry on rules you are considering.
- **Hard caps**, **Failure-kind weighting** and **Dimension weights** (the weights must sum to 1.0).

**Save Draft** keeps a draft; **Publish** makes it the active version and deactivates the previous one. Published versions are read-only — to change one, edit it into a new draft. **Policy Simulator** evaluates the policy against a run id before you publish. Policy changes apply to future evaluations; historical verdicts keep the snapshot they were decided with.

The default policy is a starting point. Review it on a new instance.

## Overrides

A QA lead or administrator can override a verdict from the release gate (**Override decision** → choose GO, CONDITIONAL GO or NO GO → a written reason → **Apply Override**; API: `POST /api/v1/release-readiness/{run_id}/override`). The override:

- requires a **written reason**,
- is recorded **alongside** the computed verdict (the machine's opinion is never erased), listed in the gate's **History** tab, and audit-logged,
- can be restricted by the policy's **Override Restrictions** rule.

Use overrides for judgment calls, not as a recurring workaround — if you override the same rule weekly, change the policy instead.

## Release phase enforcement

Releases have phases (Planning, Development, Code Freeze, QA Testing, UAT, Staging, Production). By default a QA lead can complete or skip any phase, and **Mark as Released** only requires every phase to be completed or skipped.

The **`release_phase_gate_enforcement`** feature flag (ships **off**, deployment-wide) makes the server refuse to:

- complete a phase whose gate verdict is not GO, unless the request carries `gate_override_reason`;
- skip a phase, unless the request carries `skip_reason`.

Both reasons are audited. The release page does not send these fields yet, so with the flag on a user sees "Failed to update phase"; overrides go through the release-phase API (see `/api-docs`, the Releases section). Turn the flag on only if your process expects phase overrides to go through the API.

## Compliance packs

Generating a compliance pack for a release needs the `release_compliance_pack` flag (ships **off**; generating answers `503` until it is on). What a pack contains and its limits: [Compliance & governance](compliance.md).

## Gating a CI pipeline

The verdict is available over the REST API, so a pipeline can block a deploy step on it:

1. Upload the results ([Connecting CI and test frameworks](getting-results-in.md)) and keep the `run_id`.
2. Start a deep investigation of the run, which produces the release decision: `POST /api/v1/deep-investigate/{run_id}` (needs `QA_ENGINEER`; refused when the analysis mode is Rules).
3. Poll `GET /api/v1/release-readiness/{run_id}` until it stops answering `404` ("No release decision found"), then read `recommendation`.
4. Fail the job on `NO_GO` (and on `PENDING_REVIEW`, which appears when the human review gate is enforced and the report has not been accepted yet; see [AI configuration](ai-features.md#the-human-review-gate-for-ai-reports)).

```bash
curl -sf -X POST -H "X-API-Key: $TESTLOOKUP_API_KEY" -H "Content-Type: application/json" \
  -d '{"mode": "deep"}' "$TESTLOOKUP_URL/api/v1/deep-investigate/$RUN_ID"

for i in $(seq 1 60); do
  REC=$(curl -s -H "X-API-Key: $TESTLOOKUP_API_KEY" \
    "$TESTLOOKUP_URL/api/v1/release-readiness/$RUN_ID" | jq -r '.recommendation // empty')
  [ -n "$REC" ] && break
  sleep 10
done
echo "Release gate: ${REC:-none}"
case "$REC" in NO_GO|PENDING_REVIEW|"") exit 1 ;; esac
```

If you only need "fail on real failures, ignore quarantined ones" rather than the full release decision, use `testlookup ci-verdict` instead: [Gating CI on real failures only](flaky-tests.md#gating-ci-on-real-failures-only-testlookup-ci-verdict). The MCP server's `check_release_readiness` tool gives an AI agent the same readiness view.

## Tips

- **Read CONDITIONAL_GO, don't rubber-stamp it.** It exists for the "shippable but look first" case — the conditions list is the look.
- **A permanent NO_GO usually means un-triaged flaky tests.** Turn quarantine on and quarantine the genuinely flaky ones rather than loosening thresholds.
- **Keep INFO rules around.** They cost nothing and tell you how a rule would have behaved.
