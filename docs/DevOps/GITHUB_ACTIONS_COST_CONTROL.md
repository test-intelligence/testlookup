# GitHub Actions cost control

Use a `release/*` branch as the integration branch for a release window:

1. Open feature pull requests against `release/<version>`.
2. Let `Release branch integration gate` run the fast repository guards and frontend type-check. Its concurrency group cancels superseded commits on the same pull request.
3. Merge approved feature pull requests into the release branch. These merges do not start the full CI matrix.
4. Open one promotion pull request from `release/<version>` to `main`. The existing `TestLookup - CI/CD` workflow runs the full backend, integration, frontend, SDK, MCP, and documentation matrix once for the assembled release.
5. Merge only after the promotion checks pass. The normal `main` push workflow then builds and deploys the exact merged commit.

This batches expensive validation at the release boundary while preserving full verification before production. Configure branch protection so release branches require the `Release - lightweight quality gate` job check emitted by this workflow, and `main` requires the existing full CI checks. Keep `main` as the only branch used for image publication and deployment.

The existing pull request concurrency setting in `.github/workflows/ci.yml` still cancels obsolete runs when a pull request receives another push. It does not cancel `main` deployment runs.

