# Troubleshooting & FAQ

A symptom-first index into the rest of the guide. Find your symptom, jump to the likely cause. When a fix lives in another guide, this points you there rather than repeating it.

## "The page is empty — no data, no error"

The classic. Check these three, in order:

1. **No project selected.** Almost nothing renders until a specific project is chosen in the top bar. If you're an admin in **All Projects** mode, some pages (and the Runs **Upload** button) are intentionally disabled — pick one project.
2. **The time window excludes your data.** Every analytics page shares one [time window](dashboards.md#the-golden-rule-the-shared-time-window). If it's set to "last 7 days" and your last run was two weeks ago, the page is correctly empty. Widen it.
3. **A backend validation error (422).** These now surface as a toast (they used to be silent — the historical "empty page" footgun). If you see a red validation toast, the request shape was rejected; the message names the field.

## "Counts don't match between two pages"

Almost always one of two things — see the [discrepancy cheat-sheet](dashboards.md#reading-discrepancies-cheat-sheet):

- **Different aggregation.** The Summary Report has a **window** mode (unique tests across the window — matches Coverage) and a **latest** mode (latest run per suite — deliberately smaller). Two pages in different modes *should* differ.
- **Executions vs unique tests.** The Runs page counts executions; Coverage counts unique tests. A suite run 5× shows 5 executions but maybe 20 unique tests.
- **Different window.** The shared window follows you, but confirm both pages are actually on the same one.

## "A run shows totals but no per-test rows"

The run finalized its aggregates but the per-test persistence lagged or failed:

- **Just now?** Give it a few seconds — processing is asynchronous.
- **Persistently?** This is an operator-side issue: the run's per-test rows are queued to an ingestion **shard**, and workers must subscribe to **all** shard queues. A worker on the plain `ingestion` queue only produces exactly this symptom. (Details: [architecture/INGESTION_SCALE.md](../architecture/INGESTION_SCALE.md).) The auto-recovery beat also re-stages such runs within a couple of minutes.

## "`/suites` or `/coverage` is empty but `/runs` is populated"

Same root cause as above — the shard-queue subscription gap on the worker. It's an operator fix, not a problem with your upload.

## "The Upload button is missing on Runs"

Two possibilities:

- You're in **All Projects** mode — pick a specific project.
- The `manual_upload` feature flag is off (admins toggle it under [Feature Flags](administration.md#operating-the-instance)).

## "My webhook / Jira ticket / integration didn't fire"

- **Offline mode.** `AI_OFFLINE_MODE` defaults to **on**, and it's a hard kill-switch *above* every integration flag — in a default install nothing outbound fires regardless of settings. Enable outbound integrations deliberately.
- **Then check [Integration Health](administration.md#connecting-to-your-world)** (`/settings/integration-health`) — it's the status board for configured/reachable/delivering, and the first place to look.
- **Then the [Audit Dashboard](administration.md#operating-the-instance)** for whether the event was recorded at all.

## "AI features (chat, deep investigation narratives) aren't working"

The AI tier has two levels. Rules + ML run offline by default; the **LLM** tier needs a local model (Ollama). If chat is unavailable or investigations lack narrative depth, the local LLM isn't enabled — start it with the local-LLM profile (`make dev-llm`; see [AI features](ai-features.md#what-needs-what)). Analysis never hard-fails on a missing model — it degrades LLM → ML → rules.

## "My Failures is empty but I know there are failures"

If you're an admin/QA-lead, you're probably on **Mine** scope while the failures auto-assigned to the project's default QA-lead user. Flip to **Team** scope ([Triaging failures](triaging-failures.md#your-inbox-my-failures)), then configure real suite owners so future failures route to people.

## "The bisect / compare button isn't available"

Bisect-from-green needs both a green baseline and a failed run for the suite in the current window. No green run in range → no baseline to bisect from. Widen the window. (See [Working with runs](working-with-runs.md#bisect-from-green--the-regression-shortcut).)

## "Generated test cases look wrong / aren't appearing"

- **Generation needs the optional AI stack** (local LLM); offline it returns a stub. Enable local-LLM.
- **Low faithfulness?** That's the signal to reject — the draft isn't supported by its citations. Read the cited chunks. ([Knowledge base](knowledge-base.md).)
- **No relevant results?** You may be in the wrong project, or the source isn't indexed yet (check its freshness).

## "Changes I made aren't showing in the app"

If you deployed a change and don't see it, you may be looking at a **stale browser bundle**. Hard-refresh; if it persists, an operator should confirm the frontend image/bundle actually updated.

## Still stuck?

- **Live probe** the affected page before assuming a deploy problem — most "empty page" reports resolve to project/window/scope above.
- **Operators**: `/health/details` gives per-dependency status + the running build's provenance; the [observability stack](../architecture/OBSERVABILITY.md) has metrics, traces, and alerts.
