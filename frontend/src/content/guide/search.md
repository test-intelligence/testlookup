# Search and discovery

Finding a run, a test, a defect or a release you have seen before.

## Global search

Type in the search box in the top bar and press Enter (or press `/` anywhere to jump to it). Results open on the **Search** page (`/search`), where you can refine the query. It matches test names, suites, error messages, build numbers, branches, jobs, Jira IDs and release names.

The **Entity scope** chips narrow the results to one kind: **All**, **Tests**, **Runs**, **Suites**, **Defects**, **Flaky** or **Releases**, each with its count. The query, mode and scope are in the page address, so you can share a search.

**As you type** in the top bar, after two characters a short list opens under the box with up to five matches (the kind of each is on the right). Click one to open it; the last row, **Search everything for "…"**, or **Enter** opens the Search page with your words. Escape or clicking elsewhere closes the list.

## Keyword, Hybrid and Semantic

The **Mode** chips choose how your words are matched:

- **Keyword** matches the words themselves, across all six kinds. It is the default.
- **Semantic** matches by meaning: a failure described in other words can still come up. It covers **test results only**.
- **Hybrid** combines the two rankings, also for test results only.

Choosing **Hybrid** or **Semantic** while another scope is selected switches the scope to **Tests** and says so (*Semantic and hybrid search cover test results only, so the scope is now Tests.*); choosing another scope while one of them is on switches the mode back to **Keyword** (*Keyword search covers every kind; semantic covers test results only.*). When the semantic index cannot answer here, the two chips are disabled; hover one to read why (for example *Needs the semantic index: no ChromaDB server answers here.*).

A semantic or hybrid search never leaves you with an empty page because the index failed. When it could not answer, a banner above the results says which keyword results you are looking at instead:

