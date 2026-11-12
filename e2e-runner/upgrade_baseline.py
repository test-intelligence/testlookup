"""Resolve an original, verified previous-build artifact for upgrade proofs.

Candidates are ranked by ``git rev-list --count PRIOR..CANDIDATE``: the number
of candidate-reachable commits absent from the prior build. Lower is nearer.
Candidate first-parent position breaks ties, followed by the full source SHA.
Only local ancestors with exactly one successful push-to-main ci.yml run and
an unexpired verified-images artifact are eligible. Within that run, the newest
artifact ID is used. Tags and synthesized manifests are never used.
Ranking comes from the immutable local parent graph. Discovery queries only
the exact verified-images-<SHA> name for each ancestor in that order, so unrelated
CI artifact uploads cannot change the population being paginated.

The output directory must be new. It receives release-manifest.json (original
bytes), verified-images.zip (original download), and provenance.json. Stdout is
one public JSON result without local paths, credentials, or command diagnostics.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, urlencode, urlsplit
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/release"))
from release_manifest import validate_manifest  # noqa: E402

MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
_NAME = re.compile(r"verified-images-([0-9a-f]{40})")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class BaselineBlocked(RuntimeError):
    """A static public failure reason, never raw tool output."""

    def __init__(self, code, reason):
        super().__init__(reason)
        self.code = code


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _environment():
    result = dict(os.environ)
    result.pop("GH_DEBUG", None)
    result.update(GH_PROMPT_DISABLED="1", GIT_TERMINAL_PROMPT="0", NO_COLOR="1")
    return result


def _git(*arguments):
    try:
        completed = subprocess.run(["git", *arguments], cwd=ROOT, env=_environment(),
                                   capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        raise BaselineBlocked("historyUnavailable", "Local Git ancestry could not be inspected.") from None
    if completed.returncode:
        raise BaselineBlocked("historyUnavailable", "Local Git ancestry could not be inspected.")
    return completed.stdout.decode("ascii").strip()


class GitHubAPI:
    """Read GitHub through the existing gh authentication; suppress diagnostics."""

    def _request(self, endpoint, *, paginate=False):
        command = ["gh", "api", "--method", "GET", "--hostname", "github.com", endpoint]
        if paginate:
            command.extend(["--paginate", "--slurp"])
        try:
            result = subprocess.run(command, cwd=ROOT, env=_environment(),
                                    capture_output=True, timeout=180)
        except (OSError, subprocess.SubprocessError):
            raise BaselineBlocked("githubUnavailable", "GitHub artifact verification is unavailable.") from None
        if result.returncode:
            raise BaselineBlocked("githubUnavailable", "GitHub artifact verification failed; authentication diagnostics were suppressed.")
        return result.stdout

    def json(self, endpoint, *, paginate=False):
        try:
            return json.loads(self._request(endpoint, paginate=paginate))
        except (ValueError, UnicodeError):
            raise BaselineBlocked("invalidMetadata", "GitHub returned invalid artifact metadata.") from None

    def artifacts(self, repository, name=None):
        if not isinstance(name, str) or _NAME.fullmatch(name) is None:
            raise BaselineBlocked("incompleteArtifacts", "Artifact pagination was incomplete; an exact verified-image artifact name is required.")
        endpoint = f"repos/{repository}/actions/artifacts?{urlencode({'per_page': 100, 'name': name})}"
        # An upload for this exact SHA can race its own listing. Retry the
        # complete exact-name population a bounded number of times; never
        # accept a partial or deduplicated view as proof of run uniqueness.
        for _ in range(3):
            pages = self.json(endpoint, paginate=True)
            if not isinstance(pages, list) or not pages:
                raise BaselineBlocked("incompleteArtifacts", "The complete exact-name artifact listing could not be verified.")
            items, totals = [], set()
            for page in pages:
                if (not isinstance(page, dict) or not isinstance(page.get("artifacts"), list)
                        or type(page.get("total_count")) is not int or page["total_count"] < 0):
                    raise BaselineBlocked("invalidMetadata", "GitHub returned invalid exact-name artifact metadata.")
                totals.add(page["total_count"])
                items.extend(page["artifacts"])
            if any(not isinstance(item, dict) or type(item.get("id")) is not int or item.get("name") != name for item in items):
                raise BaselineBlocked("invalidMetadata", "GitHub returned an invalid or unrelated exact-name artifact.")
            identifiers = [item["id"] for item in items]
            if len(totals) == 1 and next(iter(totals)) == len(items) and len(set(identifiers)) == len(identifiers):
                return items
        raise BaselineBlocked("incompleteArtifacts", "Exact-name artifact pagination was incomplete or changed during bounded retries.")

    def download(self, repository, artifact):
        size = artifact.get("size_in_bytes")
        if type(size) is not int or not 0 < size <= MAX_ARCHIVE_BYTES:
            raise BaselineBlocked("invalidArchive", "The image-manifest archive has an unexpected size.")
        raw = self._request(f"repos/{repository}/actions/artifacts/{artifact['id']}/zip")
        if not 0 < len(raw) <= MAX_ARCHIVE_BYTES or len(raw) != size:
            raise BaselineBlocked("invalidArchive", "The downloaded image-manifest archive has an unexpected size.")
        if not _DIGEST.fullmatch(str(artifact.get("digest", ""))) or "sha256:" + sha256(raw) != artifact["digest"]:
            raise BaselineBlocked("archiveDigestMismatch", "The original archive does not match its GitHub artifact digest.")
        return raw

    def verifier_query(self, repository, endpoint):
        # upgrade.verify_baseline uses an exact-name listing. Aggregate every
        # page here so its exactly-one-run check sees the complete population.
        parsed = urlsplit(endpoint)
        if parsed.path == f"repos/{repository}/actions/artifacts" and parsed.query:
            names = parse_qs(parsed.query).get("name", [])
            if len(names) == 1 and _NAME.fullmatch(names[0]):
                artifacts = self.artifacts(repository, name=names[0])
                return {"total_count": len(artifacts), "artifacts": artifacts}
        return self.json(endpoint)


def _unexpired(artifact):
    if artifact.get("expired") is not False:
        return False
    try:
        expires = datetime.fromisoformat(artifact["expires_at"].replace("Z", "+00:00"))
        return expires > datetime.now(timezone.utc)
    except (KeyError, TypeError, ValueError):
        raise BaselineBlocked("invalidMetadata", "An artifact has no verifiable expiration time.") from None


def _archive_manifest(raw, expected_sha):
    try:
        from io import BytesIO
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) != 1:
                raise ValueError("unexpected entries")
            entry = entries[0]
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if (entry.filename != "release-manifest.json" or entry.is_dir()
                    or entry.flag_bits & 1 or not 0 < entry.file_size <= MAX_MANIFEST_BYTES
                    or mode not in (0, stat.S_IFREG)):
                raise ValueError("unexpected entry")
            manifest_raw = archive.read(entry)
        manifest = validate_manifest(json.loads(manifest_raw), expected_source_sha=expected_sha)
    except (ValueError, KeyError, UnicodeError, zipfile.BadZipFile, NotImplementedError, RuntimeError):
        raise BaselineBlocked("invalidArchive", "The original ZIP does not contain exactly one valid source-bound release manifest.") from None
    return manifest_raw, manifest


def _distinct(prior, candidate):
    return all(prior["images"][component]["source_ref"].rsplit("@", 1)[1]
               != candidate["images"][component]["source_ref"].rsplit("@", 1)[1]
               for component in candidate["images"])


def _projection(value, keys):
    return {key: value[key] for key in keys if key in value}


def _rank_ancestor_graph(graph, candidate_sha):
    """Compute exact Git reachability distances from one complete topo graph.

    Parents precede children when processing the reversed rev-list output.
    Each integer bitset represents a commit's complete ancestor set, including
    itself. The candidate population minus its size is exactly the count from
    ``git rev-list --count PRIOR..CANDIDATE``, including merge-side commits.
    """
    rows = [line.split() for line in graph.splitlines() if line.strip()]
    if (not rows or rows[0][0] != candidate_sha
            or any(not re.fullmatch(r"[0-9a-f]{40}", value) for row in rows for value in row)
            or len({row[0] for row in rows}) != len(rows)):
        raise BaselineBlocked("historyUnavailable", "The candidate parent graph is invalid or incomplete.")
    parents = {row[0]: row[1:] for row in rows}
    masks = {}
    for index, row in enumerate(reversed(rows)):
        source, *source_parents = row
        mask = 1 << index
        for parent in source_parents:
            if parent not in masks:
                raise BaselineBlocked("incompleteHistory", "The candidate parent graph has missing or out-of-order ancestors.")
            mask |= masks[parent]
        masks[source] = mask
    total = len(rows)
    if masks[candidate_sha].bit_count() != total:
        raise BaselineBlocked("incompleteHistory", "The candidate parent graph contains unrelated commits.")
    mainline = {}
    source = candidate_sha
    while True:
        mainline[source] = len(mainline)
        if not parents[source]:
            break
        source = parents[source][0]
    ranks = sorted((total - mask.bit_count(), mainline.get(source, total), source)
                   for source, mask in masks.items() if source != candidate_sha)
    return ranks, mainline


def resolve(candidate_file, repository, out, api=None):
    """Write a fully verified nearest baseline or raise a public BLOCKED reason."""
    from upgrade import PreconditionError, qualifying_run, verify_baseline

    if not _REPOSITORY.fullmatch(repository):
        raise BaselineBlocked("invalidRepository", "Repository must be an owner/repository name.")
    try:
        candidate_raw = Path(candidate_file).read_bytes()
        candidate = validate_manifest(json.loads(candidate_raw))
    except (OSError, ValueError, UnicodeError):
        raise BaselineBlocked("invalidCandidate", "The candidate must be a readable immutable release manifest.") from None
    if _git("rev-parse", "--is-shallow-repository") != "false":
        raise BaselineBlocked("incompleteHistory", "Complete local Git history is required to rank previous main ancestors.")
    candidate_sha = candidate["source_sha"]
    ranks, mainline = _rank_ancestor_graph(_git("rev-list", "--parents", "--topo-order", candidate_sha), candidate_sha)
    out = Path(out).resolve()
    try:
        out.mkdir(mode=0o700, parents=True, exist_ok=False)
    except OSError:
        raise BaselineBlocked("outputUnavailable", "The output directory must be new and writable.") from None
    api = api or GitHubAPI()
    rejected = {"no_unexpired_artifact": 0, "nonqualifying_ci": 0, "nonunique_ci": 0,
                "digest_identical": 0, "expired_during_resolution": 0}
    queried_sources, enumerated_artifacts = 0, 0
    run_cache = {}
    with tempfile.TemporaryDirectory(prefix=".baseline-", dir=out) as staging_name:
        staging = Path(staging_name)
        for distance, first_parent_position, source in ranks:
            artifacts = api.artifacts(repository, name="verified-images-" + source)
            queried_sources += 1
            enumerated_artifacts += len(artifacts)
            eligible = [artifact for artifact in artifacts if _unexpired(artifact)]
            if not eligible:
                rejected["no_unexpired_artifact"] += 1
                continue
            qualified = {}
            for artifact in eligible:
                workflow = artifact.get("workflow_run", {})
                if workflow.get("head_sha") != source or workflow.get("head_branch") != "main":
                    continue
                run_id = artifact["workflow_run"]["id"]
                if type(run_id) is not int:
                    raise BaselineBlocked("invalidMetadata", "An artifact has no verifiable workflow run identity.")
                if run_id not in run_cache:
                    run_cache[run_id] = api.json(f"repos/{repository}/actions/runs/{run_id}")
                run = run_cache[run_id]
                if qualifying_run(run, source):
                    qualified[run_id] = run
            if not qualified:
                rejected["nonqualifying_ci"] += 1
                continue
            if len(qualified) != 1:
                rejected["nonunique_ci"] += 1
                continue
            run_id, run = next(iter(qualified.items()))
            selected = max((item for item in eligible if item["workflow_run"]["id"] == run_id), key=lambda item: item["id"])
            artifact = api.json(f"repos/{repository}/actions/artifacts/{selected['id']}")
            if (artifact.get("name") != "verified-images-" + source or artifact.get("id") != selected["id"]
                    or artifact.get("workflow_run", {}).get("id") != run_id
                    or artifact.get("workflow_run", {}).get("head_sha") != source):
                raise BaselineBlocked("artifactChanged", "Selected artifact identity changed during resolution.")
            if not _unexpired(artifact):
                rejected["expired_during_resolution"] += 1
                continue
            archive_raw = api.download(repository, artifact)
            manifest_raw, prior = _archive_manifest(archive_raw, source)
            if not _distinct(prior, candidate):
                rejected["digest_identical"] += 1
                continue
            selection = {"algorithm": "candidate_reachable_commit_count_then_first_parent_then_sha",
                         "discovery": "exact_name_queries_in_immutable_ancestor_order",
                         "candidate_reachable_commit_distance": distance,
                         "first_parent_position": first_parent_position if source in mainline else None,
                         "artifact_choice": "newest_id_within_unique_qualifying_run",
                         "enumerated_artifacts": enumerated_artifacts, "ranked_ancestor_sources": len(ranks),
                         "queried_ancestor_sources": queried_sources,
                         "rejected_nearer_sources": dict(rejected)}
            provenance = {"schema_version": 1, "repository": repository, "resolved_at": timestamp(),
                "prior_source_sha": source, "candidate_source_sha": candidate_sha, "ancestor_check_passed": True,
                "exactly_one_qualifying_run": True, "selection": selection,
                "artifact": _projection(artifact, ("id", "name", "size_in_bytes", "url", "archive_download_url", "expired", "digest", "created_at", "updated_at", "expires_at", "workflow_run")),
                "ci_run": _projection(run, ("id", "head_sha", "head_branch", "path", "event", "status", "conclusion", "html_url", "created_at", "updated_at", "run_attempt")),
                "archive_sha256": sha256(archive_raw), "prior_manifest_sha256": sha256(manifest_raw),
                "candidate_manifest_sha256": sha256(candidate_raw)}
            manifest_path, archive_path, provenance_path = (staging / name for name in ("release-manifest.json", "verified-images.zip", "provenance.json"))
            manifest_path.write_bytes(manifest_raw)
            archive_path.write_bytes(archive_raw)
            provenance_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
            try:
                _, _, verified = verify_baseline(manifest_path, Path(candidate_file), provenance_path, archive_path,
                    repository=repository, query=lambda endpoint: api.verifier_query(repository, endpoint))
            except PreconditionError:
                raise BaselineBlocked("finalVerificationFailed", "Selected baseline failed the upgrade runner's fresh provenance verification.") from None
            provenance["validated"] = True
            provenance["verification"] = verified
            provenance_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
            for path in (manifest_path, archive_path, provenance_path):
                path.rename(out / path.name)
            return {"schema_version": 1, "status": "RESOLVED", "repository": repository,
                "candidate_source_sha": candidate_sha, "previous_source_sha": source,
                "manifest_sha256": sha256(manifest_raw), "archive_sha256": sha256(archive_raw),
                "artifact_id": artifact["id"], "ci_run_id": run_id, "selection": selection,
                "files": {"manifest": "release-manifest.json", "archive": "verified-images.zip", "provenance": "provenance.json"}}
    raise BaselineBlocked("noEligibleBaseline", "No unexpired distinct verified successful main-ancestor baseline is available.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--repository", default="test-intelligence/testlookup")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = resolve(args.candidate, args.repository, args.out)
    except BaselineBlocked as error:
        result = {"schema_version": 1, "status": "BLOCKED", "error_code": error.code, "reason": str(error)}
    except Exception:
        result = {"schema_version": 1, "status": "BLOCKED", "error_code": "resolutionUnavailable",
                  "reason": "Baseline resolution could not complete; command and authentication diagnostics were suppressed."}
    print(json.dumps(result, separators=(",", ":")), flush=True)
    return 0 if result["status"] == "RESOLVED" else 1


if __name__ == "__main__":
    sys.exit(main())
