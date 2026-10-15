# Releases and gates

Grouping runs into a release, reading the readiness recommendation, and the policies behind it.

> **Important.** TestLookup does not decide whether you ship. It produces a **recommendation** with its reasoning and evidence attached. The decision stays with you.

## Releases

A **release** is a named grouping of test runs in one project (for example `v2.5.0`), with phases and a release-gate status. **Releases** in the sidebar (`/releases`) is for QA leads and administrators; everyone can use the release picker in the top bar to filter the report pages to one release.

### Creating a release

1. Select one project, open **Releases**, and click **New release**.
2. Fill in **Release Name** (required), **Version**, **Status** (**Planning**, **In Progress**, **Released** or **Cancelled**), **Planned Release Date** and **Description**.
3. **Create Release**.

A release is also created automatically the first time a run arrives with a release name that does not exist yet. Creating or editing a release needs `QA_LEAD` or higher; deleting one needs an administrator.

The list shows each release's **recorded** gate verdict: **Go**, **Conditional Go**, **No-Go**, **Not evaluated** (it was evaluated, but too few tests carried results to decide) or **Not evaluated yet** (nobody has evaluated it), with when it was evaluated. An evaluated release also shows its **Pass rate**, **Evidence** (tests with results, of those in scope) and **Runs**, and a No-Go one its first blocking tests (`test_pay (checkout-e2e) is FAILED`); **Open gate →** goes to the full list on its page. The list filters by status and searches names, versions and owners. Click a release for a side panel, or **Open full page** for its detail.

### Evaluating the release gate

A verdict is a **snapshot**: TestLookup does not re-evaluate a release when new runs arrive. A QA lead records a fresh one with **Evaluate gate** on a release (**Re-evaluate** once it has one), or **Evaluate all in progress** above the list, which evaluates every in-progress release in turn and says how many came out Go, No-Go or not evaluated. Everyone else sees the verdict but not the buttons. Check **Evaluated …** on a release before relying on it: a Go from last week says nothing about the runs since.

On a release's page, **Release gate** shows the verdict, its scorecard (pass rate, evidence, tests in scope, runs and the count per status), every blocking test, and how the release compares with its **baseline** (the previous release, or the one it names): the pass-rate change in percentage points and the change in tests in scope and evidence, or the reason no comparison is meaningful (no baseline, or too little evidence on either side).

### Owner

A release can have an **owner**: a person who can work on its project. A release created on the page is owned by whoever created it; one created by a run upload, an API key or a sync has none and reads **No owner**. Change it in **Edit** → **Owner**; the change is recorded in the activity feed with the old and new names. Service accounts and the project's built-in QA lead cannot own a release.

### The active release and syncing

A project's **active** release (marked **ACTIVE**) is where a run lands when nothing else names its release. A QA lead makes another release active with **Release actions** (**⋯** on the release) → **Set as active release**; a released or cancelled release cannot be made active.

**Sync releases** (QA lead, one project selected) pulls releases in from **GitHub milestones** or **Jira fix versions** (optionally with a **Jira project key**; empty uses the configured default). It brings names and links only; phases, gate policy and run attribution stay in TestLookup. A synced release links to its milestone or fix version. While the deployment runs offline (`AI_OFFLINE_MODE`), or the integration has no credential, the sync says why and changes nothing; **Open Settings › Integrations** goes to the integration settings.

### Getting runs into a release

A run belongs to a release in one of these ways, in this order:

1. **It arrived with a release name**: **Release** in the upload dialog, `release_name` on the API, `--release` on the CLI, or the SDK's release setting (`TESTLOOKUP_RELEASE`).
2. **A git tag or pull-request milestone** on the run names it.
3. **An attribution rule** for the project matches the run's branch, build number, environment or tag. Rules are managed through the API (`/api/v1/projects/<id>/attribution-rules`); there is no page for them yet.
4. **The release's cut-off window** contains the run, or the release was **active** when the run executed.

