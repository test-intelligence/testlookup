---
title: List the --json-only CLI commands in the CLI README output section
labels: [good first issue, docs, cli]
---

## What

The "Output and exit codes" section of the CLI README (`cli/README.md:465-473`) says that commands which print data take `-o`/`--output`, then lists the exceptions. Nine commands take only a `--json` switch and are not in that list:

- `activity show`: `cli/testlookup_cli/commands/activity.py:120`
- `activity types`: `cli/testlookup_cli/commands/activity.py:151`
- `compliance-pack verify`: `cli/testlookup_cli/commands/compliance_pack.py:57`
- `compliance-pack list`: `cli/testlookup_cli/commands/compliance_pack.py:175`
- `compliance-pack generate`: `cli/testlookup_cli/commands/compliance_pack.py:198`
- `defects jira preview`: `cli/testlookup_cli/commands/parity.py:127`
- `defects jira create`: `cli/testlookup_cli/commands/parity.py:154`
- `investigations start`: `cli/testlookup_cli/commands/parity.py:180`
- `release gate`: `cli/testlookup_cli/commands/release.py:97`

A reader who tries `-o json` on one of these gets a usage error.

## Why it helps

The README is the CLI's reference. People write CI scripts from it, so it should match the code.

## Where to start

- Read `cli/README.md:465-473` and the command table at `cli/README.md:88-113`.
- Check each option line above: every one is `typer.Option(False, "--json")` with no `-o`.
- `compliance-pack verify --json` and `release gate --json` are already described in their own sections (`cli/README.md:324` and `cli/README.md:447`). Only the summary in the output section is missing them.

## Done when

- [ ] The output section of `cli/README.md` names these commands as taking `--json` instead of `-o`, in the same style as the existing exceptions sentence.
- [ ] No other wording in the section changes meaning.
- [ ] `python scripts/check_reference_docs.py` passes (the documented docs check, `docs/maintainers/maintenance.md:14`). It imports the backend, so install `backend/requirements.txt` first.
- [ ] Commits are signed off (`git commit -s`), per CONTRIBUTING.md

Out of scope: adding `-o` to these commands. That is a code change; open a separate issue if you want it.

## Size

About 1 hour; no Docker stack needed.
