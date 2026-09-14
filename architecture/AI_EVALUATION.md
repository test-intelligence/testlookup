# AI Evaluation & Model-Ops — subsystem architecture

> Companion to [AI_QUALITY.md](./AI_QUALITY.md). Where AI_QUALITY keeps the AI
> layer honest *at runtime* (cache, corrections, evidence), this subsystem keeps
> it honest *across changes*: golden datasets, evaluation runs, drift detection,
> the model registry, and the **pre-release gate that blocks a prompt/model/
> routing change from shipping if it regresses quality**. Verified against the
> implementation 2026-07-02.

The governing idea: **the AI is a shipped artifact, so it gets its own release
gate.** A code change goes through CI; an AI change (new prompt, new model, new
routing) goes through *this*.

## 1. The evaluation loop

```mermaid
flowchart TB
    subgraph Datasets["Labeled datasets"]
        GOLD["golden_datasets / golden_agent_outputs<br/>curated items: classification, root-cause,<br/>duplicate-detection, release-decision"]
        FB["ai_eval_service.build_dataset_from_feedback<br/>datasets from accumulated human corrections"]
    end

    subgraph Eval["Evaluation"]
        HARNESS["agent_eval_harness<br/>run the agent(s) over a dataset"]
        RUN["ai_eval_service: AIEvalRun<br/>metric computation, persisted per run"]
        DRIFT["detect_quality_drift<br/>current vs previous window → direction"]
    end

    subgraph Gate["Pre-release gate"]
        GATEMAN["eval_gate_service.build_agent_stack_gate_manifest<br/>checksummed manifest of models/prompts/datasets"]
        GATEEVAL["evaluate_pre_release_gate<br/>current metrics vs baseline thresholds → shared verdict"]
    end

    subgraph Registry["Model registry"]
        REG["model_registry: get_active_model / promote /<br/>retire / get_all_status — per track"]
    end

    GOLD --> HARNESS
    FB --> HARNESS
    HARNESS --> RUN --> DRIFT
    RUN --> GATEEVAL
    GATEMAN --> GATEEVAL
    GATEEVAL -->|PASS| REG
    GATEEVAL -->|FAIL| BLOCK["do not ship the change"]
```

## 2. Golden datasets & feedback datasets

Two sources of labeled truth:

- **Golden datasets** (`golden_datasets`, `golden_agent_outputs`) — curated,
  version-controlled items with known-correct answers, one set per capability:
  `get_golden_classification_items`, `get_golden_root_cause_items`,
  `get_golden_duplicate_detection_items`, `get_golden_release_decision_items`.
  These are the fixed yardstick.
- **Feedback datasets** (`ai_eval_service.build_dataset_from_feedback`) — built
  from accumulated human corrections (the `AIFeedback` rows the
  [correction loop](./AI_QUALITY.md#2-the-correction-learning-loop-servicesanalysis_correctionspy)
  captures). This is what turns "users keep fixing X" into a regression test
  for X.

## 3. Evaluation runs & drift

`ai_eval_service` (OPS-02) runs the agents over a dataset via
`agent_eval_harness`, computes metrics, and persists an **`AIEvalRun`** per
evaluation. The 04:00 UTC scheduled gate also writes one run per evaluated
task type alongside its aggregate gate run. `detect_quality_drift` compares recent runs to prior windows and
reports the direction — so a slow degradation (a model drifting, a data-shape
shift) is caught as a trend, not only at a release boundary. Results surface on
the **AI Evaluation Dashboard** (`/settings/ai-eval`).

## 4. The pre-release gate (`eval_gate_service`)

This is the enforcement point, and it mirrors the shape of the product
[release gate](./RELEASE_GATE.md) — fail-closed, evidence-backed:

- `build_agent_stack_gate_manifest` — a **checksummed manifest**
  (`_manifest_checksum`) of the AI stack under test: models, prompt versions,
  datasets. The checksum makes "what exactly did we evaluate" tamper-evident.
- `evaluate_agent_stack_release_gate` / `evaluate_pre_release_gate` — compares
  current metrics against baseline thresholds and returns `pass`, `fail`, or
  `insufficient_samples` with per-rule results; `_overall_manifest_status`
  folds the per-gate results. Non-blocking warnings remain details on `pass`.
- `persist_agent_stack_gate_run` — the run is stored, so the decision to ship
  (or not) an AI change is auditable.
- The `POST /api/v1/ai-evaluation/pre-release-gate` endpoint is **admin-only**,
  and the docstring states the contract plainly: *prompt, model, or routing
  changes should not ship if the gate returns FAIL.*

## 4b. Prompt registry + enforced manifest ratchet (AI-F2)

Since 2026-07-15 every prompt that reaches an LLM is a **versioned artifact**
in `backend/app/services/prompt_registry.py` (`PromptDef(id, version, text)`,
~24 backend prompts) — call sites import `get_prompt_text("<id>")` instead of
holding inline constants. MCP prompt templates
(`mcp/prompts/templates.py::PROMPT_TEMPLATES`) join the same scheme under
`mcp.*` ids. Two JSON artifacts live next to the registry:

- **`prompt_manifest.json`** — pins `sha256(text)[:12]` + version per prompt.
- **`prompt_manifest_eval.json`** — the eval-gate **attestation** for the
  current manifest digest: change id, verdict, gate results, and the
  `AIEvalGateRun` id when run against a live DB.

The CI blocker is the **`ai.prompt-manifest-sync`** guard in
`scripts/quality_gate.py` (stdlib-only: it re-hashes prompts by `ast`-parsing
the two source files, never importing them). It fails when:

1. any prompt's text hash ≠ its manifest pin (forcing a deliberate version
   bump + manifest rewrite), or a manifest entry is stale/missing;
