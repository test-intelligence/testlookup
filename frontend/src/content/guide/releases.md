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

The list shows each release's **recorded** gate verdict: **Go**, **Conditional Go**, **No-Go**, **Not evaluated** (it was evaluated, but too few tests carried results to decide) or **Not evaluated yet** (nobody has evaluated it), with when it was evaluated. An evaluated release also shows its **Pass rate**, **Evidence** (tests with results, of those in scope) and **Runs**, and a No-Go one its first blocking tests (`test_pay (checkout-e2e) is FAILED`); **Open gate →** goes to the full list on its page. The list filters by status and searches names, versions and owners. Click a release to open its detail beside the list; the right-hand panels make way while it is open, and clicking another release switches to it. The address carries the open release (`?open=`), so a refresh or a shared link opens it again, and **Back** (or **Close release**) closes it. **Edit** and **Link Run** open their forms inside the detail; **Open full page** goes to the release's own page.

### Evaluating the release gate

A verdict is a **snapshot**: TestLookup does not re-evaluate a release when new runs arrive. A QA lead records a fresh one with **Evaluate gate** on a release (**Re-evaluate** once it has one), or **Evaluate all in progress** above the list, which evaluates every in-progress release in turn and says how many came out Go, No-Go or not evaluated. Everyone else sees the verdict but not the buttons. Check **Evaluated …** on a release before relying on it: a Go from last week says nothing about the runs since.

On a release's page, **Release gate** shows the verdict, its scorecard (pass rate, evidence, tests in scope, runs and the count per status), every blocking test, and how the release compares with its **baseline** (the previous release, or the one it names): the pass-rate change in percentage points and the change in tests in scope and evidence, or the reason no comparison is meaningful (no baseline, or too little evidence on either side).

