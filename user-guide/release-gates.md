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

**Releases → Gate policies** (`/policies`). Each project resolves to one *effective* policy: its own active policy, or the system default. **Creating, editing, publishing, deactivating and simulating policies need an administrator**; QA leads can read them: for them the page is read-only (it says so), **New Policy** and the simulator's **Simulate** are disabled, and policies open in **View** with no **Deactivate**.

**New Policy** opens the editor. A policy has:

- **Metadata** — name, project (or **System default (every project)**; fixed once created), description.
- **Thresholds** — **GO Threshold** and **NO_GO Threshold** on the composite risk score (GO must be below NO_GO), **Pass Rate Minimum**, and **Hard Floor Factor** (the catastrophic floor is minimum × factor).
- **Pass-Rate Bands** — the orange, yellow and green lower bounds, increasing.
- **Rules** (**+ Add Rule**) — **Flaky Recurrence Limit**, **Open Defect Limit**, **Dimension Ceiling** (BLOCK or WARN only) and **Override Restrictions**, each with an action of **BLOCK**, **WARN** or **INFO**. Start new rules at `WARN`, watch a few runs, then promote them to `BLOCK`. `INFO` rules are recorded without changing the verdict — free telemetry on rules you are considering.
- **Hard caps**, **Failure-kind weighting** and **Dimension weights** (the weights must sum to 1.0).

