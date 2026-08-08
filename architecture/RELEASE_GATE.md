# Release Gate — decision architecture

> Companion to [README.md](./README.md). How a run (or release) becomes
> **GO / CONDITIONAL_GO / NO_GO**: the input signals, the policy layer, the
> release-council synthesis, and the human override path. Verified against the
> implementation 2026-07-02.

Two design principles run through this subsystem:

1. **Deterministic and auditable** — the council assembles an `input_snapshot`
   capturing *every* data source the decision depends on, so a verdict can be
   reproduced and explained after the fact.
2. **Fail-closed composition** — when independent signals disagree, the
   composition picks the *worse* verdict (`_worse_verdict`); a green signal can
   never soften a red one.

## 1. Decision flow

```mermaid
flowchart TB
    subgraph Inputs["Input signals"]
        PR["Pass rate + PolicyPassRateBands<br/>(metrics_service.classify_with_policy)"]
        RISK["criticality_service — weighted risk dimensions<br/>incl. hist_recurrence = share of analysed failures<br/>that are RECURRING (weights: settings.RISK_WEIGHT_*)"]
        FLK["Flaky intelligence<br/>(verdicts + active quarantines excluded from the signal<br/>— see FLAKY_INTELLIGENCE.md)"]
        CLU["Failure clusters / analysis verdicts"]
    end

    subgraph Policy["Policy layer (per project)"]
        RESOLVE["policy_evaluator_service.resolve_effective_policy<br/>(project policy or defaults)"]
        EVAL["evaluate_policy → RuleEvaluation[]<br/>thresholds + weights + rules from ReleaseGatePolicy"]
    end

    subgraph Council["Release council (release_council_service)"]
        SNAP["assemble_input_snapshot<br/>(deterministic capture of every input)"]
        DIM["_build_dimension_scores<br/>DIMENSION_METADATA: score x weight = contribution"]
        BAND["_apply_band_floor<br/>band verdict can only WORSEN the composite"]
        SYNTH["_synthesize_release_council<br/>composite verdict + evidence"]
    end

    subgraph Output["Verdict + surfaces"]
        V["GO | CONDITIONAL_GO | NO_GO"]
        OVR["apply_override<br/>(human, reasoned, audited)"]
        UI["/release-gate · /releases · MCP"]
    end

    PR --> SNAP
    RISK --> SNAP
    FLK --> SNAP
    CLU --> SNAP
    RESOLVE --> EVAL
    EVAL --> SYNTH
    SNAP --> DIM
    DIM --> SYNTH
    PR --> BAND
    BAND --> SYNTH
    SYNTH --> V
    V --> OVR
    V --> UI
    OVR --> UI
```

## 2. The policy layer

`services/policy_evaluator_service.py` evaluates a project's `ReleaseGatePolicy` (edited on `/policies`):

- `resolve_effective_policy` picks the project's active policy, falling back to defaults — pages and the evaluator always work against an *effective* policy, never a nullable one.
- `_extract_thresholds` / `_extract_weights` / `_extract_rules` read the policy's three configurable parts.
- `evaluate_policy` runs every rule into a `RuleEvaluation` and folds them into a `PolicyEvaluationResult` whose `recommendation` is `GO | CONDITIONAL_GO | NO_GO`. The folding is monotonic-downward: a hard violation forces `NO_GO`; a soft violation downgrades `GO → CONDITIONAL_GO`; nothing upgrades.

## 3. Risk dimensions and `hist_recurrence`

`services/criticality_service.py` scores weighted risk dimensions (weights from `settings.RISK_WEIGHT_*`). The one with history: **`hist_recurrence`** measures *real recurrence* — the share of analysed failures in the window that are recurring (`min(100, recurring/total_analysed × 80)`). It deliberately does **not** proxy recurrence from AI confidence scores (an earlier bug class): a model being confident about a failure says nothing about whether that failure keeps coming back.

Each dimension becomes a `DimensionScore {name, label, score, weight, contribution}` via `DIMENSION_METADATA`, so the UI can show exactly which dimension pushed the composite where.

## 4. Band floor — vocabulary and fail-closed rule

Pass-rate **bands** (`PolicyPassRateBands`, configured per project) classify the run's pass rate. Two subtleties the code guards, worth knowing when reading it:

- **Vocabulary mapping**: the band classifier emits `CONDITIONAL`, the gate vocabulary is `CONDITIONAL_GO` — `_normalize_verdict` maps between them. (Producer/consumer enum mismatches are a recurring bug class in this codebase; this is one of the guarded seams.)
- **Floor, not average**: `_apply_band_floor` lets the band only *worsen* the composite. A red band drags a green composite down; a green band never lifts a red composite (that would be fail-open).

## 4a. Verdict rules — order matters

