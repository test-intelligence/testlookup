# Working with runs (moved)

This page moved into the in-app user guide, which is the single source for using TestLookup. Open **? → Documentation** in the app and pick the topic, or read the source in this repository:

| Topic | In the app | Source |
|---|---|---|
| The run list, a run's tabs (Tests, Analysis, Changes, Evidence, Timing), the run's AI history and "vs previous run", one test result, comparing runs (with both runs' AI analyses), configuration drift, likely affected tests, narrowing a failure to its last pass, bisect from last green, a red-build routine | `/docs/runs` | [runs.md](../frontend/src/content/guide/runs.md) |
| Watching a run while it streams | `/docs/live` | [live.md](../frontend/src/content/guide/live.md) |

For wiring CI so runs arrive in the first place, see [Connecting CI and test frameworks](getting-results-in.md).

Three features of the run page are summarised here for operators, because they depend on what CI sends and on how the instance is configured.

### Configuration drift

A run's **Changes** tab shows a **Configuration drift** card: what differs between this run and the baseline build the tab compares with (`Configuration drift: poetry.lock changed · python 3.11.8 → 3.11.9`). It compares, in this order:

1. **Dependencies**: the lockfile hashes of the run context (`poetry.lock changed`, `go.sum added`, `yarn.lock removed`).
2. **Runtime**: interpreter and tool versions (`python 3.11.8 → 3.11.9`).
3. **Images**: container image digests.
4. **Environment**: the run's `environment` label (`environment staging → prod`).
5. **Env**: the allowlisted environment variables (`TZ UTC → Europe/Berlin`).
6. **Run**: the branch and the OpenShift namespace (`branch main → feature/pay`).

The first three and the allowlisted variables come from the **run context** that the Python SDK (1.2.0) and the CLI (0.2.0) send with a run (see [Run context](getting-results-in.md#run-context)). The environment label, the branch and the namespace are compared for every run.

**Absence is not health.** When this run, the baseline, or both sent no run context (a dashboard upload, an older SDK, the JavaScript, Java or Go SDKs), the card says **Not recorded by this reporter** and lists only the drift it can see (the environment label, the branch, the namespace). It says **No configuration drift against build #N.** only when both runs recorded a context and nothing differs. At most 60 differences are listed; the card says how many there are in all.

The baseline is the tab's own, the build the tab names: the newest earlier run of the project whose pass rate reached 85 % of the release threshold (`RELEASE_PASS_RATE_THRESHOLD`). `GET /api/v1/runs/{run_id}/regression-diff` carries the comparison as `config_drift`.

### Narrowing a failure to its last pass

For one failing test, TestLookup walks back through the test's own history and names **the last build in which it passed** and **the first build in which it failed**: `Last passed in build #102 (a1b2c3d), first failed in build #105 (e4f5a6b).` It is shown in a failing test's **Suspects** on **Failures**, and on a run's **Changes** tab (**Narrow** beside each new failure; **Bisect from last green** on **Runs** opens the run's first new failure there). It is observe-only: no job runs, nothing is re-run, and nothing is written.

How the walk works:

- **The series** is the failing run's branch (case-insensitive), or the project's runs with no branch. Runs are taken **in the order TestLookup received them**, newest first, up to **200 runs back**. A file uploaded late with an older execution date sorts by when it arrived.
- Each earlier failure moves the first failure back; the first **pass** ends the walk. A run of the branch that did not run this test (the test is absent, skipped or unknown there) is **untested**: those between the pass and the first failure are listed (`2 run(s) in between did not run this test.`).
- **Main fallback**: when the branch never passed in the runs read, the newest pass on `main` or `master` older than the first failure is used, and the answer says so (`Never passed on feature/pay in the runs read; last passed on main in build #98 ...`). A run on `main` or `master` has no fallback.
- No pass at all: `This test never passed on <branch>` when the walk read the whole series, or `No pass in the last 200 runs of <branch>; older history was not searched.` when it stopped at the bound.
- When both builds ran the same commit, no code change explains the failure (a flaky test, data or the environment), and the answer says so.

**The commits in between.** Online, TestLookup asks the project's git host for the commits between the two builds (GitHub `good...bad`, GitLab's compare), through the **same integration, gates and network safeguards** as the Changes tab's commit list, and ranks them as suspects for that test. The answer is cached for an hour per pair of commits, so reopening it costs no call; a refused compare is remembered for a minute; a compare that takes more than 8 seconds says so and offers **Retry** (it is never cached as a failure). If the instance's Redis cache is unavailable, no compare is made at all (the two builds are still exact).

**Offline** (`AI_OFFLINE_MODE=true`), or without a GitHub / GitLab integration for the project, the two builds, their commits, links to both runs and the untested count are still shown, with `Commit list unavailable: offline mode (AI_OFFLINE_MODE=true).` (or the integration's reason) instead of the commits.

**Merges and rebases.** The host's three-dot compare lists the commits reachable from the failing build and not from the passing one. With merge commits or a rebase in between, that is not a straight line of history, and the answer says so beside the list.

`GET /api/v1/runs/{run_id}/bisect?fingerprint=<test fingerprint>` returns the answer for a test of that run (any role that can open the run; a fingerprint that is not a test of that run is `404`).

### Likely affected tests

The Changes tab's **Likely affected tests** card ranks the run's own tests by how likely the run's changes touched them. It is **advisory only**: nothing reads it to choose, skip, quarantine or gate a test, there is no SDK or CLI skip list, and every answer carries the caveat `Advisory only: ranked by path overlap and past co-failures. It never selects or skips tests.` It is always on (no flag).

**What it needs.** The run's stored commit range with changed files per commit: a range your CI supplies with the run, or one the GitHub / GitLab integration resolved. GitHub returns the files of the oldest `COMMIT_RANGE_FILE_FETCH_LIMIT` (25) commits of a range only, and GitLab puts all the files on the head commit. Without a range the answer is `available: false` with `no_commit_range`; with a range whose commits list no files, `no_changed_files`. The card never resolves a range itself (a read never writes).

**How it ranks.** Each test's best path overlap with the changed files (the same matcher as the **Suspects** ranking: the stack trace's file, class and package tokens, the test name) scores it. Words that appear in most of the run's test names, such as your organisation's package name, don't count as a match (a module token shared by half or more of the run's tests is ignored). When the project's commit history is ready (the TIA readiness rule: at least 30 runs with a usable range, spanning 14 days, with 25 distinct changed paths, in the last 90 days), the score is `0.6 × overlap + 0.4 × min(1, co-failure runs / 5)`, where a co-failure run is an earlier run of the **same project** in that window whose range changed at least one of these exact paths and in which the test was `FAILED` or `BROKEN`. A known-flaky test (the attribution engine's flaky rule) keeps its overlap, but its co-failures earn no score; the count is still shown. With the history ready, `co_failure_runs` and `history_runs_considered` are counts even when no earlier run touched these paths (`0`, measured; the card reads `no earlier run changed these files`). Before the history is ready the score is the overlap alone, both are `null` (not measured, never `0`) and the card names the readiness reason. `changed_files_count` is the true de-duplicated count; only the first 2,000 paths are scored, and the history scan is capped like the readiness scan (`scan_capped`).

`GET /api/v1/runs/{run_id}/likely-affected-tests?limit=20` (any role that can open the run; anyone else gets `404` "Test run not found"; `limit` up to 50) returns `available`, `reason`, `advisory`, `caveat`, `changed_files_count`, `file_detail_coverage`, `source`, `base_source`, `base_anchor_is_strong`, `history` (`available`, `insufficient_data_reason`, `usable_runs`, `window_days`, `runs_considered`, `scan_capped`), `tests` (each with `score` and a `reason`: `overlapping_paths`, `overlap_score`, `co_failure_runs`, `history_runs_considered`, `known_flaky`) and `total_candidates`. The pull-request comment does not carry it yet: the comment is posted before a connector range resolves.
