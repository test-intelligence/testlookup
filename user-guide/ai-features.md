# AI features — intelligence, deep investigation, agents, chat

Everything AI-flavored in TestLookup follows two rules worth knowing up front:

1. **It works offline by default.** `AI_OFFLINE_MODE=true` is the default; rules and local ML always run, and the optional LLM tier uses a *local* model (Ollama) when you enable it — analysis degrades gracefully (LLM → ML → rules) rather than failing on a missing model. Nothing here sends your data to an external API in a default install.
2. **Verdicts carry provenance.** Every analysis records which engine produced it (rules / ML / LLM / human-corrected) and why, so "the AI said so" is always inspectable — and correctable (see [Triaging failures](triaging-failures.md#correcting-the-ai-and-why-its-worth-doing)).

## Run Intelligence (`/intelligence`, `/runs/:id/intelligence`)

The **Intelligence Hub** is the cross-run view: recent activity, per-run intelligence reports, cross-run AI insights, and — importantly — **Intelligence spend** (average per run), so the cost of the AI tier is visible where its output is. Pick a run to open its **Run Intelligence** report: what failed, why, clusters, risk, and the recommended actions, assembled from the pipeline's stages.

## Deep Investigation (`/deep-investigate`, `/deep-investigate/:runId`)

Deep Investigation is the heavyweight workflow for a bad run: it clusters the run's failures (you can tune the **clustering threshold**), investigates each cluster, and reports per-cluster confidence. Findings are persisted per `(run, cluster)` by the pipeline itself and carry an **origin** tag (`pipeline` for real analysis, `seed` for demo data), so you can always tell computed findings from seeded examples. Options include **auto-draft defects** and **auto-create Jira tickets** from investigation results (Jira requires the integration to be enabled — outbound integrations are off in offline mode). Past investigations are browsable, so an investigation is a durable artifact, not a one-off chat.

## Agent Pipeline (`/agents`, `/agents/run/:runId`)

The **Agent Pipeline** page is the operational view of the analysis pipeline itself: pick a run (by suite and build) and queue the standard pipeline, then watch **Workflow Progress** stage by stage — ingestion validation, anomaly detection, analysis, clustering, summary — ending in an **Executive Summary** / AI report. Use it when you want to (re)run analysis on demand or see exactly which stage produced a verdict.

## Ask AI (`/chat`)

Chat over your test data in natural language — "why did last night's payments run fail?", "which suites got flakier this month?". Answers are grounded in your project's data (the same APIs the dashboard uses), scoped to the selected project. The same capability is available to external assistants via the [MCP server](cli-sdk-mcp.md#the-mcp-server).

**It shows its work.** When the configured engine is an LLM, chat runs a bounded
tool loop: it calls the same read APIs you could call yourself, and the reply
carries a **tool trace** of what it looked at. If an answer seems wrong, the
trace tells you which query produced it rather than leaving you to guess.

**Suggested actions are handoffs, not actions.** A reply may offer follow-ups
(open the failing run, quarantine a flaky test). These are links into the normal
UI — chat proposes, you decide. Nothing is changed on your behalf from the chat
box.

**It is bounded on purpose**, so a question can't run away with your budget:

| bound | what it limits |
|---|---|
| `CHAT_TOOL_LOOP_MAX_CALLS` | how many tools one question may call |
| per-call + total token budgets | how much tool output can enter the context (enforced server-side) |
| `AI_TIMEOUT_SECONDS` | hard wall-clock cap on the whole loop |

**Project scope is bound before the loop starts** — the LLM never scopes its own
query, so it cannot widen beyond the project you selected. Any failure inside
the loop falls back to a single-shot answer rather than erroring.

> In **rules** or **ML** mode the tool loop never engages and chat behaves as it
> did before — the feature degrades to the simpler path rather than breaking.
> Requires the `ask_ai_chat` flag (on by default) and a reachable local LLM.

## What confidence scores actually mean

Every analysis carries a 0–100 **confidence score**, but the number's *meaning* depends on which engine produced it — and TestLookup now labels that explicitly (the **confidence basis**, shown as a `calibrated` / `estimated` chip next to the score):

- **Rules engine** — each of the ~23 rules (6 statistical heuristics + 17 error-message patterns) has a *named confidence band* defined in one auditable table (`backend/app/services/confidence_bands.py`), each with a documented basis:
  - `heuristic_estimate` — an engineering estimate of how often the rule is right. **This is currently the basis for every rules-engine band**: no labeled corpus of raw error messages exists yet to measure per-rule precision, and the values are preserved from the original engine so behavior is stable. The UI tooltip reads "estimated heuristic confidence — not empirically calibrated".
  - `empirical` — reserved for bands whose value equals measured precision on a labeled evaluation dataset (with the corpus and sample count recorded in the band's provenance). When such a corpus lands, bands flip to this basis individually.
  - One band is *dynamic*: the historical-flakiness heuristic grows with evidence — `min(85, 50 + 2 × runs of history)` — because ten runs of pass/fail history genuinely say more than five.
- **LLM analyses** — the model's self-reported confidence, then *adjusted* by deterministic validators (capped when there's no evidence, when no tools ran, when the summary is too thin; small bonus for multiple corroborating sources). Every adjustment is recorded in the per-test decision audit.
- **ML classifier** — the model's class probability.

Downstream thresholds read the score uniformly regardless of basis: results below the review threshold (default 70 in-engine, `AI_CONFIDENCE_THRESHOLD=80` in the pipeline) are flagged **requires human review**; auto-triage and defect auto-actions gate on the same knobs. The basis label exists so a human deciding whether to *trust* a 75 knows whether that 75 was measured or estimated.

## Which engine actually answered (provenance)

An analysis card tells you *what produced it*, not just what it concluded. Every analysis carries a **provenance** block: the engine that ran (`rules` / `ml` / `llm`), what was originally requested, and — the case that matters — whether a **fallback** happened and why. If Ollama was unreachable and the rules engine answered instead, the card says so; heuristics are never presented as model output. The block also carries the provider/model identity, the prompt version tags in force, and the confidence basis.

Analyses recorded before this feature carry no routing metadata, so their provenance is simply **absent** rather than reconstructed — a blank is honest; a plausible guess is not.

## The confidence gate (when automation is allowed to act)

Automation acts on an AI conclusion only when its confidence clears a single configurable gate — **`ai_confidence_threshold`** on `/settings/ai` (default `AI_CONFIDENCE_THRESHOLD`, 80). Below the gate the automation does **not** act: it degrades to the deterministic path (hold for human review) and the output is marked **"low confidence — needs human review"** explicitly, as a field on the API contract, so no UI has to guess at a cutoff.

The comparison is `confidence >= threshold` — a score exactly at the threshold passes. A missing confidence never passes, and is reported as *not evaluated* rather than as low: "we did not judge this" is a different statement from "we judged it poor".

Every evaluation is recorded as a **threshold check** — `{threshold, observed_confidence, passed, source}`, where `source` is `ai_config` (an operator override is stored) or `env_default` — persisted alongside the routing decision and surfaced in the run's **decision trail**, which also rolls up how many analyses fell below the gate.

> **This is a policy dial, not a calibration.** The numbers being compared are self-declared estimates (see the basis discussion above — every rules-engine band is currently `heuristic_estimate`, and a human correction pins a hard-coded 95). "Confidence ≥ 80" means *the engine claimed at least 80*, not *this is right 80% of the time*. Raising the gate buys caution, not accuracy.

Two thresholds are deliberately **not** wired to this knob:

- **The PR-comment kind-label display floor (60)** — a display floor and an action gate answer different questions. Nothing acts on a PR-comment label, and coupling them would mean tightening the action gate silently strips labels off PR comments.
- **Fixer candidate selection** — its inputs (quarantine state, flip rate, prior attempt count) are entirely deterministic. There is no AI confidence there to gate on, and inventing one would be theatre. Fixer's safety story is its attempt budget, test-code-only restriction, ephemeral sandbox, and human-merged draft PR.

## Configuring the AI tier (`/settings/ai`, `/settings/ai-eval`)

- **AI Settings** — analysis mode (rules/ML/LLM/auto), local-LLM (Ollama) model selection, and budget controls. Per-project daily budgets cap LLM spend; over budget, analysis downgrades mode instead of stopping.
- **AI Evaluation** — the quality dashboard for the AI tier itself: how analyses are rating, feedback volumes, evaluation runs, and **Training Label Health** — how much of the ML training pool is human-verified vs the LLM's own output. If your team corrects verdicts regularly (do — corrections compound), this is where you watch accuracy improve.

## How the ML classifier actually learns

Honesty first: the local ML classifier trains on two very different kinds of labels, and the distinction is tracked end to end.

- **Human labels** — your team confirming or correcting AI verdicts (the feedback card, the classifier-correction dialog, the MCP `correct_classification` tool → *human_direct*) and Jira-resolution auto-labels (*human_indirect*). These are ground truth and always train at full weight.
- **LLM pseudo-labels** (*llm_pseudo*) — high-confidence analyses nobody has confirmed. They are the model's own opinion. Training on them unchecked teaches the classifier to imitate the LLM, not to learn from you — so they are **capped at 30% of the training set and down-weighted to 0.3** (`ML_PSEUDO_LABEL_CAP` / `ML_PSEUDO_LABEL_WEIGHT`).

With few human labels, pseudo-labels may fill the set up to the training minimum — that is a deliberate bootstrap, and the system says so: below **50 human labels** (`ML_HUMAN_LABEL_FLOOR`) the ML tier reports itself as **bootstrap (LLM-imitating)** in AI Settings, on the AI Evaluation dashboard, and in each analysis's routing decision record. In that state ML mode reproduces the LLM's behavior (cheaply and offline) — it does *not* yet learn from your corrections. Every deployed model records the exact label mix it was trained on (`label_composition` in its metadata, surfaced at `GET /api/v1/ai-eval/label-health`), so "the model learns from your feedback" is a claim you can verify, not marketing. The fastest way out of bootstrap: confirm or correct verdicts on `/failures` — each one is a ground-truth label.

## What needs what

| Feature | Works with rules/ML only (default) | Needs local LLM enabled |
|---|---|---|
| Failure categories, clusters, flaky verdicts | ✅ | — |
| Run Intelligence reports | ✅ (rules/ML content) | richer narratives |
| Deep Investigation | ✅ clustering + evidence | richer per-cluster analysis |
| Ask AI chat | — | ✅ |

Start on the default (offline, rules+ML) and enable the local LLM when you want narrative depth — see the LLM stack notes in [GETTING_STARTED.md](../GETTING_STARTED.md) (`make dev-llm`).
