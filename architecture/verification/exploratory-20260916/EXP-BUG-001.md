# EXP-BUG-001 — homelab images did not identify their source commit

**Mission / requirement / severity:** M26, exact candidate deployment
provenance, P1.

The stable homelab `/health/version` response reported both `revision` and
`built_at` as `unknown`. The backend Dockerfile supports those build arguments,
but `homelabsetup/deploy-homelab.sh` did not set or pass them. Consequently an
operator could compare an image tag and registry digest, but could not prove
from the serving application that the requested commit was deployed. The same
script would also accept tracked uncommitted source while labelling the image
only with a generated time tag.

The deploy build path now refuses tracked unstaged or staged changes, derives
the exact `HEAD` and UTC build time once, rejects untracked files under every
application build input, and passes both values to the backend production
build. Untracked local evidence outside those inputs remains allowed.

**Regression test:**
`backend/tests/test_health_build_provenance.py::test_homelab_backend_image_receives_exact_build_provenance`.
Before the implementation it failed at the missing `BUILD_REVISION` assignment.
After the implementation all seven provenance tests and the 29 focused
deploy/provenance regression tests passed.

**Mutation:** `scripts/mutation_check_exploratory_m26_provenance.py` asserts
each of seventeen mutations applies exactly once. It kills unknown revision/date,
bypassed unstaged/staged/untracked cleanliness guards, unknown revision/date
build arguments, inverted pod and serving-revision comparisons, a wrong backend
authority map, mixed pod sets that try to skip invalid siblings, leaked
generated CA staging, and an invocation that checks for `unknown` instead of
the candidate. The expanded run also kills removal of Deployment component
scoping, acceptance of a missing component label, and reintroduction of shell
command substitution in migration YAML comments.

**Deployed E2E:** pending. After review and commit, deploy that exact commit and
assert `/health/version.build.revision` equals the commit SHA and `built_at` is
the build timestamp. Then verify schema `0189` and all application pod tags and
image IDs before restarting functional missions.

**Independent review:** APPROVE at 2026-09-16T14:08:01Z after three finding
rounds. The final reviewed tracked diff hash was
`d0b7619ffa27dcf1135a6512f1c6095108c0410b`. Independent checks passed 23
focused tests, Git Bash syntax, and `git diff --check`; the stable coordinator
mutation run killed all 14 mutations. No correctness, provenance, shell
portability, secret-exposure, or test-strength findings remained.
