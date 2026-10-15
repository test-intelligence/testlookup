# AI configuration

How to configure and govern the AI parts of TestLookup: the analysis engine and its models, Ask AI, the LLM cost budget, AI evaluation, and the human review gate for AI reports. How these features look to an end user (the run's Analysis tab, Root cause (AI), Pipeline runs, Ask AI, the Inbox Approvals tab) is in the in-app guide: `/docs/ai-agents`, `/docs/ask-ai`, `/docs/failure-analysis` and `/docs/inbox` (sources under [frontend/src/content/guide/](../frontend/src/content/guide/ai-agents.md)).

Two rules hold everywhere:

1. **It works offline by default.** `AI_OFFLINE_MODE=true` is the default. Rules and the local ML classifier always run; the optional language-model tier uses a model on your own network (Ollama by default). Analysis degrades from the language model to ML to rules instead of failing when a model is missing. In offline mode the app only connects to a model endpoint whose address is loopback or private, so nothing reaches a hosted API.
2. **Every verdict carries provenance.** Each analysis records which engine produced it (rules, ML, language model, or a human correction) and why, so "the AI said so" can always be inspected and corrected.

## Configuring the AI tier (`/settings/ai`, `/settings/ai-eval`)

**Settings → AI → AI configuration** (`/settings/ai`). QA leads can open it and read the configuration; only administrators can change it (**Save Configuration**).

| Card | What you set |
|---|---|
| **Analysis Engine** | **Auto** (ML when a trained model exists, else the language model when reachable, else rules), **LLM (AI Agent)**, **Machine Learning** or **Rules-Based**. Each option shows whether it is ready: the language model's reachability, and the ML model's state (**Not Trained**, **Ready (N% accuracy)**, or **Bootstrap (LLM-imitating)**). Under the options, the **ML classifier** card: labelled samples against the floor, the model in service, and **Train now** ([how the ML classifier activates](#how-the-ml-classifier-activates)) |
| **Model Availability & Fallback Chain** | Read-only: offline mode, provider, mode, whether Ollama answers, whether each **Required model** is **Installed** or **Missing**, and the order the engines will be tried. **Re-check** probes again |
| **LLM Provider** | **Provider** (`ollama`, `lmstudio`, `localai`, `vllm`, `openai`, `gemini`, `anthropic`, `openrouter`), **Model**, **Temperature** and **Max Tokens** |
| **Embedding** | The embedding provider and model used for semantic search and knowledge-grounded generation |
| **Pipeline Settings** | **Confidence Threshold (0-100)**, **Timeout (seconds)** (5 to 1800), **Offline Mode** (shows **Pinned by environment** when the server pins it), **Deep Investigation**, **Fine-Tuning Pipeline** |
| **Knowledge-Grounded Generation** | **Enable Knowledge RAG** for [test generation from your documents](knowledge-base.md); while the `rag_faithfulness_gate` flag is on, **Faithfulness evaluator** picks the single-prompt judge or Ragas and shows which one is in use |
| **Cloud API Keys** | OpenAI, Google, Anthropic and OpenRouter keys, stored encrypted. Only used when offline mode is off |

Defaults on a new install: provider `ollama`, model `qwen2.5:7b-instruct`, embedding model `nomic-embed-text:v1.5`, confidence threshold 80, offline mode on, analysis mode **Auto**. The server-side settings behind these (`LLM_PROVIDER`, `LLM_MODEL`, `EMBEDDING_MODEL`, the provider base URLs and allow-lists, `LLM_PRICE_OVERRIDES`) are listed in the [configuration reference](../docs/operations/configuration.md). Getting the models onto an air-gapped host is the [Offline model pack](offline-model-pack.md).

**What you save here is what runs.** Each value on the page says where it comes from: **Set here** (stored on this page) or **Server default** (nothing stored; the server's environment value applies, for example `DEEP_INVESTIGATION_ENABLED` or `AI_TIMEOUT_SECONDS`). A stored value wins over the environment in both directions; only **Offline Mode** is different (the environment can pin it on, and this page can then not turn it off). The form saves every field, so after the first **Save Configuration** every value reads **Set here**. A change applies on every server within a minute; pipelines already running keep the values they started with.

- **Analysis mode** is kept until you change it: a cache restart or the passing of time never sends the app back to the server default.
- **Deep Investigation** off: the deep-investigation buttons (**Run Deep Analysis** on `/deep-investigate`, **Analyze failures** on the run's Analysis tab, **Deep investigation** in the run's ⋯ menu) are replaced by *Deep investigation is turned off in Settings › AI.*, and a direct API call answers `422` *Deep investigation is disabled in Settings › AI. Ask an administrator to enable it.* Investigations are also refused while the analysis mode resolves to **Rules** (*Deep investigation needs LLM or Auto mode*). Resuming a failed investigation (**Retry from last successful stage**) is not a new investigation and stays allowed, unless the configuration changed since it started (the retry then starts a new run, which the switch refuses).
- **Fine-Tuning Pipeline** switches fine-tuning on and off: the daily trigger check, the trigger after a training export, and the fine-tuning run itself. The weekly training-data export always runs. Fine-tuning itself needs a GPU or a hosted provider.
- **Timeout (seconds)** bounds every language-model call: the analysis stages, the summary, release-risk and regression-watchman stages, Ask AI's history compression and knowledge-grounded generation. A project's agent configuration may set a lower limit for one agent, and a few stages have a lower ceiling of their own (summary 120 s, release risk and regression watchman 90 s). A pipeline keeps the timeout it started with. An analysis that times out falls back to a faster classifier, then to rules, and records `llm_timeout: LLM timed out after N s` in its decision trail. A very low value on a slow model also counts against the language model's circuit breaker, which then pauses calls for a while.

**Settings → AI → AI evaluation** (`/settings/ai-eval`) measures the AI tier itself; see [AI evaluation](#ai-evaluation) below.

### How the ML classifier activates

The ML tier is a classifier trained on your own labelled failures. It needs no language model, runs on the CPU, and makes no outbound call.

- **Samples.** A labelled sample is a failure your team confirmed or corrected (on the run's Analysis tab, or through Jira resolutions), or a high-confidence AI verdict nobody has reviewed (capped, and weighted lower, so human labels dominate). Training needs at least **200** (`ML_MIN_TRAINING_SAMPLES`).
- **When it trains.** Nightly at 03:10 UTC (switch the nightly run off with `ML_RETRAIN_ENABLED=false`), or now: an administrator presses **Train now** on the ML classifier card. Below 200 samples the button is disabled and says how many are missing. While a run is queued or going the button reads **Training…**, and the card shows the new model when it finishes, usually within a minute.
- **What it keeps.** A model is put in service only when its accuracy on held-out samples reaches `ML_ACCURACY_THRESHOLD` (0.80). Otherwise the previous model stays and the card shows the attempt (*Last attempt …: Accuracy 0.61 is below the deployment threshold 0.80; the previous model is kept.*). Every model put in service is recorded in the settings audit log (`ml_classifier`, `trained`, with its version, accuracy, sample count and who triggered it).
- **What uses it.** In **Auto** mode a trained model is preferred over the language model; the pipeline's decision trail says *auto: trained ML model available — preferred over LLM*. **Machine Learning** mode uses it always. Until your team has confirmed or corrected 50 verdicts (`ML_HUMAN_LABEL_FLOOR`), the model is marked **Bootstrap (LLM-imitating)**: it mostly reproduces the AI's own verdicts.
- **Several servers.** The model is trained by a worker and shared with every server through object storage (`ML_MODEL_SYNC_ENABLED`, on by default). If the store is unreachable, each server keeps the model it has.

The card reads `GET /api/v1/settings/ai/ml/status` (QA lead and above); **Train now** is `POST /api/v1/settings/ai/ml/retrain` (administrators, not API keys; pressing it again while a run is going joins that run).

## What confidence scores actually mean

Every analysis carries a 0–100 **confidence score**, but its meaning depends on the engine that produced it, and the app labels that **basis** next to the score:

- **Rules engine**: each rule has a named confidence band defined in one auditable table (`backend/app/services/confidence_bands.py`). Every band is currently a `heuristic_estimate` (an engineering estimate, not measured precision), and the tooltip says so. One band is dynamic: the historical-flakiness heuristic grows with evidence, `min(85, 50 + 2 × runs of history)`.
- **Language-model analyses**: the model's self-reported confidence, then adjusted by deterministic validators (capped when there is no evidence or no tools ran, a small bonus for corroborating sources). Every adjustment is recorded in the per-test decision audit.
- **ML classifier**: the model's class probability.

## The confidence gate

Automation acts on an AI conclusion only when its confidence clears the **Confidence Threshold** on `/settings/ai` (default `AI_CONFIDENCE_THRESHOLD`, 80). Below it, the automation does not act: the result is held for human review and marked **"low confidence — needs human review"** as a field of the API contract. The comparison is `confidence >= threshold`; a missing confidence never passes and is reported as *not evaluated*.

Each check is recorded (`{threshold, observed_confidence, passed, source}`, where `source` is `ai_config` for a stored override or `env_default`) and rolled up in the run's decision trail.

> **This is a policy dial, not a calibration.** "Confidence ≥ 80" means the engine *claimed* at least 80, not that it is right 80% of the time. Raising the gate buys caution, not accuracy.

Every pipeline stage that routes on confidence (the plan, triage, gap detection, the per-analysis check) uses the threshold stored on the page, taken once when the pipeline starts: a running or resumed pipeline keeps the number it started with, and **Retry** after you changed it starts a new run instead of resuming. When nothing qualifies, the decision trail says so (*no analyses above confidence threshold 95 — skipping triage*).

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

> **On for a new install.** Enforcement is behind the `llm_cost_budget` feature flag, which is on for a new install; an install upgraded from an earlier version keeps the state it had (off unless an administrator turned it on). With it off, no usage is recorded and no cap applies; the page still opens and shows zero spend and **UNLIMITED**. Turn the flag on or off under **Settings → System → Feature flags** ([feature-flag reference](../docs/operations/feature-flags.md)).

The page shows the current period's **Total spend**, **LLM calls**, **Projects over soft-warn** and **Projected overage charge**, and a row per project with spend, **Covered**, **Overage**, **Charge** (with a month-end projection under it, *proj. $X by month end*), cap, utilisation, status (**OK**, **SOFT_WARN**, **CAPPED** or **UNLIMITED**) and cap hits. Administrators edit a project's budget (**Edit**); others can **View** it:

**Included per month**: spend up to this amount is covered; spend above it is overage. **Overage rate**: the overage is multiplied by this to give the charge. The table shows Covered, Overage, Charge and a month-end projection. The projection is linear (spend so far divided by the share of the month that has passed) and appears once a day of the month has passed and something was spent. A project with no included amount (0, the form's default) or no enabled budget shows **—** in the three columns: no charge is computed for a project that only set a cap. The overage figures are for the billing view; they never limit spend (the hard cap does).

| Field | Meaning |
|---|---|
| **Included per month ($)** | Spend up to this amount is covered; spend above it is overage |
| **Hard cap ($)** | The ceiling for the month |
| **Overage rate ($/$)** | The overage is multiplied by this to give the charge |
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

## AI history: every analysis is kept

A run can be analysed more than once (a re-triggered pipeline, a different engine or model, a correction). Each analysis is kept as an immutable **version** instead of overwriting the previous one, with its provenance (rules, ML or a language model, which model, whether it fell back):

- **Per run**: a digest of each pipeline's analysis (verdict and risk, categories, failure clusters, each analysed test's category, confidence and suggested root cause) and the run summary written with it. The run's Analysis tab lists them (**v2 · LLM llama3.1 · about 2 hours ago**), shows an older one read-only and diffs any two; the Evidence tab's decision-report version follows the same choice. In-app: `/docs/runs#ai-history`.
- **Per test result**: every pipeline analysis, single-test re-analysis, human correction and bulk reclassification of that result, shown on the test's History tab.
- **vs previous run**: the Analysis tab and Run Compare also diff a run's AI analysis with another run's. The default baseline is the previous run of the same suite **on the same branch** (the newest that finished before this run, any outcome); **Compare to previous run** on the Runs list and the run's ⋯ menu use the same server rule, so they open the same pair (they used to pick from the runs loaded on the page, on any branch). When the two analyses came from different engines, the diff says so first, so an engine change is not read as a product change.

The diff itself never needs a language model. Its one-paragraph narrative is written by the language model when one is reachable and the project's cost budget allows (labelled **AI narrative**); otherwise it is built from the diff and labelled **deterministic summary**. Viewing the history makes no model call.

How many versions are kept (by default 20 per run and kind, 10 per test result; version 1 and any version cited by a published decision report are never pruned) is part of the project's retention policy: see [Administration & settings](administration.md). **Export AI history** in a run's ⋯ menu downloads them as JSON; like other AI report exports it goes through the [human review gate](#the-human-review-gate-for-ai-reports) (`409` while enforced and unreviewed).

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
| AI history, version diffs, "vs previous run" | Yes, with a **deterministic summary** | The **AI narrative** of a run-to-run comparison |
| Root cause (AI) deep investigation | Yes, clustering and evidence (not in **Rules** mode) | Richer per-cluster analysis |
| Ask AI | — | Yes |
| Generated test cases (knowledge base) | A stub | Yes |

On Docker Compose, `make dev-llm` starts the stack with a local model (see [GETTING_STARTED.md](../GETTING_STARTED.md)).

## Related

- [Administration & settings](administration.md) — the Investigator and Fixer agents, agent activity, feature flags
- [Offline model pack](offline-model-pack.md)
- [Knowledge base & test generation](knowledge-base.md)
- [architecture/AI_QUALITY.md](../architecture/AI_QUALITY.md)