2. the manifest digest changed **without a fresh attestation**, or the
   attested verdict is not `pass` — i.e. *a prompt edit cannot ship without
   an eval-gate run*.
3. model construction, routing, capability tier/escalation maps, or reviewer
   checks differ from the source hashes recorded in the attestation.

Workflow for changing a prompt:

```bash
# 1. edit the text in prompt_registry.py (or PROMPT_TEMPLATES) + bump version
cd backend
python -m app.services.prompt_registry --write-manifest
python -m app.services.prompt_registry --attest <change-id>   # runs §4's gate, records the run id
# no DB reachable? score against the in-repo golden datasets instead:
python -m app.services.prompt_registry --attest <change-id> --offline
python -m app.services.prompt_registry --check                # what CI will assert
# 2. commit the prompt edit + prompt_manifest.json + prompt_manifest_eval.json together
```

Offline attestation notes: it applies `eval_gate_service._evaluate_rules`
with no baseline (default thresholds) over the golden datasets and records
`"mode": "offline_golden"` so reviewers can tell it apart from a
baseline-compared gate run. Without current recorded candidate outputs the
offline verdict is `insufficient_samples`, never `pass`. The `duplicate_detection` gate is also
`insufficient_samples` there (its offline scorer is a prompt-independent lexical
heuristic — a prompt change cannot regress it). MCP templates are hash-pinned
but their eval story is thinner: they steer an external assistant's tool
calls rather than a scored model output, so the attestation covers them as
hash-pinned + reviewed, not metric-gated.

**What the attestation does not measure, and the gate that does (re-audit
M16).** The offline attestation scores golden items whose correctness is
written in the dataset, so it passes whatever the prompt text says: it proves
a version moved, not that outputs stayed good. For prompts whose output is a
checkable decision or narrative, `prompt_eval_recordings.json` holds golden cases, the raw
model outputs recorded for them, and indexes recordings by prompt hash then
`provider/model@tier`. Narrative cases add explicit grounding, length, and
actionability rubrics. The prompt hash identifies the prompt those outputs were
produced under; `app/services/prompt_eval_recordings.py` scores them
(`fast_classifier_system`: category matches; `regression_watchman_classify`:
per-cluster classification matches; `release_risk_reasoning`: the prompt's own
grounding rules hold). The gate fails when a gated prompt's current hash is not
the hash its outputs were recorded under, when a "measured" entry has no
outputs, or when the outputs score below `min_score`. It runs in the backend
test job (`tests/test_prompt_eval_recordings.py`) and as
`python -m app.services.prompt_eval_recordings --check`.

```bash
# after editing a gated prompt (on a machine with the model):
cd backend
python -m app.services.prompt_eval_recordings --record fast_classifier_system [--model qwen2.5:7b] [--tier slm]
python -m app.services.prompt_eval_recordings --check
# commit the prompt edit + prompt_eval_recordings.json with the manifest + attestation
```

No model is available in CI, so the five current gated prompts are
`insufficient_samples`: `--check` lists them by name and never
counts them as passing. An unmeasured entry is accepted only at the exact hash
frozen in `GRANDFATHERED_UNMEASURED`, so the first edit to any of them fails
the gate until real outputs are recorded and scored. The grandfather keeps an
unchanged branch green; it cannot produce a passing attestation.

Runtime provenance: the registry's version tags (`v<version>:<hash12>`) are
stamped into the pipeline version snapshot
(`workflow._runtime_version_snapshot` → `prompt_registry` digest, plus the
full `prompt_versions` map in `execution_metadata`), the per-test `_audit`
block (analysis_agent), and the `_routing` decision record
(analysis_router) — so any stored verdict traces back to the exact prompt
bytes that produced it.

## 5. Model registry (`model_registry`)

Per **track** (e.g. classification, root-cause), the registry records which
model is live: `get_active_model`, `promote(track, model, metrics)`,
`retire(track)`, `get_all_status`. Promotion carries the metrics that
justified it — you can always answer "why is *this* model live for *this*
capability, and what did it score." The gate (§4) is what a promotion should
pass first.

## 6. Cost as a first-class signal (`agent_cost_service`)

Model-ops includes spend: `get_pipeline_cost_summary` and `check_alerts` track
per-pipeline AI cost and fire alerts, feeding the **Intelligence spend** the
[Intelligence Hub](../user-guide/ai-features.md#run-intelligence-intelligence-runsidintelligence)
shows and complementing the per-project [LLM budget gate](./INGESTION_SCALE.md#6-keeping-the-ai-layer-affordable-under-volume).
A "better" model that costs 10× is a trade-off the numbers make visible.

## 7. Design invariants

- **AI changes are gated like code** — no prompt/model/routing change ships on
  vibes; it passes an evidence-backed, checksummed gate or it doesn't ship.
- **Two yardsticks** — fixed golden sets catch regressions against known-good;
  feedback-derived sets catch regressions against *what real users corrected*.
- **Drift is a trend, not an event** — degradation is watched continuously, not
  only at release.
- **Everything auditable** — eval runs, gate runs, and promotions are all
  persisted with their metrics.

## Related docs

- Runtime AI honesty (the other half): [AI_QUALITY.md](./AI_QUALITY.md)
- The product release gate this mirrors: [RELEASE_GATE.md](./RELEASE_GATE.md)
- User-facing eval dashboard: [user-guide/ai-features.md](../user-guide/ai-features.md#configuring-the-ai-tier-settingsai-settingsai-eval)
