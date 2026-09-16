# End-to-End Test Suite

## Automated lanes

The blocking hermetic lane runs `npm run test:e2e:ci`. It starts Vite, launches
Chromium, controls the API boundary, and needs no VPN, credentials, database, or
backend. The live lane runs `npm run test:e2e` against a configured deployment.

| ID | Journey | Preconditions | Steps and assertions | Negative / boundary |
|---|---|---|---|---|
| E2E-CI-01 | Protected deep link | Empty browser storage | Open `/runs`; assert redirect and sign-in heading | No protected shell may render |
| E2E-CI-02 | Sign in and resume | Mock token/user; initial `/reviews` | Submit password form; assert form encoding via service test, return to `/reviews`, shell visible | Optional shell endpoints return empty values |
| E2E-CI-03 | Rejected persisted token | Cached user and expired token; `/auth/me` returns 401 | Open `/runs`; assert local auth is revoked and browser reaches `/login` | Distinguishes explicit 401 from transient network failure |
| E2E-LIVE-01 | Ingest failed run | Admin token, project, JUnit fixture | Upload; wait for persistence; assert counts/status and test rows | malformed XML, duplicate idempotency key, 50k boundary |
| E2E-LIVE-02 | Run intelligence | Failed run with baseline | Open intelligence; assert diff, clusters, evidence, owner and next action | missing baseline; AI unavailable uses deterministic fallback |
| E2E-LIVE-03 | Cluster to defect | Eligible cluster and integration policy | Promote; assert local/Jira result, activity and dedupe link | Jira disabled/error creates safe local draft without duplicate |
| E2E-LIVE-04 | Release decision | Release with mixed runs and gate policy | Compute decision; inspect evidence; override as allowed; assert audit | viewer cannot override; stale evidence is visible |
| E2E-LIVE-05 | Search | Indexed project corpus | Run semantic, keyword, hybrid queries; open result | Chroma unavailable falls back; other-project result absent |
| E2E-LIVE-06 | Review gate | Pending AI report, producer and distinct QA lead | Verify gated output; accept; verify public state and notification/export | producer cannot self-review; reject prevents retry and releases no narrative |
| E2E-LIVE-07 | Agent pipeline recovery | Worker, Redis, PostgreSQL | Start run; interrupt worker; reap/resume; assert one terminal outcome | stale worker cannot finalize; deadline/DLQ alerts fire |
| E2E-LIVE-08 | Workflow lifecycle | QA lead and replay corpus | Fork built-in; edit; validate; evaluate; publish; invoke exact version | built-in immutable; invalid DAG refused; regression needs reason |
| E2E-LIVE-09 | Role matrix | Viewer, tester, engineer, lead, admin | Walk navigation and direct URLs; execute representative reads/writes | hidden controls plus server-side 403; API key cannot review |
| E2E-LIVE-10 | Project isolation | Two projects with distinct data | Switch project and query routes; inspect lists/detail/search/export | guessed IDs and stale local selection reveal no foreign data |
| E2E-LIVE-11 | MFA/SSO | Optional/required policies | Password, challenge, forced enrollment, recovery and SSO redirect | replayed code, unsafe redirect, lockout and disabled account |
| E2E-LIVE-12 | Test lifecycle | Cases with history, steps and flaky evidence | Inspect, quarantine/release, compare history, assign owner | insufficient evidence and already-resolved states |
| E2E-LIVE-13 | Notifications | Configured channel and event | Configure, test, trigger, inspect delivery/history/read state | unreviewed narrative is replaced; provider retry is idempotent |
| E2E-LIVE-14 | Retention/deletion | Project with eligible/ineligible records | Preview; execute; poll job; verify audit and remaining protected data | wrong confirmation, concurrent job, legal/protected data |
| E2E-LIVE-15 | API keys | Admin and scoped project | Create once, use, rotate/revoke, verify activity | secret never reappears; revoked/cross-project use fails |
| E2E-LIVE-16 | Offline ceiling | Offline deployment with local models | Invoke all eligible capabilities; inspect provider metadata | no hosted provider call; unsupported capability fails closed |
| E2E-LIVE-17 | All-green fast path | Passing run | Ingest; assert terminal status and no wasteful AI stages | zero-test and skipped-only runs retain honest semantics |

## Execution policy

- E2E-CI runs on every pull request and push.
- Live security, review, ingestion, and release journeys block a release.
- Browser traces and server correlation IDs are retained for failures.
- Test data names include a run ID and are removed through supported APIs.
- Retries reveal environmental flake; the first failure remains evidence and a
  test is never declared stable because a retry passed.
