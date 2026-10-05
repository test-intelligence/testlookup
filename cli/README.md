# testlookup-cli

**TestLookup CLI — QA intelligence from your terminal.**

Upload test results, query failures, and gate a build on the release verdict
without leaving the shell. Talks to a TestLookup server over its REST API; it
holds no state of its own beyond a saved connection profile.

## Install

```bash
pip install ./cli          # from a checkout
testlookup --help
```

## Connect

```bash
testlookup auth login      # stores a profile (URL + API key)
testlookup doctor          # one-shot verdict: profile + reachability + auth
testlookup health          # confirm the server is reachable
```

`doctor` is the fastest way to debug a fresh install: it checks that a server
URL is resolved, that the server is reachable (and reports its build), and that
your credentials are accepted — exiting non-zero only on a hard failure, so it
works as a CI preflight too.

Configuration resolves in this order — later wins:

1. saved profile in `profiles.json` (platform config dir)
2. `TESTLOOKUP_URL` / `TESTLOOKUP_API_KEY` environment variables
3. explicit command-line options

`TESTLOOKUP_PROFILE` selects which saved profile to use.

## Common tasks

```bash
# Upload results after a test run
testlookup upload file results.xml --project my-project --build "$CI_PIPELINE_IID" --format auto

# Fail a CI job on the release verdict (exit 0 = GO, 1 = NO_GO, 2 = error)
testlookup ci-verdict --run <run-id> --project my-project

# Look around
testlookup runs list --project my-project
testlookup tests failing --project my-project
testlookup search "connection reset"

# Human review of AI reports (accept/reject need `auth login`; API keys are refused)
testlookup reviews list <project-id>
testlookup reviews accept <review-id> --notes "checked against the logs"
testlookup reviews reject <review-id> --reason unsupported_claim

# Chart data: the same series the UI's charts draw (release- and suite-scoped)
testlookup analytics trends --project <project-id> --release <release-id> --suite checkout --days 90
testlookup analytics trends --project <project-id> --by suite --format csv > pass-rate-by-suite.csv
testlookup analytics chart --metric duration_p95 --group-by week --group-by environment -o json
```

`analytics trends` is `--metric pass_rate` per UTC day; `--by <dimension>` adds one
series per value (the 7 largest plus "other", unless `--top-n` says otherwise).
`analytics chart` takes any metric over one or two `--group-by` dimensions.
`--release` and `--suite` repeat (OR within each, AND across); a release is a
UUID or `unattributed`. Both read `GET /api/v1/analytics/chart-data`, the endpoint
MCP's `get_chart_data` reads too. `--format csv` writes the UI export's layout
(scope as `# Label,value` lines, a gap as an empty cell, never 0) as UTF-8 with
`\n` line ends and no BOM; `table` draws a sparkline per series. The UI's
Trends page draws its catalogue charts from the same endpoint, but its
pass-rate trend card reads `/metrics/trends`, which counts every run that
touched a suite whole, so under `--suite` that card and these commands
differ. The server allows 120 chart-data requests a minute per user.

Every command supports `--output json` for piping. JSON mode keeps stdout
clean — diagnostics go to stderr — so `| jq` works without filtering.

Commands that show AI-generated reports (`intelligence show`) print the report's
review state and the AI disclaimer to stderr. `unknown` means the server sent
no review status: treat the report as an unreviewed draft.

## Command groups

`auth` · `doctor` · `health` · `projects` · `runs` · `tests` · `search` ·
`intelligence` · `deep` · `reports` · `reviews` · `analytics` · `keys` · `upload`, plus the top-level
`ci-verdict`.

Run `testlookup <group> --help` for details on any of them.

## CI context and commit ranges

In CI the CLI auto-detects provider metadata (branch, build number, PR/MR,
commit SHA) for GitHub Actions, GitLab, Jenkins, Azure Pipelines and CircleCI,
and collects the commit range from local git so failures can be attributed to
the changes that plausibly caused them. Both are best-effort and never fail a
run; disable with `--no-commit-range` or `TESTLOOKUP_COMMIT_RANGE=0`.

See `user-guide/` in the repository for the full CI recipes.

## Tests

```bash
python -m pytest cli/tests    # from the repository root
```