**Save Draft** keeps a draft; **Publish** makes it the active version and deactivates the previous one. Published versions are read-only — to change one, open it and use **New version from this** (administrators): it creates a draft of the next version with the same scope, name, description and settings, verbatim, so the change starts from what is in force. **Policy Simulator** evaluates the policy against a run before you publish: pick the run from the searchable list of the policy's project's runs (the top-bar project's for the system default), or paste its id. Policy changes apply to future evaluations; historical verdicts keep the snapshot they were decided with.

**Versions** (a closed section under the editor's header, read-only, for every role that can open the policy) lists every version of the policy's scope with its state (`v4 · Active`, `v3 · Published`, `v5 · Draft`), who created it and when it was activated (`GET /api/v1/release-gate-policies/history/{project_id}`, or `/system-history` for the system default; each row carries `created_by_name` and `activated_by_name`). Tick two and **Compare** lists what differs, one line per setting: thresholds (`GO threshold 20 → 25`), pass-rate bands, hard caps, dimension weights, failure-kind weights and rules (added, removed, enabled or disabled, parameters changed). Compare before you publish; it is the review step the editor does not force.

The default policy is a starting point. Review it on a new instance.

## The pass-rate bands are the target line

The bands are also the line every page draws, so a reader sees the gate's own rule rather than a number of the page's choosing: the **GO line** is the yellow lower bound (`yellow_min`) and the **No-Go zone** is everything below the orange lower bound (`orange_min`). Trends draws the GO line as its target and shades the No-Go zone, Live flags its pass rate against the two lines, Home's **How this verdict is decided** names the policy (`Decided by Payments gate v4 (project policy): …`), and the release-gate page names the band policy beside the stored decision's. With no published policy, Trends and Live draw the default bands (yellow 95, orange 90, green 99), labelled **default bands**; Home's verdict is then the built-in rule, not the bands, and it says so (`Decided by the built-in rule (no release-gate policy is published for this project or the deployment): Go at 95% or more with no open defects and at most 5 flaky tests, Conditional from 85% with at most 5 open defects, otherwise No-Go.`).

This moved Trends' target line from a fixed 90% to the GO line: 95% under the default bands. To keep a 90% line, publish a policy with `yellow_min` 90. The dashboard summary (`GET /api/v1/metrics/summary`) names the resolved policy in `readiness_policy` (`id`, `name`, `version`, `level` `project` or `system`, and the three bands); it is `null` when the window has no execution evidence or no policy resolves, which Trends and Live read as the default bands. The summary is cached for 60 seconds, and publishing a policy refreshes it.

## Attribution rules

Which release a run belongs to decides which release gate it feeds, so attribution is part of gating. When a run arrives, the server links it by the first rung that applies: (1) the release the run names (`release_name`, `--release`, `TESTLOOKUP_RELEASE`), (2) a GitHub milestone on its pull request, (3) the project's attribution rules, top to bottom, (4) a release whose cut-off window holds the run, (5) the release that was active when it ran. A run that no rule matches still lands by rung 4 or 5, and a rule never moves a run that is already linked.

**Releases → ⋯ → Attribution rules** (`/releases/attribution-rules`, one project) manages the rules: QA leads and administrators create, edit, enable or disable and delete them; everyone else reads them. A rule matches one field: **Branch**, **Build number** and **Tag** with the wildcards `*` and `?`, **Environment** exactly (case-insensitive). The lowest **Priority** runs first; equal priorities run in creation order; the first enabled match wins. A rule targets a release by **name**, and a name no release has yet is created by the first matching run.

**Preview** (`POST /api/v1/projects/{project_id}/attribution-rules/preview?detail=true`) runs the rule against the last 200 runs without saving it and reports what would really happen: how many match, how many an earlier enabled rule already claims (`shadowed`; it wins), how many named their release or were matched to a milestone (`explicit`; a rule never moves them), whether the target release exists (`target_exists`), and a sample with each run's release today and where it would land (`rule`, `earlier_rule`, `explicit`). Preview before you save a broad pattern. The page also counts the runs among those 200 that have no release at all (**Unattributed**: the linker failed on them; in a healthy system there are none) and links to `/runs?release=unattributed` (the Runs list on the release picker's Unattributed bucket; the API's own filter is `GET /api/v1/runs?release_id=unattributed`); such a run's page reads **Unattributed** and offers to set its release.

## Digests carry the release verdict

Daily and weekly digests, and the per-release digest emails, include the release-level gate verdict of their release: the release the subscription names (a per-release subscription, or a saved view that names exactly one release), else the project's active release. It is the release's **recorded** verdict (the last **Evaluate gate**), never recomputed and never the run-level decision, which keeps its own line: `Release 2026.11 — No-Go (2 blockers) · decided 2026-10-21 14:05 UTC · the project's active release`. A release nobody has evaluated reads `not evaluated yet`, never Go. The preview on **Settings → Digests** shows the same line.

## Overrides

A QA lead or administrator can override a verdict from the release gate (**Override decision** → choose GO, CONDITIONAL GO or NO GO → a written reason → **Apply Override**; API: `POST /api/v1/release-readiness/{run_id}/override`). The override:

- requires a **written reason**,
- is recorded **alongside** the computed verdict (the machine's opinion is never erased), listed in the gate's **History** tab, and audit-logged,
- can be restricted by the policy's **Override Restrictions** rule.

Use overrides for judgment calls, not as a recurring workaround — if you override the same rule weekly, change the policy instead.

## Release phase enforcement

Releases have phases (Planning, Development, Code Freeze, QA Testing, UAT, Staging, Production). By default a QA lead can complete or skip any phase, and **Mark as Released** only requires every phase to be completed or skipped.

The **`release_phase_gate_enforcement`** feature flag (**on** for a new install; an upgraded install keeps its stored value, off unless an admin turned it on: [feature flags](../docs/operations/feature-flags.md#release-flags-on-new-installs-only)) makes the server refuse to:

- complete a phase whose gate verdict is not GO, unless the request carries `gate_override_reason`;
- skip a phase, unless the request carries `skip_reason`.

On the release page this is one step: setting a phase to **Completed** over a failing gate, or to **Skipped**, opens a reason form under that phase that quotes the gate's own sentence (for example `This phase's gate says NO_GO: test_pay (checkout-e2e) is FAILED. ...`) and asks for a reason of at least 3 characters; **Complete with override** / **Skip phase** resends the change with the reason. Cancel changes nothing. Each reason is recorded in the access audit log and in the project's activity feed (`/activity?category=release`), for example `Phase gate Regression overridden on release 2026.11: Customer waiver CAB-142`. The API takes the same two fields (see `/api-docs`, the Releases section).

## Compliance packs

Generating a compliance pack for a release needs the `release_compliance_pack` flag (**on** for a new install, same rule as above; generating answers `503` while it is off). The releases page's side rail lists the packs that exist, with their size and the first 8 characters of the manifest hash, and downloads them; listing and downloading never need the flag. New packs are signed with the deployment's Ed25519 key, and **Verify** on a pack (or `testlookup compliance-pack verify` offline) checks it. What a pack contains, what its signature proves and its limits: [Compliance & governance](compliance.md#how-a-pack-proves-it-hasnt-been-altered).

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
