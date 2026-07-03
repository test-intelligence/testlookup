# Knowledge base & test generation

TestLookup can generate test cases **from your own documents** — a requirements spec, an API doc, a runbook — instead of from a blank prompt. You register the documents as **knowledge sources**, and the AI drafts test cases grounded in them, each one citing the passages it came from. Every generated case is traceable to its evidence, so you review facts, not guesses.

> This is part of the **optional AI stack** (ChromaDB + a local LLM) and is gated by `AI_OFFLINE_MODE` and a feature flag. In a default offline install, generation returns a deterministic stub rather than calling a model — enable the local-LLM profile ([GETTING_STARTED](../GETTING_STARTED.md), `make dev-llm`) to use it for real. Internals: [architecture/KNOWLEDGE_RAG.md](../architecture/KNOWLEDGE_RAG.md).

## Registering knowledge sources

A knowledge source is a document you want the generator to draw on — an upload or a URL. Manage them per project; each source is **chunked and indexed** into that project's private knowledge base (one project's documents never surface in another's generation).

Sources track their own health:

- **Sync history** — when the source was last (re)indexed.
- **Freshness** — whether the indexed copy is current with its upstream. A URL source whose page changed shows as stale until re-synced.

URL sources are validated against an allowlist and scheme check before fetching — you can't point the indexer at arbitrary internal hosts.

## Generating test cases

In **Test Management**, the knowledge-generation tab (needs a specific project selected) lets you generate cases from the indexed sources:

1. Pick the knowledge source(s) to ground on (the source picker).
2. Generate — the AI retrieves the most relevant chunks and drafts cases from them.
3. Each draft arrives with **citations**: the exact source passages behind it, viewable in the citation drawer. A case that can't cite its source isn't produced.

## Reviewing before they enter the catalog

Generated cases don't silently become real tests — they go through a **review panel**. This is deliberate: the AI drafts, a human approves. Two signals help you review:

- **Faithfulness** — an automatic score of whether each case is actually supported by its cited chunks (an LLM judge or the RAGAS metric). Low-faithfulness drafts are the ones to scrutinize or reject.
- **Citations** — read the cited passages; if they don't back the assertion, don't approve it.

Approved cases join the [test-case catalog](test-management.md) like any other.

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
