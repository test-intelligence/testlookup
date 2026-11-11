---
title: Accept --output on the five CLI commands that only take --format
labels: [good first issue, cli]
---

## What

The CLI README says "Commands that print data take `-o`/`--output`" (`cli/README.md:467`). Five commands only accept `--format` and `-o`, so `--output json` fails on them with "No such option":

- `activity list`: `cli/testlookup_cli/commands/activity.py:69`
- `quarantine list`: `cli/testlookup_cli/commands/parity.py:47`
- `investigations list`: `cli/testlookup_cli/commands/parity.py:195`
- `investigations show`: `cli/testlookup_cli/commands/parity.py:218`
- `release list`: `cli/testlookup_cli/commands/release.py:124`

`cases list` already accepts all three spellings: `typer.Option("table", "--format", "--output", "-o", ...)` at `cli/testlookup_cli/commands/cases.py:198`.

## Why it helps

Scripts can use one flag for every command. That is what the README already tells people.

## Where to start

- The five option lines above. Each one needs `"--output"` added, the same way as `cli/testlookup_cli/commands/cases.py:198`.
- No name clash: `activity export` uses `--output` for a file path (`cli/testlookup_cli/commands/activity.py:173`), but it is a different command.
- Test to copy: `cli/tests/test_cases_commands.py:138` (`test_an_unknown_format_exits_2_and_output_is_an_alias`) checks the alias. `cli/tests/test_release_commands.py:120` and `:126` show how `release list` and `quarantine list` are called against a fake API.
- Run the CLI tests from the repository root: `pip install ./cli`, then `python -m pytest cli/tests` (`cli/README.md:556`).

## Done when

- [ ] `--output json` works on all five commands and prints the same JSON as `-o json`.
- [ ] A test calls each of the five commands with `--output json`, checks exit code 0, and parses the output as JSON. The fixtures in `cli/tests/test_release_commands.py` and `cli/tests/test_activity_commands.py` already fake the routes these commands call.
- [ ] `--format` and `-o` still work (the existing tests stay green).
- [ ] A line under `[Unreleased]` in `CHANGELOG.md`.
- [ ] Commits are signed off (`git commit -s`), per CONTRIBUTING.md

Out of scope: these five commands do not reject an unknown format the way `cases` does (`cli/testlookup_cli/commands/cases.py:43`); an unknown value falls through to JSON (`cli/testlookup_cli/output.py:56-57`). That can be a separate issue.

## Size

About 1–2 hours; no Docker stack needed. Python 3.11 only.
