# M03 upload, parse, deduplicate, and recover

**Final validation candidate:**
`617eb41e2820d26f4505bc6077d6f54fabd996be`, tag
`build-20260916-202123`, schema `0189`. Before the maintained test ran,
all nine application deployments were fully updated and available on this tag.
The backend and workers served digest
`sha256:0ab489aa3061df4a2dcf811e0811a42fe9df37ac6056ccfe3cd57b108f814e66`,
the frontend served
`sha256:f79f0d3f0b1860cd2b48d2d45d7880cc364796b7d1058ffe871f90c5d964668e`,
and MCP served
`sha256:b01b5c380ab0039a077223ddfcd5c8469adbf0fa5a77aa8245024f13bbd89070`.
`/health/version` reported the exact revision, readiness and details were
healthy, and Alembic reported `0189 (head)`.

The live format matrix uploaded TestNG, JUnit, Robot, NUnit, TRX, xUnit,
pytest, Cypress, Playwright, Cucumber, and Allure reports. Every advertised
format produced its expected durable test count. Unicode names and an
all-skipped report remained intact. Duplicate identifiers collapsed to one
result with the worst status, while a deliberate second submission produced a
separate run. A simulated client disconnect reconciled the accepted task and
run without resubmission.

Boundary and refusal probes covered empty, unsupported, malformed, and
wrong-format files. Empty and unsupported files returned 400; malformed input
ended in a parse error without a run; wrong-format input ended as an empty
report without a run. A valid report of exactly 50 MiB succeeded with one
test, while a 50 MiB plus one-byte request returned 413. All synthetic projects
were removed.

Archive probes covered a two-result Allure zip and a mixed JUnit plus pytest
bundle with four results. An empty zip became an empty report, an unsafe path
was refused as `unsafe_path`, and a nested zip was refused as `nested_zip`.
The archive probe removed its synthetic project.

The new maintained browser journey uploaded a 5 MiB JUnit report through the
real UI, captured the 202 receipt, immediately reloaded the Runs page, polled
that same task to success, verified its durable run contained one result, and
rendered the parsed sentinel. Chromium, Firefox, and WebKit all passed, and
each synthetic project was removed. TypeScript and ESLint for the new journey
were clean.

Supporting maintained tests passed: 249 focused backend parser, upload,
archive, format-detection, idempotency, security, and ingestion API tests, plus
18 focused frontend upload and deep-link tests. No M03 product defect was
found, so no defect-specific regression or mutation artifact was required.

The deploy command's immediate authority pass sampled a terminating prior
critical-worker pod and exited nonzero after Kubernetes reported every rollout
complete. A subsequent fail-closed inventory showed no terminating app pod,
all desired/updated/ready/available counts equal, and every serving pod on the
candidate tag and expected digest before testing began.

**Result:** PASSED.
