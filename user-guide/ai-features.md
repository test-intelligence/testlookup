# AI configuration

How to configure and govern the AI parts of TestLookup: the analysis engine and its models, Ask AI, the LLM cost budget, AI evaluation, and the human review gate for AI reports. How these features look to an end user (the run's Analysis tab, Root cause (AI), Pipeline runs, Ask AI, the Inbox Approvals tab) is in the in-app guide: `/docs/ai-agents`, `/docs/ask-ai`, `/docs/failure-analysis` and `/docs/inbox` (sources under [frontend/src/content/guide/](../frontend/src/content/guide/ai-agents.md)).

Two rules hold everywhere:

1. **It works offline by default.** `AI_OFFLINE_MODE=true` is the default. Rules and the local ML classifier always run; the optional language-model tier uses a model on your own network (Ollama by default). Analysis degrades from the language model to ML to rules instead of failing when a model is missing. In offline mode the app only connects to a model endpoint whose address is loopback or private, so nothing reaches a hosted API.
2. **Every verdict carries provenance.** Each analysis records which engine produced it (rules, ML, language model, or a human correction) and why, so "the AI said so" can always be inspected and corrected.

## Configuring the AI tier (`/settings/ai`, `/settings/ai-eval`)

**Settings → AI → AI configuration** (`/settings/ai`). QA leads can open it and read the configuration; only administrators can change it (**Save Configuration**).

| Card | What you set |
|---|---|
| **Analysis Engine** | **Auto** (ML when a trained model exists, else the language model when reachable, else rules), **LLM (AI Agent)**, **Machine Learning** or **Rules-Based**. Each option shows whether it is ready: the language model's reachability, and the ML model's state (**Not Trained**, **Ready (N% accuracy)**, or **Bootstrap (LLM-imitating)**) |
| **Model Availability & Fallback Chain** | Read-only: offline mode, provider, mode, whether Ollama answers, whether each **Required model** is **Installed** or **Missing**, and the order the engines will be tried. **Re-check** probes again |
| **LLM Provider** | **Provider** (`ollama`, `lmstudio`, `localai`, `vllm`, `openai`, `gemini`, `anthropic`, `openrouter`), **Model**, **Temperature** and **Max Tokens** |
| **Embedding** | The embedding provider and model used for semantic search and knowledge-grounded generation |
| **Pipeline Settings** | **Confidence Threshold (0-100)**, **Timeout (seconds)**, **Offline Mode** (shows **Pinned by environment** when the server pins it), **Deep Investigation**, **Fine-Tuning Pipeline** |
| **Knowledge-Grounded Generation** | **Enable Knowledge RAG** for [test generation from your documents](knowledge-base.md); while the `rag_faithfulness_gate` flag is on, **Faithfulness evaluator** picks the single-prompt judge or Ragas and shows which one is in use |
| **Cloud API Keys** | OpenAI, Google, Anthropic and OpenRouter keys, stored encrypted. Only used when offline mode is off |

Defaults on a new install: provider `ollama`, model `qwen2.5:7b-instruct`, embedding model `nomic-embed-text:v1.5`, confidence threshold 80, offline mode on, analysis mode **Auto**. The server-side settings behind these (`LLM_PROVIDER`, `LLM_MODEL`, `EMBEDDING_MODEL`, the provider base URLs and allow-lists, `LLM_PRICE_OVERRIDES`) are listed in the [configuration reference](../docs/operations/configuration.md). Getting the models onto an air-gapped host is the [Offline model pack](offline-model-pack.md).

> **Deep investigation is switched by the server.** The **Deep Investigation** checkbox is stored with the configuration, but starting an investigation is governed by the server setting `DEEP_INVESTIGATION_ENABLED` (default on). Investigations are also refused when the analysis mode resolves to **Rules**.

**Settings → AI → AI evaluation** (`/settings/ai-eval`) measures the AI tier itself; see [AI evaluation](#ai-evaluation) below.

## What confidence scores actually mean

Every analysis carries a 0–100 **confidence score**, but its meaning depends on the engine that produced it, and the app labels that **basis** next to the score:

- **Rules engine**: each rule has a named confidence band defined in one auditable table (`backend/app/services/confidence_bands.py`). Every band is currently a `heuristic_estimate` (an engineering estimate, not measured precision), and the tooltip says so. One band is dynamic: the historical-flakiness heuristic grows with evidence, `min(85, 50 + 2 × runs of history)`.
- **Language-model analyses**: the model's self-reported confidence, then adjusted by deterministic validators (capped when there is no evidence or no tools ran, a small bonus for corroborating sources). Every adjustment is recorded in the per-test decision audit.
- **ML classifier**: the model's class probability.

## The confidence gate

Automation acts on an AI conclusion only when its confidence clears the **Confidence Threshold** on `/settings/ai` (default `AI_CONFIDENCE_THRESHOLD`, 80). Below it, the automation does not act: the result is held for human review and marked **"low confidence — needs human review"** as a field of the API contract. The comparison is `confidence >= threshold`; a missing confidence never passes and is reported as *not evaluated*.

Each check is recorded (`{threshold, observed_confidence, passed, source}`, where `source` is `ai_config` for a stored override or `env_default`) and rolled up in the run's decision trail.

> **This is a policy dial, not a calibration.** "Confidence ≥ 80" means the engine *claimed* at least 80, not that it is right 80% of the time. Raising the gate buys caution, not accuracy.

Some pipeline stages read the server default `AI_CONFIDENCE_THRESHOLD` directly rather than the stored value, so if you change the threshold on the page, set the environment value to the same number.

Two thresholds are deliberately not wired to this dial: the display floor for kind labels on pull-request comments (60), and the Fixer's candidate selection, which uses only deterministic inputs.

## Ask AI

End users see Ask AI when the `ask_ai_chat` flag is on (default on) **and** the analysis mode is **Auto** or **LLM**. In **Rules** or **ML** mode the server refuses chat requests (`409 llm_disabled`) and the page says so. The flag only hides the sidebar entry and the run page's **Ask AI about this run**; it does not disable the chat API, so to turn chat off completely, use a Rules or ML analysis mode.

Every chat model call goes through the same budget, cost cap and circuit breaker as the analysis pipeline, and is charged to the project. The project is bound on the server; the model never names a project or an id. Conversations are private to the user who started them (administrators can open any).

The limits are server settings:

| Setting | Default | Limits |
|---|---|---|
| `CHAT_MAX_TOOL_ROUNDS` | 4 | Rounds of lookups per answer; after the last, the model must answer |
| `CHAT_FIRST_TOKEN_TIMEOUT_SECONDS` | 45 | The wait for the provider's first text, and any silence after it |
| `CHAT_TURN_TIMEOUT_SECONDS` | 120 | The whole answer, lookups included |
| `CHAT_HISTORY_MESSAGES` | 12 | Earlier messages replayed to the model |
| `CHAT_LLM_MODEL` | empty | A model for chat only, on the same provider (for example a better tool user); empty means `LLM_MODEL` |

A provider that cannot take tool calls still answers, from the latest-runs data and the lookups made before the model is called.

## LLM cost budget

**Settings → AI → LLM cost budget** (`/settings/billing`) caps what each project spends on language-model calls.

> **Off by default.** Enforcement is behind the `llm_cost_budget` feature flag, which ships **off**. With it off, no usage is recorded and no cap applies; the page still opens and shows zero spend and **UNLIMITED**. Turn the flag on under **Settings → System → Feature flags** ([feature-flag reference](../docs/operations/feature-flags.md)).

The page shows the current period's **Total spend**, **LLM calls** and **Projects over soft-warn**, and a row per project with spend, cap, utilisation, status (**OK**, **SOFT_WARN**, **CAPPED** or **UNLIMITED**) and cap hits. Administrators edit a project's budget (**Edit**); others can **View** it:

| Field | Meaning |
|---|---|
| **Included per month ($)** | Spend covered before overage applies |
| **Hard cap ($)** | The ceiling for the month |
| **Overage rate ($/$)** | How spend above the included amount is counted |
| **Soft-warn threshold (%)** | The utilisation at which the at-cap action starts |
| **At-cap action** | **Soft warn** (log only), **Auto-downgrade to ML**, **Auto-downgrade to rules**, or **Hard block** |
| **Budget enforcement enabled** | Per-project on/off |

Budgets are **monthly**. Below the soft-warn threshold nothing happens; above it, the at-cap action applies. At 100% of the hard cap, calls are always blocked and a `quota.exceeded` event is sent to outbound webhooks. A failure of the budget check itself lets the call through, so a broken budget table never stops analysis; the separate per-call reservation, when a cap exists, refuses a call that would overrun it.

Costs are computed from a built-in price table; `LLM_PRICE_OVERRIDES` (JSON, `{"provider:model-regex": {"input_per_mtok": ..., "output_per_mtok": ...}}`) adds or corrects prices. Self-hosted providers (`ollama`, `lmstudio`, `localai`, `vllm`) always count as zero cost. A hosted model with no price entry is recorded as **unpriced**: it counts as zero against the budget, logs a warning and increments the `llm_unpriced_calls_total` metric, so add an override for any hosted model you use.

## AI evaluation

**Settings → AI → AI evaluation** (`/settings/ai-eval`) has three tabs. QA leads can read everything; running evaluations, creating datasets and setting baselines need an administrator.

- **Quality Overview** — runtime evaluation provenance, **Human-AI Agreement (30d)**, **Training Label Health** (how much of the ML training pool is human-verified), decision-report evaluation, **Quality Drift**, recent evaluation runs and model version history.
- **Evaluation Datasets** — **Generate from Feedback** builds a dataset from your team's confirmations and corrections; **Seed Golden Datasets** loads the reference sets; **Run Evaluation** scores the current AI against a dataset.
- **Evaluation Gate** — run the pre-release gate for one task type (**Gate: Classification**, **Gate: Root Cause**, **Gate: Duplicate Detection**, **Gate: Release Decision**) and read accuracy, precision, recall, F1 and the rules that passed or failed. **Active Baselines** lists the baselines each gate compares against; baselines are set through the API (`/api/v1/ai-eval/baselines`).

The agent-stack release gate, tier comparison and reviewer-quality reports exist in the API (`/api/v1/ai-eval/...`, see `/api-docs`) but have no page yet. Design notes: [architecture/AI_EVALUATION.md](../architecture/AI_EVALUATION.md).

## The human review gate for AI reports

Every AI report the pipeline produces is a **review request**: `pending_review`, then `accepted`, `rejected` or `superseded` (by a newer report for the same run). Reviewers work from **Inbox → Approvals** (`/my-failures?tab=approvals`, filter **AI reports**): **Accept**, or **Reject** with a reason (wrong category, unsupported claim, missing evidence, contradiction, stale data, other) and optional notes.

Settling a review needs the `QA_LEAD` role and membership of the project (or `ADMIN`), signed in interactively: API keys and synthetic accounts are refused. When the run proposes actions for an agent to take, the person who requested the run cannot settle its review (separation of duties). The report then carries a banner: **Draft: awaiting human review**, **Reviewed and accepted**, **Rejected by a reviewer** or **Superseded by a newer report**.

Whether an unreviewed report may leave the app is the server setting **`REVIEW_GATE_ENFORCED`** (default `false`):

| | `false` (default) | `true` |
|---|---|---|
| Exports, shared links, notifications, webhooks, PR/MR comments carrying an unreviewed report | Allowed. Each would-be refusal is logged to the access audit log as `ai_report.distribution_would_refuse`, so you can see what enforcement would block before turning it on | Refused (`409`). Notifications say an AI summary is ready and awaiting human review instead of including it. A QA lead can still export one deliberately (`include_unreviewed=true` on the run PDF export); it goes out watermarked "DRAFT - AI-generated, not human-reviewed" and audited. Rejected and superseded reports are never distributed |
| Release verdict on an unreviewed report | The computed verdict | `PENDING_REVIEW` until a reviewer settles it |
| AI kind labels on PR/MR comments | Shown | Stripped until reviewed |

Run with it `false` for a while, read the would-refuse audit rows, make sure reviewers are keeping up with the Approvals queue, then turn it on.

## How the ML classifier learns

The local ML classifier trains on two kinds of labels, and the difference is tracked end to end.

- **Human labels**: your team confirming or correcting verdicts (the feedback controls, **Correct classification**, the MCP `correct_classification` tool), and Jira-resolution auto-labels. These are ground truth and train at full weight.
- **Language-model pseudo-labels**: high-confidence analyses nobody has confirmed. They are capped at 30% of the training set and down-weighted to 0.3 (`ML_PSEUDO_LABEL_CAP`, `ML_PSEUDO_LABEL_WEIGHT`).

Below **50 human labels** (`ML_HUMAN_LABEL_FLOOR`) the ML tier reports itself as **Bootstrap (LLM-imitating)** on the AI configuration page, on the evaluation dashboard and in each analysis's routing record: it reproduces the language model's behaviour cheaply and offline, but does not yet learn from your corrections. Every deployed model records the label mix it was trained on (`GET /api/v1/ai-eval/label-health`). The fastest way out of bootstrap is to confirm or correct verdicts on **Failures** and on test results.

## What needs what

| Feature | Rules and ML only (default) | Needs a reachable language model |
|---|---|---|
| Failure categories, clusters, flaky scores, release verdicts | Yes | — |
| A run's Analysis tab | Yes (rules and ML content) | Richer narratives |
| Root cause (AI) deep investigation | Yes, clustering and evidence (not in **Rules** mode) | Richer per-cluster analysis |
| Ask AI | — | Yes |
| Generated test cases (knowledge base) | A stub | Yes |

On Docker Compose, `make dev-llm` starts the stack with a local model (see [GETTING_STARTED.md](../GETTING_STARTED.md)).

## Related

- [Administration & settings](administration.md) — the Investigator and Fixer agents, agent activity, feature flags
- [Offline model pack](offline-model-pack.md)
- [Knowledge base & test generation](knowledge-base.md)
- [architecture/AI_QUALITY.md](../architecture/AI_QUALITY.md)
