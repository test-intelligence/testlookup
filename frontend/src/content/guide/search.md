# Search and discovery

Finding a run, a test, a defect or a release you have seen before.

## Global search

Type in the search box in the top bar and press Enter (or press `/` anywhere to jump to it). Results open on the **Search** page (`/search`), where you can refine the query. It matches test names, suites, error messages, build numbers, branches, jobs, Jira IDs and release names.

The **Entity scope** chips narrow the results to one kind: **All**, **Tests**, **Runs**, **Suites**, **Defects**, **Flaky** or **Releases**, each with its count. The query, mode and scope are in the page address, so you can share a search.

## Keyword only, for now

Global search matches **keywords**. The **Hybrid** and **Semantic** mode chips are shown but disabled: global search spans six kinds of record, and only keyword matching covers all of them. Hover a disabled chip to see why.

Semantic (meaning-based) matching is used elsewhere, where it covers the data involved: grouping similar failures into clusters, recalling earlier analyses of similar failures for the analysis pipeline and for [Ask AI](/docs/ask-ai), and [generating test cases from your documents](/docs/test-management). It needs the optional vector store; without it, those features fall back to keyword matching and say so rather than failing. The API also offers similar failures for one test result (`GET /api/v1/search/similar/<test result id>`).

## Index status

The Search page shows whether the index is ready (**Index ready**), how many items are indexed for each kind, and **Index freshness**. Each kind is marked **Live**, **Embedding** (still being indexed) or **Down**.

A background job brings the index up to date every hour.

> **Tip.** Newly ingested data may take a moment to appear in search even though it is already visible under **Runs**. The run list reads the database; search reads the index.

A QA lead can force a rebuild for one project through the API; see [Step-by-step workflows](/docs/workflows#reindex-search).

## Scope and permissions

Search is **project-scoped**. You see only what you have access to, and one project's results never appear in another's. In All Projects mode you search across the projects you can see — not across the deployment.

## Reading results

Ranking reflects how well a result matches your words, not a probability. A high-ranked result is *more like* your query than the ones below it; it is not "85% likely to be the same bug".

## Limitations

- Keyword search does not match a failure described in different words. Search for a distinctive part of the error message, the test name or the build number.
- Very short queries match a lot.
- An index that has not been rebuilt after bulk deletion may briefly return stale entries.

## Related

- [Investigating failures](/docs/failure-analysis)
- [Settings and administration](/docs/administration)