Daily and weekly digests, and per-release digest emails, carry this recorded verdict for their release: the release the digest names (a per-release subscription, or a saved view that names exactly one release), else the project's active release. The line reads `Release 2026.11 — No-Go (2 blockers) · decided 2026-10-21 14:05 UTC · the project's active release`, or `Release 2026.11 — not evaluated yet` for a release nobody has evaluated (never a Go it did not earn). It sits apart from the digest's latest run decision, which is one run's recommendation, not the release gate.

### Owner

A release can have an **owner**: a person who can work on its project. A release created on the page is owned by whoever created it; one created by a run upload, an API key or a sync has none and reads **No owner**. Change it in **Edit** → **Owner**; the change is recorded in the activity feed with the old and new names. Service accounts and the project's built-in QA lead cannot own a release.

### The active release and syncing

A project's **active** release (marked **ACTIVE**) is where a run lands when nothing else names its release. A QA lead makes another release active with **Release actions** (**⋯** on the release) → **Set as active release**, then **Make active** in the question that appears under the release; a released or cancelled release cannot be made active. **Delete** (a release) and **Remove phase** ask the same way, in place, and change nothing until you confirm.

**Sync releases** (QA lead, one project selected) pulls releases in from **GitHub milestones** or **Jira fix versions** (optionally with a **Jira project key**; empty uses the configured default). It brings names and links only; phases, gate policy and run attribution stay in TestLookup. A synced release links to its milestone or fix version. While the deployment runs offline (`AI_OFFLINE_MODE`), or the integration has no credential, the sync says why and changes nothing; **Open Settings › Integrations** goes to the integration settings.

### Getting runs into a release

When a run arrives, TestLookup links it to a release by the first of these that applies, in this order (the **attribution ladder**):

1. **The release the run names**: **Release** in the upload form, `release_name` on the API, `--release` on the CLI, or the SDK's release setting (`TESTLOOKUP_RELEASE`).
2. **A GitHub milestone on its pull request**, when the project has a GitHub integration and the run carries its pull request's number.
3. **The project's attribution rules**, top to bottom: the first enabled rule that matches the run's branch, build number, environment or tag (see [Attribution rules](#attribution-rules)).
4. **A release whose cut-off window holds the run.**
5. **The release that was active when the run executed.**

So a run that names no release and that no rule matches still lands, by step 4 or 5. A rule decides where a new run lands; it never moves a run that is already linked.

A run with no release at all is **Unattributed**: the ladder did not link it, which in a healthy system does not happen (the linker failed on that run). Its page shows **Unattributed** where the release would be, and the release picker's **— Unattributed —** lists these runs.

You can also link runs by hand: on a release's page, **Link Run**, pick a recent run and optionally a phase. On a run's page, **Unattributed** (or the pencil, **Change release**, next to its release) attaches the run to a release by name and makes that its primary release. Both need `QA_LEAD` or higher; unlinking a run needs an administrator.

### Attribution rules

**Releases → ⋯ → Attribution rules** (`/releases/attribution-rules`) maps a run's branch, build number, environment or tag to a release when the run does not name one. It needs one project selected; in **All Projects** it asks you to pick one. The page first explains the ladder above, then lists the project's rules in the order they are tried: **Order**, **Rule**, **Matches** (for example `Branch release/2.5*`), **Release** (with `(created on first match)` when no release of the project has that name yet), **Priority** and **Enabled**. With no rules it says `No rules yet. Runs that name no release land in the cutoff window or the active release.`

- **Branch**, **Build number** and **Tag** match with the wildcards `*` and `?` (`release/2.5*`, `nightly-*`, `rc-*`); a tag rule matches when any of the run's tags does. **Environment** matches exactly, ignoring case (`staging`).
- Rules are tried by **Priority**, lowest first; rules with the same priority run in the order they were created. The first enabled rule that matches wins.
- A rule names its release by **name**. When no release of the project has that name yet, the first run the rule matches creates it.

A QA lead (or administrator) adds a rule with **New rule** (**Name**, **Field**, **Pattern**, **Release**, **Priority** from 0 to 10,000, **Enabled**, then **Save**), changes one with **Edit**, turns one on or off with its **Enabled** toggle, and removes one with **Delete**, which asks in place (`Runs it already matched stay where they are.`). Everyone else can read the page but not change it.

**Preview** tries the rule in the form against the project's last 200 runs and saves nothing. It says how many match (`Matches 12 of the last 200 runs (183 carry a branch).`), how many of those an earlier rule already claims (`3 of them already match an earlier rule, which wins.`), how many named their release outright (`2 named their release outright; a rule does not move them.`), whether the release exists yet (`No release named "2.6" yet: the first matching run creates it.`), and `This rule matches every recent run.` for a catch-all. A sample table lists matched runs with their build, branch, environment, tags, when they ran, the release they are in today and where the rule would put them: **here**, **earlier rule** or **stays (named)**. After a preview, the footer counts the runs among those 200 that have no release (`4 of the last 200 runs have no release (Unattributed).`) and links to them with **Show them in Runs** (`/runs?release=unattributed`: the Runs list on the release picker's Unattributed bucket).

On the API the rules are `/api/v1/projects/<id>/attribution-rules`; `…/attribution-rules/fields` lists the fields and which of them take wildcards.

### Phases

A release's page lists **Release Phases**. **Add Phase** adds one, with a name and a type (**Planning**, **Development**, **Code Freeze**, **QA Testing**, **UAT**, **Staging**, **Production**). Move a phase on with its status (**Pending**, **In Progress**, **Completed**, **Skipped**). Each phase shows its gate (**Gate: Go**, **No-Go** or **Not evaluated**), worked out live from the phase's linked runs and not recorded; a QA lead records one with **Record gate verdict**.

While a planning or in-progress release has no phases yet, a QA lead can build its first schedule in one step, from its card in the list (**No phases scoped yet**) or beside **Add Phase** in its detail:

- **Copy phases from…** opens **Copy phases**. Pick **From release**: another release of the same project that has phases (`2026.10 · 4 phases`). Check the preview of its phases (name, type, days), and set **First phase starts** (shown when the source's phases have dates; filled in with the release's cut-off start, else its planned date minus the source's span, else today). **Copy phases** copies every phase in order (name, type, description, notes and exit criteria), all **Pending**, with every planned date moved by the same number of days so that the first phase starts on the date you chose.
- **Draft from a PRD…** opens **Draft phases from a PRD**. Paste the product requirements document into **PRD text**, or **Choose file** (`.md`, `.txt` or `.docx`, up to 200 KB), and **Draft phases**. The heading parser always drafts one phase per heading (`##` headings work best; sections such as Overview, Goals, Scope, Requirements or Appendix are skipped), with a type from the heading's words (smoke, regression or UAT, for example; a heading that names none is drafted as QA testing and the draft says so), a duration when the heading or its text names one (`5 days`; `2 weeks` counts as 10 working days) and any `Exit criteria:` lines. When the deployment's AI analysis mode is **LLM** or **Auto**, the configured AI model drafts instead and the badge names it (`Drafted by qwen2.5:7b`). In **Rules** mode, or when the model cannot be used (offline mode refuses its provider, it is not available, it does not answer within 45 seconds, or its answer cannot be read or holds no phases), you get the parser's draft and the badge says why: `Drafted by rules — the deployment is offline (AI_OFFLINE_MODE)`. Edit the draft (**Name**, **Type**, **Days**, **Description**, **Remove**, **Add a row**), optionally set **First phase starts** (the phases then get back-to-back windows of their days, a row without days counting as 5), and **Create N phases**. Nothing is saved before **Create**, and the document itself is never stored.