| Banner | What happened |
|---|---|
| *Semantic index unavailable — showing keyword results.* | The semantic index did not answer. |
| *No semantic matches — showing keyword results.* | It answered, with nothing close enough. |
| *Semantic search needs words to compare — showing keyword results for the filters.* | The query held only [filters](#filters), no words. |

The mode code in the Results header shows the mode that actually answered. A semantic or hybrid search samples the most relevant matches, so its counts are lower bounds (*at least*).

Semantic search compares the embeddings of your words with those of each test result, made by the embedding model in use (**Settings → AI → Embedding**; the bundled MiniLM model unless an administrator chose another). Semantic matching is also used elsewhere: grouping similar failures into clusters, recalling earlier analyses of similar failures for the analysis pipeline and for [Ask AI](/docs/ask-ai), and [generating test cases from your documents](/docs/test-management). Without the vector store those features fall back to keyword matching and say so rather than failing.

## Filters

Type a filter into the query as `key:value`, beside or instead of words. A value with spaces goes in double quotes: `status:failed suite:"API Regression" since:7d checkout`.

| Key | Example | Applies to |
|---|---|---|
| `status:` | `status:failed` | Tests (`passed`, `failed`, `error`, `skipped`); Runs (`passed`, `failed`) |
| `suite:` | `suite:"API Regression"` | Tests, Runs, Suites, Flaky |
| `branch:` | `branch:main` | Tests, Runs, Suites, Flaky |
| `env:` | `env:staging` | Tests, Runs, Suites, Flaky |
| `release:` | `release:"2026.11"` | Tests, Runs, Suites, Flaky, Releases |
| `owner:` | `owner:payments` | Tests, Defects |
| `since:` | `since:7d`, `since:24h`, `since:2026-10-01` | Every kind (within the last 365 days) |
| `type:` | `type:defect` | Narrows the kinds searched: `test`, `run`, `suite`, `defect`, `flaky`, `release` |

After a search, each filter that was applied shows as a chip under the query box; its **×** removes it and runs the search again. **Syntax**, at the end of the chips row, lists the keys. Keys are not case-sensitive. Repeating a key matches any of its values (`status:failed status:error`); a repeated `since:` keeps the last one. Up to 10 filters apply, each value up to 200 characters, and the whole query up to 500 characters.

Nothing is dropped silently. A line under the chips says what happened to each token that did not apply:

- an unknown key stays part of the words searched: *unknown filter foo: searched as text*;
- a known key with a value it cannot take is left out: *flaky is not a status: use type:flaky*;
- a kind that cannot apply a filter is **not searched**: *branch: is not supported for Defects, so no defects were searched.*;
- a `release:` value is matched against release names and versions in your projects only: *No release named "9.9" in your projects.*

**Semantic** and **Hybrid** apply `status:` (one value), `since:` and `type:` only. With any other filter nothing is searched, and the line says so (*suite: works in Keyword mode only, so no semantic search was run.*): switch to **Keyword** to use it.

Filters also apply to the search API and the MCP search tools: a query such as `status:failed` there filters rather than matching the words.

## Saved and recent searches

With the query box empty, the left of the page lists **Suggested** searches, your **Recent searches** and your **Saved searches**. Picking one runs it in the project you have selected now.

**Save search** (in the Results header, or under **No results** for a search that found nothing) asks for a **Name** (the query, to start with) and, with one project selected, offers **Share with** that project. Saved searches are kept on the server, so they follow you to another browser; there is no limit on how many. The first three answer **⌘1**, **⌘2** and **⌘3**. On your own saved searches you can **Rename**, **Share** or **Stop sharing** (a search saved for one project only) and **Delete**, each in the row. A search another member shared with the project can be run, not changed. A search saved in **All projects** cannot be shared.

**Recent searches** are the last 20 searches you ran on this page, in any browser, kept for 30 days. They are stored in the server's cache under your account (not in the database), and only searches in projects you can still open are listed. **Clear history** removes them all.

The first time you open the page after this release, saved searches an earlier version kept in your browser are moved to the server; the recent searches it kept in your browser are not moved.

## Suggested searches

With one project selected and the box empty, **Suggested** offers up to three groups, each a top five: **Failing in the last 7 days** (the tests with the most failures, with their count), **Open defects** (the newest with a Jira key), and **Active release** (for a release named 2026.11, *Failures in 2026.11*, which runs `release:2026.11 status:failed` on Tests). A group with nothing in it is left out.

## Similar failures

A failed or broken test result has a fourth tab, **Similar failures**, beside **History**, **Steps** and **Details**. It lists up to five failed or broken results of the same project whose failure reads most like this one, from the semantic index, each a link to that result with its suite, status, relevance and when it last ran. It asks the index only when you open the tab. When the index cannot help, the tab says so: *No similar failures in this project's semantic index.*, *Similar failures need the semantic index: no ChromaDB server answers here.* or *The semantic index answered with an error. Try again later.* A passed or skipped result has no such tab.

## Index status

The **Searchable data** card on the right lists the six kinds global search covers, each a keyword match over its own table with its row count, and a separate line for the **semantic index**, which holds test results only: **Ready** with its document count and when it was last written, **Not configured** (no vector store answers here), **Unavailable** or **Unknown**.

A background job adds new test results to the semantic index every hour.

> **Tip.** A newly ingested result may take a moment to appear in semantic search even though it is already visible under **Runs**. Keyword search reads the database; semantic search reads the index.

**Re-index now** (QA leads and administrators, with one project selected) rebuilds the project's semantic index in the background: the button reads **Queued** and the line says when the request was queued and that it updates when the rebuild finishes. The API path is in [Step-by-step workflows](/docs/workflows#reindex-search).

## Search usage

QA leads and administrators see three tiles under the search page's ribbon: **Searches today** (the UTC day), **Zero-result rate** and **p95 latency**. With no search yet today, each reads *Not measured yet: no search ran today.*, never `0 %`. A search that could not reach every source is not counted as finding nothing.

What is recorded, and what is not: one row per search you run on this page with words (not the top-bar list, not later pages of the same search, not the API, MCP or CLI). The row holds **no query text and no user**: a keyed hash of the normalised query (it counts repeats; without the server's secret key it cannot be reversed), its length and the number of words and filters, the scope, the mode asked for and the mode that answered, the result count and the time taken. Rows are kept 30 days, then deleted nightly; a project's [retention purge](/docs/administration#retention-and-purge) deletes its own as well.

## Scope and permissions

Search is **project-scoped**. You see only what you have access to, and one project's results never appear in another's. In All Projects mode you search across the projects you can see — not across the deployment.

## Reading results

Ranking reflects how well a result matches your words, not a probability. A high-ranked result is *more like* your query than the ones below it; it is not "85% likely to be the same bug".

## Limitations

- Keyword search does not match a failure described in different words. Use **Semantic** on Tests, or search for a distinctive part of the error message, the test name or the build number.
- Semantic matches from a small embedding model can look odd for short or generic queries; the ranking is relative, not a verdict.
- Very short queries match a lot.
- An index that has not been rebuilt after bulk deletion may briefly return stale entries.

## Related

- [Investigating failures](/docs/failure-analysis)
- [Settings and administration](/docs/administration)
