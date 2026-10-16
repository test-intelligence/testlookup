# Knowledge base & test generation

TestLookup can generate test cases **from your own documents** — a requirements spec, an API doc, a runbook — instead of from a blank prompt. You register the documents as **knowledge sources**, and the AI drafts test cases grounded in them, each one citing the passages it came from. Every generated case is traceable to its evidence, so you review facts, not guesses.

> This is part of the **optional AI stack** (ChromaDB and a language model). Turn it on with **Enable Knowledge RAG** under **Settings → AI → AI configuration** (administrators); that switch is the `knowledge_rag` feature flag. Without a reachable model, generation returns a deterministic stub rather than calling one — on Compose, start the local-LLM profile ([GETTING_STARTED](../GETTING_STARTED.md), `make dev-llm`) to use it for real. `AI_OFFLINE_MODE` keeps the model and URL fetches on your own network. Internals: [architecture/KNOWLEDGE_RAG.md](../architecture/KNOWLEDGE_RAG.md).

## Registering knowledge sources

A knowledge source is a document you want the generator to draw on — an upload or a URL. Manage them per project; each source is **chunked and indexed** into that project's private knowledge base (one project's documents never surface in another's generation).

Sources track their own health:

- **Sync history** — when the source was last (re)indexed.
- **Freshness** — whether the indexed copy is current with its upstream. A URL source whose page changed shows as stale until re-synced.

URL sources are validated against an allowlist and scheme check before fetching — you can't point the indexer at arbitrary internal hosts.

## Generating test cases

In **Test cases → More → Knowledge** (or **AI Generate → From documents**; needs a specific project selected) you generate cases from the indexed sources:

1. Pick the knowledge source(s) to ground on (the source picker).
2. Generate — the AI retrieves the most relevant chunks and drafts cases from them.
3. Each draft arrives with **citations**: the exact source passages behind it, viewable in the citation drawer. A case that can't cite its source isn't produced.

## Reviewing before they enter the catalog

Generated cases don't silently become real tests — they go through a **review panel**. This is deliberate: the AI drafts, a human approves. Two signals help you review:

- **Faithfulness** — an automatic score of whether each case is actually supported by its cited chunks (an LLM judge or the RAGAS metric, chosen under **Faithfulness evaluator** in the AI configuration). Both judge with the LLM provider configured there, and every call they make counts against the project's LLM cost cap; RAGAS makes several calls per case. RAGAS needs offline mode off and the `ragas` package, which the standard image leaves out: build the backend image with `--build-arg INSTALL_RAGAS=1` (it installs `backend/requirements-ragas.txt`) to add it. Low-faithfulness drafts are the ones to scrutinize or reject. Scoring, and the gate that holds back unfaithful drafts, run only when the `rag_faithfulness_gate` feature flag is on; it ships **off**, in which case every draft passes unscored.
- **Citations** — read the cited passages; if they don't back the assertion, don't approve it.

Approved cases join the test-case catalogue like any other, under the [authored test-case lifecycle](test-management.md#authored-test-case-lifecycle).

## Keeping generated cases honest over time

When a knowledge source changes, the cases generated from it are **flagged stale** automatically — they might now contradict the updated document. Stale cases surface for re-review (re-generate, re-approve, or dismiss the flag), so your generated tests don't quietly rot when the spec moves under them.

## When this is worth it

- **New feature with a written spec** — generate a first pass of cases from the spec, review, and you have coverage before writing a line of test code.
- **Onboarding a suite you inherited** — point it at the existing docs to bootstrap cases.
- **Not** for areas with no authoritative document — without a good source to ground on, you're back to ungrounded generation, which this feature deliberately avoids.

## Tips

- **Review faithfulness first, then citations.** The score tells you where to look; the citations tell you whether to trust it.
- **Keep sources fresh.** A stale source generates stale cases — watch the freshness indicator.
- **One project's knowledge stays in that project.** If generation can't find relevant chunks, you're likely in the wrong project or the source isn't indexed yet.