`criticality_service.score_to_recommendation()` maps composite + pass rate onto
the three verdicts. The rules are **ordered so nothing below can soften a
NO_GO**:

| # | rule | verdict |
|---|------|---------|
| 1 | pass rate below `HARD_FLOOR_FACTOR × threshold` (**0.7 × threshold**) | `NO_GO` — catastrophic, whatever the composite says |
| 2 | composite ≥ `no_go_threshold` | `NO_GO` |
| 3 | composite ≥ `go_threshold` | `CONDITIONAL_GO` |
| 4 | **pass rate below the configured `threshold`** | `CONDITIONAL_GO` |
| 5 | otherwise | `GO` |

**Rule 4 closes a measured gap.** The configured release bar (e.g. 90%)
previously gated *nothing* — only 70% of it acted as a NO_GO floor, so the gate
was effectively binary at ~63%. Measured across nine pass-rate levels on a
throwaway project:

```
pass_rate  100  95  89  83  70  64 | 62     50     0
before     GO   GO  GO  GO  GO  GO | NO_GO  NO_GO  NO_GO
after      GO   GO  CG  CG  CG  CG | NO_GO  NO_GO  NO_GO
```

A build with a third of its suite failing was reported ship-ready. Rule 4 also
makes `CONDITIONAL_GO` **reachable at all** on the cheap "synthesized" path:
there the analysis-driven dimensions all score 0, so the composite only ran
5–13 before jumping to 60 via the hard-floor bump — skipping the entire
CONDITIONAL_GO band.

> This changes ship/no-ship advice, so it was made as an explicit product
> decision rather than folded in as a bug fix.

## 4b. Failure-kind weighting (opt-in, US-9.3 + AI-4)

`policy_evaluator_service` can excuse failures the team has decided shouldn't
block a release — typically infrastructure. It is **off unless a policy
configures it**, and every rule leans conservative:

| rule | behaviour |
|---|---|
| bucketing | failures grouped by the derived kind triad; **`unknown` folds into `product`** — an unclassified failure never softens the gate |
| budgets | a kind whose count ≤ `max_failures` is excluded from the NO_GO trigger; **exceeding the budget restores full counting** for that kind. Kinds with no budget always count |
| confidence floor | when `min_confidence_to_excuse` is set, only failures whose per-failure kind confidence clears it are eligible. **Below-floor or unknown-confidence failures count as `product`** |
| downgrade ceiling | if zero blocking failures remain *and* at least one was excused, a base `NO_GO` drops **at most to `CONDITIONAL_GO` — never to `GO`** (enforced by both the schema `Literal` and the evaluator) |
| base verdicts | `GO` and `CONDITIONAL_GO` are never touched — weighting can only *soften a NO_GO*, never harden or upgrade |
| missing data | absent kind counts (older snapshots, simulator against pre-feature decisions) → no downgrade, recorded in the trail |

**Excluded failures are always reported, never hidden.** The rule evaluation
carries the full kind breakdown plus a counterfactual, so a reader can see what
the verdict *would* have been without the weighting.

The failure kinds themselves come from the derived triad — see
[AI_QUALITY.md §3](./AI_QUALITY.md#3-evidence-grading-agentslog_intelligence_agentpy)
for how the evidence checklist produces the per-failure confidence this floor
consumes.

## 5. The council synthesis and overrides

`services/release_council_service.py` is where it all converges:

- `assemble_input_snapshot(run_id)` captures every input the decision reads — pass rates, dimension scores, policy result, flaky/quarantine state — as one deterministic snapshot stored with the decision.
- `_synthesize_release_council` combines the policy recommendation, dimension scores, and band floor into the composite verdict with its evidence; `_worse_verdict` is the combining operator.
- `apply_override` is the human escape hatch: a permitted user can override the verdict **with a reason**, and the override is recorded alongside the original computed verdict — the gate never silently loses the machine's opinion.

Quarantined tests (see [FLAKY_INTELLIGENCE.md](./FLAKY_INTELLIGENCE.md)) are excluded from the failure signal while quarantine is active, which is the mechanism that stops known-flaky tests from permanently pinning a project at `NO_GO`.

## 6. Adjacent: the agent-stack gate

`services/eval_gate_service.py` is a *different* gate with the same shape: it evaluates whether the **AI agent stack itself** (models, prompt versions, datasets) is fit to release, via `build_agent_stack_gate_manifest` + checksummed manifests and per-gate results folded into an overall status. It shares the fail-closed philosophy but gates the AI layer, not your test run.

## 7. Where it surfaces

- **`/release-gate`** (and `/release-gate/:runId`) — the verdict, the dimension breakdown, the evidence, and the override control.
- **`/releases`** — gate status per tracked release.
- **`/policies`** — the editor for thresholds, weights, rules, and pass-rate bands.
- **MCP / REST** — the same verdict payloads, for CI pipelines that block on the gate.
