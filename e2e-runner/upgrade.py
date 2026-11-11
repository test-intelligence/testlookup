"""Prove TL-DEPLOY-001 on a fresh prior -> candidate -> restored-prior stack.

The supplied baseline is checked against live GitHub Actions metadata and its
original archive. This operator proof deliberately does not alter the release
runner, catalogue or promotion policy. Raw datastore snapshots stay inside the
owned disposable infrastructure containers and disappear during cleanup.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Callable
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/release"))
from release_manifest import validate_manifest  # noqa: E402


class PreconditionError(RuntimeError):
    """Required immutable provenance, tools or isolation are unavailable."""


class ProofFailure(AssertionError):
    """An exercised upgrade or preservation oracle did not hold."""

    def __init__(self, message, evidence=None):
        super().__init__(message)
        self.evidence = evidence


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def github_json(endpoint):
    try:
        result = subprocess.run(["gh", "api", endpoint], capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PreconditionError("GitHub verification is unavailable") from error
    if result.returncode:
        raise PreconditionError("GitHub verification failed; authentication output suppressed")
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise PreconditionError("GitHub returned invalid provenance metadata") from error


def qualifying_run(run, sha):
    return (run.get("head_sha") == sha and run.get("head_branch") == "main"
            and run.get("event") == "push" and run.get("path") == ".github/workflows/ci.yml"
            and run.get("status") == "completed" and run.get("conclusion") == "success")


def verify_baseline(previous_file, candidate_file, provenance_file, archive_file,
                    repository="test-intelligence/testlookup", query: Callable = github_json):
    """Reject invented, expired, unrelated or digest-identical prior builds."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise PreconditionError("Invalid repository name")
    prior_raw, candidate_raw = previous_file.read_bytes(), candidate_file.read_bytes()
    prior = validate_manifest(json.loads(prior_raw))
    candidate = validate_manifest(json.loads(candidate_raw))
    if prior["source_sha"] == candidate["source_sha"]:
        raise PreconditionError("A true previous build must have a different source revision")
    if any(prior["images"][name]["source_ref"].rsplit("@", 1)[1]
           == candidate["images"][name]["source_ref"].rsplit("@", 1)[1] for name in prior["images"]):
        raise PreconditionError("Every application component needs a distinct prior image")
    ancestry = subprocess.run(["git", "merge-base", "--is-ancestor", prior["source_sha"],
                               candidate["source_sha"]], cwd=ROOT, capture_output=True)
    if ancestry.returncode:
        raise PreconditionError("Previous source is not a verified local ancestor of the candidate")
    recorded = json.loads(provenance_file.read_bytes())
    if recorded.get("repository") != repository:
        raise PreconditionError("Baseline provenance belongs to another repository")
    artifact_id = recorded["artifact"]["id"]
    artifact = query(f"repos/{repository}/actions/artifacts/{artifact_id}")
    run_id = artifact["workflow_run"]["id"]
    run = query(f"repos/{repository}/actions/runs/{run_id}")
    name = "verified-images-" + prior["source_sha"]
    if (artifact.get("name") != name or artifact.get("expired") is not False
            or artifact["workflow_run"].get("head_sha") != prior["source_sha"]
            or not qualifying_run(run, prior["source_sha"])):
        raise PreconditionError("Previous image artifact lacks successful main-push CI provenance")
    if datetime.fromisoformat(artifact["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
        raise PreconditionError("Previous image artifact has expired")
    # Keep the same exactly-one successful main CI run contract as release authorization.
    listing = query(f"repos/{repository}/actions/artifacts?name={name}&per_page=100")
    if listing.get("total_count", len(listing["artifacts"])) > len(listing["artifacts"]):
        raise PreconditionError("Previous artifact enumeration is incomplete; cannot prove uniqueness")
    run_ids = {item["workflow_run"]["id"] for item in listing["artifacts"] if not item["expired"]}
    matches = []
    for item in run_ids:
        observed = run if item == run_id else query(f"repos/{repository}/actions/runs/{item}")
        if qualifying_run(observed, prior["source_sha"]):
            matches.append(item)
    if matches != [run_id]:
        raise PreconditionError("Expected exactly one qualifying previous main CI run")
    archive_hash = sha256(archive_file.read_bytes())
    if artifact.get("digest") != "sha256:" + archive_hash:
        raise PreconditionError("Previous manifest archive does not match the GitHub artifact digest")
    with zipfile.ZipFile(archive_file) as archive:
        if archive.namelist() != ["release-manifest.json"]:
            raise PreconditionError("Previous artifact archive has unexpected entries")
        if archive.read("release-manifest.json") != prior_raw:
            raise PreconditionError("Previous manifest bytes differ from the verified artifact")
    evidence = {"repository": repository, "checked_at": timestamp(), "previous_source_sha": prior["source_sha"],
        "candidate_source_sha": candidate["source_sha"], "ancestor": True,
        "all_component_images_distinct": True, "previous_manifest_hash": sha256(prior_raw),
        "candidate_manifest_hash": sha256(candidate_raw), "archive_sha256": archive_hash,
        "artifact": {key: artifact[key] for key in ("id", "name", "digest", "expires_at", "expired")},
        "ci_run": {key: run[key] for key in ("id", "head_sha", "head_branch", "event", "path", "conclusion", "html_url")}}
    return prior, candidate, evidence


def fixture(environment, action, state=None, revision=None):
    from upgrade_fixture import render

    raw = environment.exec_python(render(action, state=state, expected_revision=revision))
    lines = [line.split("UPGRADE_JSON=", 1)[1] for line in raw.splitlines() if line.startswith("UPGRADE_JSON=")]
    if len(lines) != 1:
        raise ProofFailure("Fixture did not return exactly one sanitized evidence record")
    result = json.loads(lines[0])
    if result.get("ok") is not True:
        evidence = {key: result.get(key) for key in ("stage", "error_code", "error_type", "requests")}
        raise ProofFailure("Fixture oracle failed: " + str(result.get("stage", action))
                           + " (" + str(result.get("error_code", "oracleMismatch"))
                           + "; " + str(result.get("error_type", "unknown")) + ")", evidence)
    return result


def public_proof(report):
    """Allowlist a digest-bound case proof without namespace, IDs or host paths."""
    provenance = report.get("provenance", {})
    observations = []
    app_services = {"backend", "frontend", "mcp", "worker", "worker-children", "beat",
                    "worker-critical", "worker-ingestion", "worker-ai", "worker-default"}
    for step in report["steps"]:
        evidence = step.get("evidence")
        if not isinstance(evidence, dict) or not isinstance(evidence.get("images"), list):
            continue
        unique = {}
        for item in evidence["images"]:
            if item.get("service") not in app_services:
                continue
            value = {key: item[key] for key in ("component", "candidate", "running", "matched")}
            unique[(value["component"], value["candidate"], value["running"])] = value
        observations.append({"stage": step["name"], "expected_revision": evidence.get("source_sha"),
            "observed_revision": evidence.get("build", {}).get("build", {}).get("revision"),
            "matched": evidence.get("matched") is True, "images": list(unique.values())})
    return {"schema_version": 1, "id": report["id"], "priority": "P0", "journey": "J13",
        "target": report["target"], "status": report["status"],
        "scope": "Installation, true prior-to-candidate transition, failed-migration hold and snapshot rollback only",
        "manifest_hash": provenance.get("candidate_manifest_hash"),
        "candidate_source_sha": provenance.get("candidate_source_sha"),
        "previous_source_sha": provenance.get("previous_source_sha"),
        "previous_manifest_hash": provenance.get("previous_manifest_hash"),
        "previous_archive_sha256": provenance.get("archive_sha256"),
        "previous_verified_ci_run": provenance.get("ci_run", {}).get("id"),
        "started": report["started"], "ended": report.get("ended"),
        "stages": [{key: step.get(key) for key in ("name", "status", "duration_seconds")} for step in report["steps"]],
        "observations": observations,
        "cleanup_verified": report.get("cleanup", {}).get("clean") is True,
        "harness_source_sha256": report.get("harness", {}).get("source_sha256", {}),
        "release_promotion_authorized": False}


def write_report(out, report):
    (out / "upgrade-results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (out / "upgrade-proof.json").write_text(json.dumps(public_proof(report), indent=2) + "\n", encoding="utf-8")
    lines = ["# Installation, upgrade and rollback proof", "", f"{report['status']} — {report.get('reason', 'In progress')}",
             "", f"Case {report['id']} · target {report['target']}",
             "This report covers only TL-DEPLOY-001; it is not a release promotion decision.", ""]
    for step in report["steps"]:
        lines.append(f"- {step['name']}: {step['status']} ({step.get('duration_seconds', 0):.2f}s)")
    if report.get("cleanup"):
        lines += ["", "Cleanup: " + json.dumps(report["cleanup"], sort_keys=True)]
    (out / "upgrade-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def exercise(environment, prior, candidate, report, persist=lambda: None):
    """Exercise actual transitions; negative migration cannot replace prior apps."""
    def step(name, call):
        started = time.monotonic()
        item = {"name": name, "started": timestamp(), "status": "RUNNING"}
        report["steps"].append(item)
        persist()
        try:
            evidence = call()
            item.update(status="PASSED", evidence=evidence)
            return evidence
        except Exception as error:
            item["status"] = "FAILED"
            if isinstance(error, ProofFailure) and error.evidence is not None:
                item["evidence"] = error.evidence
            raise
        finally:
            item.update(ended=timestamp(), duration_seconds=round(time.monotonic() - started, 3))
            persist()

    def observe(manifest):
        observed = environment.observe(manifest)
        if observed.get("matched") is not True:
            raise ProofFailure("Running images or observed revision differ from the expected build")
        return observed

    step("install verified previous build on fresh isolated stores", environment.install)
    report["owned_environment"] = {key: getattr(environment, key) for key in ("project", "namespace", "namespace_uid", "context") if hasattr(environment, key)}
    step("observe previous revision and immutable image identities", lambda: observe(prior))
    seed = step("seed prior data and independent preservation oracle", lambda: fixture(environment, "seed", revision=prior["source_sha"]))
    state = seed["state"]
    step("verify prior data before transition", lambda: fixture(environment, "check", state, prior["source_sha"]))

    def reject_failed_migration():
        result = environment.migrate(candidate, negative=True)
        if result is not False:
            raise ProofFailure("Invalid-revision migration did not fail closed")
        return {"migration_returned_success": False, "injection": "Alembic nonexistent revision tl_e2e_nonexistent", "app_replacement_allowed": False}
    step("negative migration blocks application replacement", reject_failed_migration)
    step("previous build remains running after failed migration", lambda: observe(prior))
    step("data remains intact after failed migration", lambda: fixture(environment, "check", state, prior["source_sha"]))
    step("quiesce all application writers", environment.quiesce)

    def snapshot():
        result = environment.snapshot()
        for name in ("postgres", "mongo", "minio"):
            item = result[name]
            if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) or item["size"] <= 0:
                raise ProofFailure("Snapshot lacks a nonempty checksummed " + name + " payload")
        return result
    step("snapshot PostgreSQL MongoDB and complete object storage", snapshot)

    def migrate_candidate():
        if environment.migrate(candidate) is not True:
            raise ProofFailure("Candidate migration failed; application replacement withheld")
        return {"migration_succeeded": True, "application_replacement_permitted": True}
    step("migrate quiesced data with verified candidate", migrate_candidate)
    step("activate candidate images", lambda: environment.activate(candidate))
    step("observe candidate revision and immutable image identities", lambda: observe(candidate))
    step("verify data IDs counts membership and policy after upgrade", lambda: fixture(environment, "check", state, candidate["source_sha"]))
    marked = step("write post-snapshot markers in all three stores", lambda: fixture(environment, "candidate_mark", state, candidate["source_sha"]))
    step("quiesce candidate writers before restoration", environment.quiesce)
    step("verify snapshot checksums and restore exact prior stores", environment.restore)
    step("activate previous immutable images without schema downgrade", lambda: environment.activate(prior))
    step("observe restored previous revision and image identities", lambda: observe(prior))
    step("verify baseline data and absence of all candidate-only markers", lambda: fixture(environment, "check", marked["state"], prior["source_sha"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("podman", "kubernetes"), required=True)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline-provenance", type=Path, required=True)
    parser.add_argument("--baseline-archive", type=Path, required=True)
    parser.add_argument("--repository", default="test-intelligence/testlookup")
    parser.add_argument("--context", help="Required explicit context for Kubernetes")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        parser.error("--out must be a fresh private directory")
    if args.target == "kubernetes" and not args.context:
        parser.error("Kubernetes requires an explicit --context")
    out.mkdir(parents=True)
    report = {"schema_version": 1, "id": "TL-DEPLOY-001", "priority": "P0", "journey": "J13",
              "target": args.target, "started": timestamp(), "status": "RUNNING", "steps": []}
    environment = None
    stage = "preflight"
    persist = lambda: write_report(out, report)
    persist()
    try:
        prior, candidate, evidence = verify_baseline(args.previous, args.candidate,
            args.baseline_provenance, args.baseline_archive, args.repository)
        report["provenance"] = evidence
        if args.target == "podman":
            from upgrade_podman import PodmanUpgrade
            environment = PodmanUpgrade(prior, candidate, out)
        else:
            from upgrade_kubernetes import KubernetesUpgrade
            environment = KubernetesUpgrade(prior, candidate, out, context=args.context)
        stage = "execution"
        report["harness"] = {"source_sha256": {
            path.name: sha256(path.read_bytes()) for path in Path(__file__).parent.glob("upgrade*.py")
            if not path.name.endswith("_test.py")}, "captured_at": timestamp()}
        exercise(environment, prior, candidate, report, persist)
        report.update(status="PASSED", reason="Verified previous install, failed-migration hold, candidate transition, and exact snapshot restoration preserve the oracle data.")
    except (ProofFailure, AssertionError) as error:
        report.update(status="FAILED", reason=str(error))
    except Exception as error:
        # Adapter exceptions suppress credential-bearing command output. Preserve
        # only their type: infrastructure absence must never become a pass.
        report.update(status="BLOCKED", reason=f"{stage} could not complete ({type(error).__name__}); inspect private adapter diagnostics.")
        if isinstance(error, PreconditionError):
            report["reason"] = str(error)
    finally:
        if environment is not None:
            try:
                report["cleanup"] = environment.cleanup()
                if report["cleanup"].get("clean") is not True:
                    report.update(status="BLOCKED", reason="Owned disposable resources lack verified cleanup.")
            except Exception as error:
                report["cleanup"] = {"clean": False, "error_type": type(error).__name__}
                report.update(status="BLOCKED", reason="Owned disposable cleanup failed; resources need inspection.")
        report["ended"] = timestamp()
        persist()
    print(json.dumps({"id": report["id"], "target": args.target, "status": report["status"],
                      "reason": report["reason"], "evidence": str(out / "upgrade-results.json")}), flush=True)
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    sys.exit(main())
