# Launch drafts (R4 GitHub adoption track)

[Maintainer guide](../README.md)

Drafts for the repository's public launch. **Nothing in this folder has been posted, created or uploaded**: each item is for the maintainer to review and publish by hand.

| Draft | Where it goes | How to publish it |
|---|---|---|
| [starter-issues/](starter-issues/README.md) | 11 issues labelled `good first issue` | Create each issue from its file (front matter = title and labels). Check the cited lines have not moved. |
| [release-notes-0.1.0.md](release-notes-0.1.0.md) | The GitHub release for tag `v0.1.0` | Follow [RELEASING.md](../../../RELEASING.md); fill in the digests and bundle checksum marked **TO FILL**. |
| [social-preview.png](social-preview.png) | Repository social preview (1280 × 640) | Settings › General › Social preview › Edit › Upload an image. |
| [org-profile-README.md](org-profile-README.md) | `profile/README.md` in a new public `test-intelligence/.github` repository | Create the repository, commit the file without its leading comment, and fill in the maintainer block. |
| [source/](source/) | Sources of `social-preview.png` and the README hero image | Open in Chromium at 1280 × 640 (card) or 1040 px wide (hero) and take a screenshot; the images come from [docs/product/screenshots](../../product/screenshots/README.md) and [docs/product/brand](../../product/brand/README.md). |

## Owner actions this pull request does not do

These need repository or organisation settings, accounts or decisions that only the owner has.

- [ ] **Enable GitHub Discussions** (Settings › General › Features). SUPPORT.md and MAINTAINERS.md already point people there.
- [ ] **Upload the social preview** (`social-preview.png`, above).
- [ ] **Create the starter issues** from `starter-issues/` and pin the repository on the organisation page.
- [ ] **Tag and publish 0.1.0**: tag the merge commit `v0.1.0` per RELEASING.md, let "Promote verified release" tag the images, then publish the release with the notes above. The Kubernetes overlays pin `v0.1.0`, so `kubectl apply -k` works only after this step (until then, pass a commit SHA with `--image-tag`).
- [ ] **Protect `main` with a ruleset**: pull request required, no force-push, no deletion (today only the DCO status check is required; the `NoDirectMerge` ruleset is disabled).
- [ ] **Require 2FA for the organisation** and add a second organisation owner as a succession safeguard.
- [ ] **Set up a monitored security mailbox**, for example `security@testlookup.app` through the existing Email Routing, and add it to SECURITY.md as a second channel.
- [ ] **Reserve the package names** on PyPI, npm and Maven Central for the SDKs and the CLI.
- [ ] **Turn on Dependabot security updates** (Settings › Code security).
- [ ] **Dismiss the four chromadb alerts on `backend/requirements-ragas.txt`** (critical and high) as "vulnerable code is not used", with the same comment as the dismissed alerts on `backend/requirements.txt`: chromadb 1.5.9 is the newest release, none is fixed, and the app uses only the client. `backend/requirements-ragas.txt` records the reasoning.
- [ ] **Confirm anonymous pulls** of `ghcr.io/test-intelligence/testlookup/{backend,frontend,mcp}` from a signed-out machine.
- [ ] **Fill in the "Built by" card** in the README (photo, name, role, links). It is a placeholder; nothing was invented.
- [ ] After the first push to `main`, check that the **OpenSSF Scorecard** workflow ran and the README badge shows a score.
