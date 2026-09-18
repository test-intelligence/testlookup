# M19 — Accessibility, UX and browser lifecycle

## Result

**PARTIAL — two keyboard-accessibility defects are fixed and the exact
executable was deployed before testing.** Candidate `7cf85143` was deployed as
`build-20260918-064519`. `/health/version` returned the full revision, every
application deployment was ready on the candidate tag and expected digest,
all health endpoints were green, and Alembic reported `0191 (head)`.

## What was proved

- Release creation/editing, release run linking, claim correction, and evidence
  inspection dialogs take focus, wrap Tab and Shift+Tab, close on Escape, and
  restore the invoking control.
- Icon-only release dialog close controls have accessible names.
- The shared critical-route browser checks exercise skip navigation, main
  landmark focus, modal focus containment, and serious/critical axe findings on
  Projects and Reviews.

## Verification

- Homelab authority: revision
  `7cf85143f3fea888515cfd44b05eb93bd3606b2d`; tag
  `build-20260918-064519`; backend and worker digest
  `sha256:6952e40c435d929c2720507cb486e16272d80c01610227058e3fae80c1db8859`;
  frontend digest
  `sha256:29833e965f2205878c898bd0f0502447451a65eccc6dcc5a9a922c98daf45275`;
  MCP digest
  `sha256:0dbe7c85653c85043e4ae28d35d60cb52f8bf31176acc6370c5626f88d6c7263`.
- Focused frontend suite: **51 passed**. Chromium keyboard/landmark and axe
  suite: **4 passed**.
- TypeScript passed. ESLint reported **0 errors** and 19 existing warnings.
  Backend Ruff passed; mypy held at **367/367**.
- Quality gate: all **43 guards** green. Quality-gate self-tests: **238
  passed**.
- Mutation harness: **6 unsafe changes killed** and its pytest wrapper passed.
  Every mutation selector was asserted to apply exactly once and original
  bytes were restored.
- Independent final review: **APPROVE** for executable `7cf85143`; no blocking
  finding remains.

## Defects fixed

EXP-BUG-114 and EXP-BUG-115.

## Deviations and remaining gaps

The available browser runtime covers Chromium only. Firefox, WebKit, a real
screen reader, authenticated end-to-end keyboard journeys, duplicate-tab edit
conflicts, cached-chunk replacement, RTL content, theme contrast, and the full
375px/768px/200% zoom matrix remain unproved. Automated axe checks are limited
to serious and critical findings and do not establish WCAG conformance. Other
lower-priority hand-written dialogs still need migration to the shared focus
contract. These gaps keep M19 partial.

The first candidate build exposed two test uses of `Array.prototype.at`, which
is outside the frontend target library. No test was run against that candidate;
the tests were corrected, committed, and the exact corrected candidate was
deployed and verified before the verification suites above ran.
