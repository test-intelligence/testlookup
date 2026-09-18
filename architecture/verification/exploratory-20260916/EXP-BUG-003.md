# EXP-BUG-003 — migration YAML comment executed shell text

**Mission / requirement / severity:** M26, deterministic migration execution,
P3.

The candidate migration completed, but the runner printed `USER: command not
found`. The manifest uses an expanding heredoc, and backticks around
`USER testlookup` in a YAML comment caused shell command substitution while the
manifest was generated.

The comment now uses ordinary quotation marks, preserving the intended text in
the submitted manifest without invoking a command.

**Regression test:**
`backend/tests/regression/test_k8s_migration_runner.py::test_runner_uses_unique_attempt_jobs_and_readiness_init_container`
failed because the literal comment disappeared from the fake-kubectl payload.
It now requires that literal and rejects a command-not-found diagnostic.

**Mutation:** the shared M26 mutation harness restores the backticks and proves
the regression fails. All 17 mutations were killed.

**Independent review:** APPROVE. The reviewer independently passed the 30-test
focused suite, Ruff, shell syntax, `git diff --check`, and all 17 mutations.
The reviewed tracked diff hash was
`977453fc4467281c3544a7e9f351cb8a95fdf635`. Deployed E2E remains pending for
the follow-up commit.
