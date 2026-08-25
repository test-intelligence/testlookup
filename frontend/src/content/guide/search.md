# Search and discovery

Finding a run, a test, or a failure you have seen before.

## What you can search

- **Global search** across entities you have access to.
- **Similar failures** from a specific failure — "has this happened before?"
- **Filters** by entity type, project and date.

## Keyword and semantic search

Two mechanisms:

- **Keyword** — always available. Matches text.
- **Semantic** — matches on meaning rather than wording, so a failure phrased differently can still match. Requires a configured vector store.

> **Note.** When semantic search is not configured or unavailable, search **falls back to keyword matching**. Results are still returned; they will be literal rather than conceptual. If similar-failure results look shallow, this is the first thing to check with an administrator.

## Indexing

Searchable content is indexed as it arrives. An index can be rebuilt when it drifts or after bulk changes — an administrator action, not an instant one.

> **Tip.** Newly ingested data may take a moment to appear in search even though it is already visible under Runs. The run listing reads the database; search reads the index.

## Scope and permissions

Search is **project-scoped**. You see only what you have access to, and one project's results never appear in another's. In All Projects mode you search across the projects you can see — not across the deployment.

## Reading results

Ranking reflects relevance, not probability. A high-ranked result is *more like* your query than the ones below it; it is not "85% likely to be the same bug". Similar-failure results are candidates to compare, and the comparison is yours to make.

## Limitations

- Semantic quality depends on the embedding configuration.
- Very short error messages carry little signal either way.
- An index that has not been rebuilt after bulk deletion may briefly return stale entries.

## Related

- [Investigating failures](/docs/failure-analysis)
- [Administration](/docs/administration)
