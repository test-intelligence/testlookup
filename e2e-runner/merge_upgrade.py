"""Attach the owned upgrade rehearsal to the ordinary release decision."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import zipfile

from policy import evaluate
from run import JOURNEYS, render
from upgrade import public_proof, qualifying_run, validate_manifest

CASE = "TL-DEPLOY-001"
STAGES = (
    "install verified previous build on fresh isolated stores",
    "observe previous revision and immutable image identities",
    "seed prior data and independent preservation oracle",
    "verify prior data before transition",
    "negative migration blocks application replacement",
    "previous build remains running after failed migration",
    "data remains intact after failed migration",
    "quiesce all application writers",
    "snapshot PostgreSQL MongoDB and complete object storage",
    "migrate quiesced data with verified candidate",
    "activate candidate images",
    "observe candidate revision and immutable image identities",
    "verify data IDs counts membership and policy after upgrade",
    "write post-snapshot markers in all three stores",
    "quiesce candidate writers before restoration",
    "verify snapshot checksums and restore exact prior stores",
    "activate previous immutable images without schema downgrade",
    "observe restored previous revision and image identities",
    "verify baseline data and absence of all candidate-only markers",
)


class InvalidEvidence(ValueError):
    """A fixed public reason safe to publish without private operator output."""


def require(condition, message):
    if not condition:
        raise InvalidEvidence(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pending_only_upgrade(report):
    cases = report["cases"]
    operator = [row for row in cases if row["id"] == CASE]
    ordinary = [row for row in cases if row["id"] != CASE]
    return (
        report["verdict"] == "NO_GO"
        and report["tier"] in ("regression", "full")
        and report.get("selected_cases_passed") is True
        and len(operator) == 1
        and operator[0]["status"] == "BLOCKED"
        and operator[0]["evidence"] == "Required case has no recorded execution."
        and evaluate(ordinary, report["errors"], report["tier"])[0] == "GO"
    )


def bind_baseline(folder, candidate, candidate_hash, raw):
    previous_bytes = (folder / "release-manifest.json").read_bytes()
    previous = validate_manifest(json.loads(previous_bytes))
    baseline = json.loads((folder / "provenance.json").read_bytes())
    archive = folder / "verified-images.zip"
    archive_hash = sha(archive.read_bytes())
    with zipfile.ZipFile(archive) as zip_file:
        require(
            zip_file.namelist() == ["release-manifest.json"]
            and zip_file.read("release-manifest.json") == previous_bytes,
            "Baseline archive and previous manifest disagree.",
        )
    provenance = raw["provenance"]
    artifact = baseline["artifact"]
    run = baseline["ci_run"]
    previous_sha = previous["source_sha"]
    require(
        baseline["validated"] is True
        and baseline["ancestor_check_passed"] is True
        and baseline["exactly_one_qualifying_run"] is True
        and previous_sha != candidate["source_sha"]
        and baseline["prior_source_sha"]
        == provenance["previous_source_sha"]
        == previous_sha
        and baseline["candidate_source_sha"]
        == provenance["candidate_source_sha"]
        == candidate["source_sha"]
        and baseline["candidate_manifest_sha256"]
        == provenance["candidate_manifest_hash"]
        == candidate_hash
        and baseline["prior_manifest_sha256"]
        == provenance["previous_manifest_hash"]
        == sha(previous_bytes)
        and baseline["archive_sha256"] == provenance["archive_sha256"] == archive_hash
        and baseline["repository"] == provenance["repository"]
        and provenance["ancestor"] is True
        and provenance["all_component_images_distinct"] is True
        and qualifying_run(run, previous_sha)
        and run["id"] == provenance["ci_run"]["id"]
        and artifact["id"] == provenance["artifact"]["id"]
        and artifact["workflow_run"]["id"] == run["id"]
        and artifact["workflow_run"]["head_sha"] == previous_sha
        and artifact["name"]
        == provenance["artifact"]["name"]
        == "verified-images-" + previous_sha
        and artifact["digest"]
        == provenance["artifact"]["digest"]
        == "sha256:" + archive_hash
        and artifact["expired"] is False
        and provenance["artifact"]["expired"] is False
        and all(
            previous["images"][k]["source_ref"].rsplit("@", 1)[-1]
            != v["source_ref"].rsplit("@", 1)[-1]
            for k, v in candidate["images"].items()
        ),
        "Previous verified build provenance does not match.",
    )
    return previous


def verify_stages(raw, previous, candidate):
    steps = raw["steps"]
    owner = raw["owned_environment"]
    cleanup = raw["cleanup"]
    require(
        owner["project"] == cleanup["project"],
        "Cleanup was measured for a different environment.",
    )
    if raw["target"] == "kubernetes":
        require(
            owner["project"] == owner["namespace"]
            and owner["context"]
            and re.fullmatch("testlookup-e2e-upgrade-[0-9a-f]{12}", owner["namespace"])
            and owner["namespace_uid"] == cleanup["namespace_uid"]
            and bool(owner["namespace_uid"])
            and cleanup["namespace_deleted"] is True,
            "Kubernetes namespace ownership and deletion were not verified.",
        )
    else:
        require(
            re.fullmatch("tl-upgrade-[0-9a-f]{12}", owner["project"])
            and cleanup["down_exit_code"] == 0,
            "Podman Compose project ownership and teardown were not verified.",
        )
    require(
        tuple(s["name"] for s in steps) == STAGES
        and all(
            s["status"] == "PASSED" and s.get("duration_seconds", -1) >= 0
            for s in steps
        ),
        "Upgrade, negative migration, preservation and rollback stages must all pass in order.",
    )
    require(
        raw["cleanup"].get("clean") is True
        and raw["cleanup"].get("verified") is True
        and all(
            raw["cleanup"].get(key) == []
            for key in ("containers", "volumes", "networks")
        ),
        "Owned resource cleanup is incomplete.",
    )
    negative = steps[4]["evidence"]
    require(
        negative["migration_returned_success"] is False
        and negative["app_replacement_allowed"] is False
        and steps[9]["evidence"]["migration_succeeded"] is True,
        "Migration controls lack a measured refusal and successful candidate transition.",
    )
    snapshots = steps[8]["evidence"]
    require(
        all(
            snapshots[k]["size"] > 0
            and re.fullmatch("[a-f0-9]{64}", snapshots[k]["sha256"])
            for k in ("postgres", "mongo", "minio")
        ),
        "Store snapshots lack size or checksum evidence.",
    )
    for index, build in (
        (2, previous),
        (3, previous),
        (6, previous),
        (12, candidate),
        (13, candidate),
        (18, previous),
    ):
        ev = steps[index]["evidence"]
        checks = ev["checks"]
        require(
            ev["ok"] is True
            and ev["expected_revision"]
            == ev["observed_revision"]
            == build["source_sha"],
            "Data preservation evidence is bound to a different build.",
        )
        expected = (
            (
                "candidate_project_present",
                "candidate_object_present",
                "candidate_mongo_document_present",
            )
            if index == 13
            else (
                "project",
                "case_ids_and_counts",
                "membership_and_viewer_access",
                "policy",
                "baseline_object_checksum",
                "baseline_object_metadata_and_acl_hash",
                "baseline_mongo_document",
            )
        )
        require(
            all(checks.get(k) is True for k in expected),
            "A data preservation oracle is missing.",
        )
    require(
        steps[18]["evidence"]["checks"].get("candidate_sentinels_removed") is True,
        "Restored stores lack the candidate-marker removal check.",
    )
    proof = public_proof(raw)
    observations = proof["observations"]
    require(len(observations) == 4, "Four measured image observations are required.")
    for observed, (index, build) in zip(
        observations,
        ((1, previous), (5, previous), (11, candidate), (17, previous)),
        strict=True,
    ):
        require(
            observed["stage"] == STAGES[index]
            and observed["matched"] is True
            and observed["expected_revision"]
            == observed["observed_revision"]
            == build["source_sha"]
            and {i["component"] for i in observed["images"]} == set(build["images"]),
            "Running components or revision differ from the expected stage.",
        )
        for image in observed["images"]:
            ref = build["images"][image["component"]]["source_ref"]
            allowed = {ref.rsplit("@", 1)[-1]}
            if raw["target"] == "kubernetes":
                for item in steps[index]["evidence"]["images"]:
                    if item.get("candidate") == ref and item.get("matched") is True:
                        allowed.update(item.get("children", []))
            require(
                image["matched"] is True
                and image["candidate"] == ref
                and re.fullmatch("sha256:[a-f0-9]{64}", image["running"])
                and image["running"] in allowed,
                "An observed image differs from its verified immutable reference.",
            )
    require(
        datetime.fromisoformat(proof["ended"])
        >= datetime.fromisoformat(proof["started"]),
        "Upgrade completion time is missing or precedes its start.",
    )
    return proof


def merge(
    directory,
    upgrade_file,
    manifest_file,
    baseline,
    platform,
    suite_exit=0,
    operator_exit=0,
):
    report = json.loads((directory / "results.json").read_bytes())
    cases = report["cases"]
    rows = [c for c in cases if c["id"] == CASE]
    ordinary = [c for c in cases if c["id"] != CASE]
    reported_failure = bool(report["errors"]) or (
        any(c["status"] != "PASSED" for c in ordinary)
        and evaluate(ordinary, report["errors"], report["tier"])[0] != "GO"
    )
    report["suite_exit"] = suite_exit
    try:
        require(
            len(rows) == 1 and rows[0]["priority"] == "P0",
            "Missing or duplicate deployment obligation.",
        )
        require(
            len({c["id"] for c in cases}) == len(cases)
            and set(JOURNEYS) <= {c["id"].split()[0] for c in cases},
            "The release journey denominator is incomplete or duplicated.",
        )
        require(
            report["tier"] in ("regression", "full")
            and report["target"] == "ephemeral"
            and report.get("execution_backend") == platform,
            "Suite and upgrade execution platforms differ.",
        )
        candidate_bytes = manifest_file.read_bytes()
        candidate = validate_manifest(json.loads(candidate_bytes))
        digest = sha(candidate_bytes)
        require(
            report["manifest_hash"] == digest,
            "Suite and candidate manifest identities differ.",
        )
        revision = report["observed_build"].get("revision")
        if not revision:
            message = "The ordinary suite did not reach a running candidate to verify its revision."
            # A failed launcher emits only blocked ordinary rows. Its independent
            # upgrade can still have a verified result for these manifest bytes.
            require(
                suite_exit != 0 and all(c["status"] == "BLOCKED" for c in ordinary),
                message,
            )
            if message not in report["errors"]:
                report["errors"].append(message)
        else:
            require(
                revision == candidate["source_sha"]
                and report.get("images") == candidate["images"],
                "Suite and candidate manifest identities differ.",
            )
        raw = json.loads(upgrade_file.read_bytes())
        require(
            raw["id"] == CASE and raw["target"] == platform,
            "Upgrade case or platform differs.",
        )
        previous = bind_baseline(baseline, candidate, digest, raw)
        last_stage = next(
            (
                step["name"]
                for step in reversed(raw.get("steps", []))
                if step.get("name") in STAGES
            ),
            "preflight",
        )
        if raw["status"] == "FAILED":
            rows[0].update(
                status="FAILED",
                evidence=f"Upgrade operator reported a failure at {last_stage}.",
            )
        else:
            require(
                raw["status"] == "PASSED" and operator_exit == 0,
                f"Upgrade operator did not complete successfully. Last recorded stage was {last_stage}.",
            )
            proof = verify_stages(raw, previous, candidate)
            require(
                (rows[0]["status"] == "BLOCKED" and "upgrade_proof" not in report)
                or (
                    rows[0]["status"] == "PASSED"
                    and report.get("upgrade_proof") == proof
                ),
                "A completed deployment outcome cannot be replaced.",
            )
            report["upgrade_proof"] = proof
            rows[0].update(
                status="PASSED",
                evidence=f"19 ordered upgrade stages, four observed builds, three restored stores and verified cleanup on {platform}.",
            )
    except (
        InvalidEvidence,
        ValueError,
        KeyError,
        TypeError,
        OSError,
        zipfile.BadZipFile,
        IndexError,
    ) as error:
        reason = (
            str(error)
            if isinstance(error, InvalidEvidence)
            else "Upgrade evidence was missing, malformed or incomplete."
        )
        if rows and rows[0]["status"] == "BLOCKED":
            rows[0]["evidence"] = reason
        report["errors"].append(reason)
    if suite_exit and not (suite_exit == 1 and reported_failure):
        report["errors"].append(
            "The ordinary suite or its launcher exited unsuccessfully."
        )
    report["verdict"], report["reason"] = evaluate(
        cases, report["errors"], report["tier"]
    )
    render(directory, report)
    return 0 if report["verdict"] == "GO" else 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("results", "upgrade", "candidate", "baseline"):
        p.add_argument("--" + name, required=True, type=Path)
    p.add_argument("--platform", required=True, choices=("podman", "kubernetes"))
    p.add_argument("--suite-exit", type=int, default=0)
    p.add_argument("--operator-exit", type=int, default=0)
    a = p.parse_args()
    return merge(
        a.results,
        a.upgrade,
        a.candidate,
        a.baseline,
        a.platform,
        a.suite_exit,
        a.operator_exit,
    )


if __name__ == "__main__":
    raise SystemExit(main())