If none of these applies, the run is **unattributed** (the release picker's **— Unattributed —** shows these).

You can also link runs by hand: on a release's page, **Link Run**, pick a recent run and optionally a phase. On a run's page, **Set release** (or **Change release**) attaches the run to a release by name and makes that its primary release. Both need `QA_LEAD` or higher; unlinking a run needs an administrator.

### Phases

A release's page lists **Release Phases**. **Add Phase** adds one, with a name and a type (**Planning**, **Development**, **Code Freeze**, **QA Testing**, **UAT**, **Staging**, **Production**). Move a phase on with its status (**Pending**, **In Progress**, **Completed**, **Skipped**). Each phase shows its gate (**Gate: Go**, **No-Go** or **Not evaluated**), worked out live from the phase's linked runs and not recorded; a QA lead records one with **Record gate verdict**.

When every phase is completed or skipped, **Mark as Released** sets the release to released; the server refuses it while any phase is still open.

> **Phase enforcement.** With the `release_phase_gate_enforcement` feature flag on (the default on a new installation), completing a phase whose gate is not Go, or skipping one, needs a reason. The page asks for it: **Complete "…" anyway?** quotes what the gate found, and **Skip "…"?** explains why a skip needs one. Write the reason (at least 3 characters) and choose **Complete with override** or **Skip phase**; **Cancel** leaves the phase as it was. The override or skip is recorded in the audit log and in the activity feed with its reason. On the API the fields are `gate_override_reason` and `skip_reason`. The flag is described in the repository's feature-flag reference (`docs/operations/feature-flags.md`).

### Also on a release's page

- **Runs**, **Tests**, **Passed**, **Failed** and **Pass Rate** across its linked runs, and the **Linked Test Runs** list.
- **Release gate**: the recorded verdict and its evidence (see [Evaluating the release gate](#evaluating-the-release-gate)).
- **Production outcome**: after shipping, **Mark incident** or **Record rollback** with a reason, so the release's history records how it went.
- **Compliance Pack**: **Generate pack** (`QA_LEAD` or higher) builds a downloadable ZIP of the evidence behind the release decision. Compliance packs are on by default on a new installation (`release_compliance_pack` flag); with the flag off, generating says the feature is disabled. The packs that exist are also listed beside the release list, with their size, checksum and a **Download**.
- **Release workflow**: the release's pipeline status.

## Gate policies

**Releases → Gate policies** (`/policies`) holds the rules the gate applies. Each project uses one active policy, or the system default.

A policy has **thresholds** (**GO Threshold**, **NO_GO Threshold**, **Pass Rate Minimum**, **Hard Floor Factor**), **pass-rate bands** (orange, yellow and green lower bounds), **rules** (**Flaky Recurrence Limit**, **Open Defect Limit**, **Dimension Ceiling**, **Override Restrictions**, each with an action of **BLOCK**, **WARN** or **INFO**), and optional hard caps and weights. **Policy Simulator** shows what a policy would decide for a run before you publish it.

Policies are versioned: **New Policy** or editing creates a draft, **Save Draft** keeps it, and **Publish** makes it active and deactivates the previous version. Published versions are read-only, and past verdicts keep the policy version they were decided with. Creating, publishing and simulating policies needs an **administrator**; QA leads can read them. Writing good policies is covered in the operator guide in the repository (`user-guide/release-gates.md`).

## The release gate

**Release gate** in the sidebar (`/release-gate`) opens the newest finished run's gate; `/release-gate/<run id>` opens a specific run's (also **⋯ → Release gate** on a run's page). In **All Projects** it lists recent runs with **View Gate →**.

The verdict card shows the **Recommendation** (**GO**, **CONDITIONAL GO**, **NO GO** or **PENDING**), the pass rate, the risk score, the top blockers or the conditions for GO, and which policy it was evaluated with. Below it, three tabs:

- **Why** — blocking issues, conditions for GO, the policy rules evaluated, the reasoning, open defects by component, and the **Risk Dimension Breakdown**.
- **Context** — charts and the linked failure clusters.
- **History** — the override history.

**Run analysis** re-runs the analysis; **⋯ → Export PDF** downloads the decision; **⋯ → Share report** creates a read-only link (see [Reports and outputs](/docs/reports#sharing-and-retention)).

## The recommendation

The release-risk stage produces one of three values, plus the reasoning behind it:

| Value | Means |
|---|---|
| **GO** | No blocking signals found in what was assessed |
| **CONDITIONAL_GO** | Proceed only if the listed conditions are handled |
| **NO_GO** | Blocking issues present |

Alongside it you get a **risk score**, **dimension scores**, **blocking issues**, **conditions for go**, and the **version of the scoring model** used — so a recommendation from last month can be read in the terms that produced it.

When the administrator has made human review of AI reports mandatory, a run whose report has not been reviewed yet shows **PENDING** until a QA lead accepts the report; see [Your inbox and approvals](/docs/inbox#ai-reports).

## The seven risk dimensions

The composite risk score is built from seven dimensions. Each is scored from the run's own evidence:

| Dimension | Reads as |
|---|---|
| **User impact** | Product-bug share weighted by open defect pressure |
| **Environment sensitivity** | How much of the failure is environmental |
| **Reproducibility** | Whether the failure repeats or wanders |
| **Regression likelihood** | Whether this looks like something that used to pass |
| **Historical recurrence** | Whether this has happened before |
| **Blast radius** | How far across suites the failure reaches |
| **Diagnosis confidence** | How sure the analysis is — low confidence *raises* risk |

That last one is worth pausing on: **uncertainty counts against you.** An analysis that cannot tell you what went wrong makes the release riskier, not safer.

## The decision rules, in order

The engine evaluates these in sequence and takes the first that matches:

| Condition | Result |
|---|---|
| Pass rate below **70% of your configured bar** | **NO-GO** — catastrophic, whatever the composite says |
| Composite risk at or above the NO-GO threshold | **NO-GO** |
| Composite risk at or above the GO threshold | **CONDITIONAL GO** |
| Pass rate below your configured bar | **CONDITIONAL GO** |
| Otherwise | **GO** |

The first rule is a hard floor. A catastrophic pass rate produces NO-GO regardless of how the composite scored — no combination of otherwise-healthy dimensions can talk the gate out of it.

Policy rules then apply on top: a **BLOCK** rule that fires forces NO-GO, and a **WARN** rule turns a GO into a CONDITIONAL GO. Rules only ever make the verdict stricter.

## Pass-rate bands can only make it stricter

Where pass-rate bands are configured they can **tighten** a verdict but never loosen one. A band can turn a GO into a CONDITIONAL GO; it can never unblock a NO-GO.

Tests in active quarantine are left out of the failure signal, so a known-flaky test that is being fixed does not hold the gate at NO-GO; see [Flaky tests](/docs/flaky#quarantine-on-the-flaky-tests-page).

## How it is produced

Scoring is deterministic: dimensions are scored, combined into a risk score, and mapped to a recommendation. Narrative text around it may be model-written, but **the verdict is arithmetic**, not generated.

> **Note.** If the scorer fails, the stage substitutes `CONDITIONAL_GO` at risk 50 with "manual review required" — and records that it did so. A substituted verdict is stored in the same shape as a computed one, so the pipeline logs the substitution explicitly. If you see a bare CONDITIONAL_GO at exactly risk 50, check whether it was computed.

## Reading it honestly

- The gate assesses **the signals it has**. A suite that does not test something cannot report risk in it.
- `GO` means "nothing blocking was found", not "this release is safe".
- Blocking issues are the part to read. The single word at the top is a summary of them.

## Overrides

A recommendation can be overridden. On the release gate, **Override decision** opens **QA Lead Override**: choose **GO**, **CONDITIONAL GO** or **NO GO**, write the reason (required), and **Apply Override**. The override is recorded alongside the computed verdict, with who made it and why, and listed under **History** — the point is an auditable decision, not a silent one. A policy's **Override Restrictions** rule can forbid some overrides.

Required role: `QA_LEAD` or higher to override. If you override the same rule week after week, change the policy instead.

## Related

- [How decisions are made](/docs/decisions)
- [Reports and outputs](/docs/reports)
- [Runs and run detail](/docs/runs)
