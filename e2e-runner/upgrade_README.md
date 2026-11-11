# Installation, upgrade and rollback operator proof

`upgrade.py` exercises **TL-DEPLOY-001** separately from the shared release runner.
It creates a fresh owned deployment, installs a previous verified main build,
seeds an independent preservation oracle, rejects a real failed Alembic command,
upgrades to the candidate, and restores the complete prior snapshot and images.
It never changes an existing target namespace or promotes a release.

The previous manifest must come from an unexpired `verified-images-<SHA>` artifact
of exactly one successful `push` to `main` in `.github/workflows/ci.yml`. The
operator rechecks GitHub metadata, the original ZIP digest and manifest bytes,
local source ancestry, and distinct component image identities. Missing baseline,
tooling or isolation is `BLOCKED`, never a synthetic pass.

Use a private new output directory and the existing Python test environment with
PyYAML, Podman/Compose, Git, and authenticated read-only `gh` access. Kubernetes
also requires `kubectl` and an explicitly selected context with permission to
create and delete a disposable namespace. Both adapters resolve runtime image
digests and check the observed revision at each stage.

Resolve the nearest eligible verified main ancestor first:

```sh
python e2e-runner/upgrade_baseline.py \
  --candidate /private/candidate/release-manifest.json \
  --repository test-intelligence/testlookup \
  --out /private/prior
```

The new output directory receives exactly the filenames `release-manifest.json`,
`verified-images.zip` and `provenance.json` needed below. The resolver enumerates
all artifact pages and ranks eligible ancestors by the fewest candidate-reachable
commits absent from the prior build, then first-parent position and source SHA.
It retains original bytes and verifies CI, expiration and digests again. Its one
stdout JSON result is `RESOLVED` or `BLOCKED`; absent provenance remains blocked.

```sh
python e2e-runner/upgrade.py --target podman \
  --previous /private/prior/release-manifest.json \
  --baseline-provenance /private/prior/provenance.json \
  --baseline-archive /private/prior/verified-images.zip \
  --candidate /private/candidate/release-manifest.json \
  --out /private/results/podman-upgrade

python e2e-runner/upgrade.py --target kubernetes --context YOUR_TEST_CONTEXT \
  --previous /private/prior/release-manifest.json \
  --baseline-provenance /private/prior/provenance.json \
  --baseline-archive /private/prior/verified-images.zip \
  --candidate /private/candidate/release-manifest.json \
  --out /private/results/kubernetes-upgrade
```

The fixture compares project and test-case IDs/counts, project membership and
published gate policy, plus Mongo and object-storage markers/checksums. S3
response metadata and a canonical ACL checksum are also preserved; raw ACL owner
IDs are not recorded. Object archives use GNU tar with filesystem attributes,
ACLs and numeric owners preserved, through a helper restricted to the owned volume.
It then
adds post-snapshot markers in all three stores. Rollback must remove those markers
while preserving the original oracle, which rejects a restore that merely merges
old data into the candidate's stores. All app writers stop before snapshot or
restore. Checksummed PostgreSQL, MongoDB and complete object-volume archives stay
inside the disposable infrastructure containers. Redis is flushed on restore;
rebuildable vector/model stores are outside this case. No schema downgrade is
attempted. This follows the documented quiesce/backup/restore procedure rather
than assuming every migration has a safe downgrade.

`upgrade-results.json` records private per-stage evidence, actual revisions,
provenance and cleanup. `upgrade-summary.md` is a private compact index.
`upgrade-proof.json` is a separate public allowlist: candidate/previous manifest
hashes, revisions, component digests, stage outcomes and cleanup status. It excludes
namespace details, fixture record IDs, credentials and host paths. `PASSED` applies to this case
only; the parent runner still decides overall release readiness. The invalid
revision control fails before schema writes and proves application replacement
is withheld; it does not simulate every possible migration failure.

Cleanup always removes only the adapter's generated Podman project or its
UID-checked Kubernetes upgrade namespace, including its data and credentials.
Keep diagnostics private. Existing suite namespaces, live users and live services
are outside the mutation scope.

Run the fault-focused harness checks with:

```sh
python -m pytest e2e-runner/upgrade_test.py -q
```
