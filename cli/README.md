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
```

Every command supports `--output json` for piping. JSON mode keeps stdout
clean — diagnostics go to stderr — so `| jq` works without filtering.

## Command groups

`auth` · `doctor` · `health` · `projects` · `runs` · `tests` · `search` ·
`intelligence` · `deep` · `reports` · `keys` · `upload`, plus the top-level
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