Both build a release's **first** schedule: a release that already has phases grows one phase at a time with **Add Phase**. Each is recorded in the activity feed (`Release 2026.12: 4 phases copied from 2026.10`, `Release 2026.13: 3 phases created (rules)`).

When every phase is completed or skipped, **Mark as Released** sets the release to released; the server refuses it while any phase is still open.

> **Phase enforcement.** With the `release_phase_gate_enforcement` feature flag on (the default on a new installation), completing a phase whose gate is not Go, or skipping one, needs a reason. The page asks for it: **Complete "…" anyway?** quotes what the gate found, and **Skip "…"?** explains why a skip needs one. Write the reason (at least 3 characters) and choose **Complete with override** or **Skip phase**; **Cancel** leaves the phase as it was. The override or skip is recorded in the audit log and in the activity feed with its reason. On the API the fields are `gate_override_reason` and `skip_reason`. The flag is described in the repository's feature-flag reference (`docs/operations/feature-flags.md`).

### Also on a release's page

- **Runs**, **Tests**, **Passed**, **Failed** and **Pass Rate** across its linked runs, and the **Linked Test Runs** list.
- **Release gate**: the recorded verdict and its evidence (see [Evaluating the release gate](#evaluating-the-release-gate)).
- **Production outcome**: after shipping, **Mark incident** or **Record rollback** with a reason, so the release's history records how it went.
- **Compliance Pack**: **Generate pack** (`QA_LEAD` or higher) builds a downloadable ZIP of the evidence behind the release decision. Compliance packs are on by default on a new installation (`release_compliance_pack` flag); with the flag off, generating says the feature is disabled. The packs that exist are also listed beside the release list, with their size, checksum and a **Download**. Each new pack is **signed** by the deployment's key (`Signed · k-1a2b3c4d`); a pack generated before signing existed reads `Unsigned (tamper-evident only)`. **Verify** re-reads a stored pack and checks every file against its manifest, the manifest against the digest recorded when the pack was generated, and the signature: `Verified · 12 files · signed by key k-1a2b3c4d5e6f7a8b`, or the file that does not match (`Tampered: decision.json does not match the manifest`). **Public key** downloads the key that checks the signature. What signing does and does not prove, and verifying a pack offline with the CLI, are in the repository's compliance guide (`user-guide/compliance.md`).
- **Release workflow**: the release's pipeline status.

### Schedule and calendar

**List | Calendar** at the top of **Releases** switches the list column between the release cards and a month calendar (in **All Projects** too). The calendar shows each release on its day (its release date once released, otherwise its planned date; cancelled releases are left out) and each phase with planned dates as a bar across the days it covers, up to three bars a week and `+N more` on the day for the rest. **‹ Prev**, **Next ›** and **Today** change the month; click a release or a phase to open the release beside the calendar. The address keeps the view and the month (`?view=calendar&month=2026-11`), so a refresh or a shared link opens the same calendar. Dates are UTC days, as the server stores them; the calendar says so under the grid.

**⋯ → Export schedule (.ics)** downloads the releases and their planned phases as an iCalendar file for Outlook, Google Calendar or Apple Calendar: all-day events in UTC, one per dated release and one per phase with a planned window, each with a stable id, so importing a newer file updates the events instead of duplicating them. **⋯ → Export schedule (.csv)** downloads one row per phase (a release without phases gets one row) for a spreadsheet, with dates in ISO-8601 UTC; a cell a spreadsheet would run as a formula is written as text. Both cover the selected project, or in **All Projects** every project you can see (more than 500 releases: pick one project). An export is a file you download, through your signed-in session: there is no calendar subscription URL, so export again to refresh.

### Release health trend

Open **Release health · this week** above the list to see the four release counts (**In progress**, **Ready to ship**, **Blocked**, **Released · 30d**), each with a 12-week sparkline: one point at the end of each week (Monday 00:00 UTC) and a last point for now, which is the card's own number. Hover a point for its week and its count. The trend is read only when you open the section.

At each point:

- **In progress**: releases that existed then and are in progress today, or that are released today but were not yet released then.
- **Ready to ship**: of those, the ones whose latest release-level gate verdict at that time was **Go**.
- **Blocked**: of those, the ones whose latest verdict at that time was **No-Go** or carried a blocking reason; at the last point (now), also any with a failed phase today.
- **Released · 30d**: releases released in the 30 days before that point (and still released today).

> **Note.** TestLookup does not keep a history of status changes, so **In progress**, **Ready to ship** and **Blocked** use **today's status** for every past week: a release that is still planning today counts nowhere in the past, and one cancelled today is never counted. Each of their points says `approximation: today's status`. A week before the first release existed has no point at all, which is not the same as zero; from then on, 0 is a measured zero.

Two captions sum up the 12 weeks, each a count over its own denominator: **Ready to ship** says `GO on 3 of 4 evaluated (12 w)`: of the releases with a release-level verdict in the window, how many were **Go** at their latest one (a **Conditional Go** or a **Not evaluated** counts among the 4, never as Go), or `No releases evaluated in 12 weeks`. **Released · 30d** says `2 of 3 released with a pack (12 w)`: of the releases released in the window, how many have a compliance pack that is not retired, or `No releases released in 12 weeks`. If the trend cannot be read, the cards keep their numbers and say `Could not load the 12-week trend.`

## Gate policies

**Releases → Gate policies** (`/policies`) holds the rules the gate applies. Each project uses one active policy, or the system default.

A policy has **thresholds** (**GO Threshold**, **NO_GO Threshold**, **Pass Rate Minimum**, **Hard Floor Factor**), **pass-rate bands** (orange, yellow and green lower bounds), **rules** (**Flaky Recurrence Limit**, **Open Defect Limit**, **Dimension Ceiling**, **Override Restrictions**, each with an action of **BLOCK**, **WARN** or **INFO**), and optional hard caps and weights. **Policy Simulator** shows what a policy would decide for a run before you publish it: pick the run from the searchable list of the policy's project's runs (the top-bar project's for the system default), or **Paste run ID**.

Policies are versioned: **New Policy** or editing creates a draft, **Save Draft** keeps it, and **Publish** makes it active and deactivates the previous version. Published versions are read-only, and past verdicts keep the policy version they were decided with. Creating, publishing and simulating policies needs an **administrator**; QA leads can read them. Writing good policies is covered in the operator guide in the repository (`user-guide/release-gates.md`).

**Versions**, a closed section under a policy's header, lists every version of that policy's scope, newest first (`v4 · Active`, `v3 · Published`, `v5 · Draft`), with its name, who created it and when, and when it was activated. **Open** goes to a version; the one you are on reads **This version**. Tick **Compare** on two versions and **Compare v3 → v4** lists each setting that differs (`GO threshold 20 → 25`, `Pass-rate band yellow_min 95 → 97`, `Hard cap max_flaky_count 10 → 5`, `Rule "Block on P0" added`), or `No settings differ.` Versions and Compare are read-only, for everyone who can open the policy. On a published version an administrator also has **New version from this**: it creates a draft with the same scope, name, description and settings, as the next version number, and opens it, so a change starts from what is in force (`Draft v5 created from v4.`).

### Which line is the target

Every pass-rate line in the app comes from the effective policy's **pass-rate bands**: the **GO line** is the yellow lower bound, and below the orange lower bound is the **No-Go zone**. With no published policy for the project or the deployment, Trends and Live draw the default bands (Go at 95%, No-Go below 90%, green from 99%) and say **default bands**.

- **Trends** draws the GO line as the dashed target of the pass-rate chart (`GO ≥ 95%`) and shades the No-Go zone (`No-Go < 90%`); the sentence above the chart names the source, for example `7 pp below the GO line (95%, default bands)` or `(97%, Payments gate v4)`.
- **Live** flags the pass rate `below the GO line (95%)` between the two lines and `in the No-Go zone (< 90%)` under the lower one; hover the flag for the policy (`Bands: Payments gate v4`).
- **Home** names the policy that decides the readiness verdict under **How this verdict is decided** (`Decided by Payments gate v4 (project policy): Go at 95% or more, Conditional from 90%, No-Go below 90%.`), and lists any hard cap that forced No-Go (`Open critical (P0) defects: 2, cap 0`). With no published policy, Home's verdict is not the default bands but the built-in rule, and it says so: `Decided by the built-in rule (no release-gate policy is published for this project or the deployment): Go at 95% or more with no open defects and at most 5 flaky tests, Conditional from 85% with at most 5 open defects, otherwise No-Go.`
- **Release gate** names the band policy beside the stored decision's (`Evaluated with project policy v3 · pass-rate bands: project policy v4`), and the hard caps that moved the verdict, if any.

Earlier versions drew a fixed 90% target on Trends and Live. The line is now the GO line, which is 95% under the default bands; a project that wants its line at 90% publishes a policy whose yellow lower bound is 90.

## The release gate

**Release gate** in the sidebar (`/release-gate`) opens the newest finished run's gate; `/release-gate/<run id>` opens a specific run's (also **⋯ → Release gate** on a run's page). In **All Projects** it lists recent runs with **View Gate →**.

With a release chosen in the top bar the page follows it: choosing another release opens that release's newest finished run (choosing **All releases** does the same for the project), and a line under the title gives the **release's** recorded gate verdict beside the run's, `Release 2026.11 (top bar) · release gate: No-Go · 2 blockers · decided 2 h ago` with **Open release →**, or `release gate: not evaluated yet`. The card below it stays the run's own recommendation; the line is the release gate (see [Evaluating the release gate](#evaluating-the-release-gate)).

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
