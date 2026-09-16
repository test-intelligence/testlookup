# EXP-BUG-002 — image authority check included retained migration pods

**Mission / requirement / severity:** M26, exact candidate deployment
authority, P1.

The first candidate rollout placed every application Deployment on the exact
new tag and registry digest, but the post-rollout authority check failed for the
backend. Its `app=testlookup-backend` selector also matched retained migration
Job pods from older releases, so the verifier compared unrelated historical
images as though they belonged to the current Deployment.

The verifier now reads the component label from the Deployment template and
uses the combined app and component selector. It fails closed when that label
is absent, and still requires every selected pod to be ready on the exact tag
and digest.

**Regression test:**
`backend/tests/regression/test_homelab_build_authority.py::test_deployment_component_excludes_retained_migration_job_pods`
failed against the app-only selector and passes with Deployment-derived scope.
The missing-component regression also proves the verifier fails closed.

**Mutation:** the shared M26 mutation harness kills removal of component
scoping and acceptance of a missing component label. All 17 mutations were
killed.

**Independent review:** APPROVE. The reviewer independently passed the 30-test
focused suite, Ruff, shell syntax, `git diff --check`, and all 17 mutations.
The reviewed tracked diff hash was
`977453fc4467281c3544a7e9f351cb8a95fdf635`. Deployed E2E remains pending for
the follow-up commit.
