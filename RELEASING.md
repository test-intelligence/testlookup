# Releasing TestLookup

How a maintainer cuts a release. Users upgrading an install want
[UPGRADING.md](UPGRADING.md) instead.

## Versioning

- Releases are tagged `vMAJOR.MINOR.PATCH` ([Semantic Versioning](https://semver.org/)).
  The repository-root `VERSION` file holds the same number without the `v`.
- Before 1.0, a **minor** release may contain breaking changes; each one is
  listed in UPGRADING.md and the CHANGELOG. A **patch** release is a drop-in
  replacement (bug and security fixes only, no migration that cannot be rolled
  back).
- Container images are published to `ghcr.io/test-intelligence/testlookup/{backend,frontend,mcp}`.
  Every push to `main` publishes `:<commit-sha>` and `:latest`; a release adds
  the immutable `:vX.Y.Z` alias to the exact digests CI verified — the release
  never rebuilds.
- The SDKs, CLI and MCP server are released from the same tag; they are not
  published to package registries yet (install from source, see their READMEs).

## Cutting a release

1. **Prepare on `main`.** Every change for the release is merged and CI is
   green for the commit you will tag.
2. **Update the changelog.** Move the `[Unreleased]` entries of
   [CHANGELOG.md](CHANGELOG.md) under `## [X.Y.Z] - YYYY-MM-DD`, leave an empty
   `[Unreleased]` section above it, and add the upgrade notes for the version to
   UPGRADING.md.
3. **Refresh the third-party notices**: `python scripts/generate_third_party_notices.py`
   (it reads the lockfiles, `scripts/license-allowlist.json` and
   `deploy/images.manifest.txt`; `--check` reports a stale file).
   **Bump `VERSION`** to `X.Y.Z` in the same pull request. Merge it and wait
   for CI on `main` to finish: it builds and records the image digests for that
   commit.
4. **Tag the merge commit** (an annotated, signed-off tag on a commit reachable
   from `main`):

   ```bash
   git switch main && git pull
   git tag -a vX.Y.Z -m "TestLookup vX.Y.Z"
   git push origin vX.Y.Z
   ```

5. **Watch "Promote verified release"** (`.github/workflows/release.yml`). It
   refuses a tag that is not strict semver, not on `main`, or that disagrees
   with `VERSION`; it reuses the CI run that verified that commit
   (`require-verified-sha.yml`), scans the exact image digests with Trivy,
   keeps SBOM evidence for 90 days, and tags the digests `:vX.Y.Z`. An existing
   tag pointing at different digests fails the run instead of being moved.
6. **Build the offline bundle** for air-gapped users: run the "Offline bundle"
   workflow (`.github/workflows/offline-bundle.yml`) with the release version,
   or `make offline-bundle` locally; attach the archive and its checksum file to
   the GitHub release.
7. **Publish the GitHub release** for the tag: the CHANGELOG section as the
   notes, the upgrade notes linked, the bundle attached.
8. **Announce** breaking changes in the release notes' first paragraph.

## Hotfixes

Fix on `main`, then release the next patch version from `main` as above. There
are no long-lived release branches before 1.0.

## Optional: batching work on a release branch

To keep CI cost down during a busy release window, feature pull requests can
target a `release/<version>` branch, where the lightweight
`.github/workflows/release-integration.yml` gate runs (fast repository guards
and the frontend type-check). One promotion pull request from
`release/<version>` to `main` then runs the full CI matrix once for the
assembled release. Branch protection should require the lightweight check on
`release/*` and the full CI checks on `main`; only `main` publishes images.

## Release checklist

- [ ] CI green on the commit to tag
- [ ] CHANGELOG section dated, `[Unreleased]` reset
- [ ] UPGRADING.md has the version's notes (or says "no action needed")
- [ ] `VERSION` matches the tag
- [ ] `THIRD_PARTY_NOTICES.md` regenerated (`--check` passes)
- [ ] "Promote verified release" succeeded; `:vX.Y.Z` exists for backend, frontend and mcp
- [ ] Offline bundle built and attached (with checksums)
- [ ] GitHub release published
