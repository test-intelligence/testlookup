# M20 — Keys, MFA/SSO/SCIM and permission changes

## Result

**PARTIAL — two API-key UI defects are fixed and the exact executable was
deployed before testing.** Candidate `eed44908` was deployed as
`build-20260918-080343`. `/health/version` returned the full revision, every
application deployment was ready on the candidate tag and expected digest,
all health endpoints were green, and Alembic reported `0191 (head)`.

## What was proved

- API-key listing outages are visibly distinct from a valid empty list and
  expose an explicit retry.
- A one-time raw key, generated setup snippets, and an open generation form are
  removed when project, user, session, or role authority changes. A late create
  response cannot restore them, and the original project label remains bound
  to the secret until removal.
- The one-time key dialog takes and contains focus, closes on Escape, and
  restores the invoking control.
- Existing focused identity suites cover project-bound key confinement, review
  mutation refusal, key mint/revoke races, MFA enrollment/challenge/recovery,
  SAML validation/replay controls, SCIM scope/patch/deprovision behavior, role
  and membership authority, session refresh, and audit events.

## Verification

- Homelab authority: revision
  `eed44908969f614499ad36053ab408ce6b8f67d6`; tag
  `build-20260918-080343`; backend and worker digest
  `sha256:215b8c3a9a61a26e2e8359619cbf5ad678140b8408a96d143a3d9c58ccb575ee`;
  frontend digest
  `sha256:b2f120016b413fbf88455f749241b8efbb468a072a03b3ea4a0da7cf7ba05052`;
  MCP digest
  `sha256:0dbe7c85653c85043e4ae28d35d60cb52f8bf31176acc6370c5626f88d6c7263`.
- Focused frontend identity suite: **132 passed**. Backend key, MFA, SSO, SCIM,
  session, membership, and role suite: **425 passed, 78 skipped**; the skips
  are integration tests whose optional Postgres harness was unavailable.
- TypeScript and changed-file ESLint passed. Backend Ruff passed; mypy held at
  **367/367**.
- Quality gate: all **43 guards** green. Quality-gate self-tests: **238
  passed**.
- Mutation harness: **5 unsafe changes killed** and its pytest wrapper passed.
  Every mutation selector was asserted to apply exactly once and original
  bytes were restored.
- Independent final review: **APPROVE** for executable `eed44908`; no blocking
  finding remains.
- No synthetic users, keys, or provider records were created, so cleanup was
  not required.

## Defects fixed

EXP-BUG-116 and EXP-BUG-117.

## Deviations and remaining gaps

The shared homelab has no dedicated IdP/test tenant, disposable identity
accounts, or notification sink. Live MFA enrollment/recovery, SAML callback,
SCIM provisioning/deprovision, invitation delivery, concurrent key revoke/use,
and old-tab permission-change exercises are therefore blocked. Safe local tests
exercise their contracts without contacting real recipients. These gaps keep
M20 partial.

The first exact candidate exposed a React lint violation and a regression-test
filename caught by the repository's secret-file ignore rules. The state reset
was changed to an authority-keyed remount and the test was renamed. Independent
review then required explicit role-loss and settled late-response proofs; those
were added before the exact corrected candidate `eed44908` was deployed and
verified for all results recorded above.
