# AI features — intelligence, deep investigation, agents, chat

Everything AI-flavored in TestLookup follows two rules worth knowing up front:

1. **It works offline by default.** `AI_OFFLINE_MODE=true` is the default; rules and local ML always run, and the optional LLM tier uses a *local* model (Ollama) when you enable it — analysis degrades gracefully (LLM → ML → rules) rather than failing on a missing model. Nothing here sends your data to an external API in a default install.
2. **Verdicts carry provenance.** Every analysis records which engine produced it (rules / ML / LLM / human-corrected) and why, so "the AI said so" is always inspectable — and correctable (see [Triaging failures](triaging-failures.md#correcting-the-ai-and-why-its-worth-doing)).

## Run Intelligence (`/intelligence`, `/runs/:id/intelligence`)

The **Intelligence Hub** is the cross-run view: recent activity, per-run intelligence reports, cross-run AI insights, and — importantly — **Intelligence spend** (average per run), so the cost of the AI tier is visible where its output is. Pick a run to open its **Run Intelligence** report: what failed, why, clusters, risk, and the recommended actions, assembled from the pipeline's stages.

## Deep Investigation (`/deep-investigate`, `/deep-investigate/:runId`)

Deep Investigation is the heavyweight workflow for a bad run: it clusters the run's failures (you can tune the **clustering threshold**), investigates each cluster, and reports per-cluster confidence. Options include **auto-draft defects** and **auto-create Jira tickets** from investigation results (Jira requires the integration to be enabled — outbound integrations are off in offline mode). Past investigations are browsable, so an investigation is a durable artifact, not a one-off chat.

## Agent Pipeline (`/agents`, `/agents/run/:runId`)

The **Agent Pipeline** page is the operational view of the analysis pipeline itself: pick a run (by suite and build) and queue the standard pipeline, then watch **Workflow Progress** stage by stage — ingestion validation, anomaly detection, analysis, clustering, summary — ending in an **Executive Summary** / AI report. Use it when you want to (re)run analysis on demand or see exactly which stage produced a verdict.

## Ask AI (`/chat`)

Chat over your test data in natural language — "why did last night's payments run fail?", "which suites got flakier this month?". Answers are grounded in your project's data (the same APIs the dashboard uses), scoped to the selected project. The same capability is available to external assistants via the [MCP server](cli-sdk-mcp.md#the-mcp-server).

## Configuring the AI tier (`/settings/ai`, `/settings/ai-eval`)

- **AI Settings** — analysis mode (rules/ML/LLM/auto), local-LLM (Ollama) model selection, and budget controls. Per-project daily budgets cap LLM spend; over budget, analysis downgrades mode instead of stopping.
- **AI Evaluation** — the quality dashboard for the AI tier itself: how analyses are rating, feedback volumes, and evaluation runs. If your team corrects verdicts regularly (do — corrections compound), this is where you watch accuracy improve.

## What needs what

| Feature | Works with rules/ML only (default) | Needs local LLM enabled |
|---|---|---|
| Failure categories, clusters, flaky verdicts | ✅ | — |
| Run Intelligence reports | ✅ (rules/ML content) | richer narratives |
| Deep Investigation | ✅ clustering + evidence | richer per-cluster analysis |
| Ask AI chat | — | ✅ |

Start on the default (offline, rules+ML) and enable the local LLM when you want narrative depth — see the LLM stack notes in [GETTING_STARTED.md](../GETTING_STARTED.md) (`make dev-llm`).
