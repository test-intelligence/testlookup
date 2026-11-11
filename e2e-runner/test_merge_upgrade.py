"""The final release report accepts only a complete, candidate-bound operator pass."""

import json
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

import merge_upgrade as merger
import upgrade
from upgrade_test import baseline_files


def save(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    files, artifact, run, query = baseline_files(tmp_path)
    prior_file, candidate_file, provenance_file, archive_file = files
    monkeypatch.setattr(
        upgrade.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0)
    )
    previous, candidate, provenance = upgrade.verify_baseline(
        *files, repository="example/project", query=query
    )
    prior_file.rename(tmp_path / "release-manifest.json")
    archive_file.rename(tmp_path / "verified-images.zip")
    save(
        provenance_file,
        {
            "repository": "example/project",
            "validated": True,
            "ancestor_check_passed": True,
            "exactly_one_qualifying_run": True,
            "prior_source_sha": previous["source_sha"],
            "candidate_source_sha": candidate["source_sha"],
            "archive_sha256": provenance["archive_sha256"],
            "candidate_manifest_sha256": provenance["candidate_manifest_hash"],
            "prior_manifest_sha256": provenance["previous_manifest_hash"],
            "artifact": artifact,
            "ci_run": run,
        },
    )
    started = "2026-06-01T00:00:00+00:00"
    steps = [
        {"name": name, "status": "PASSED", "duration_seconds": 1, "evidence": {}}
        for name in merger.STAGES
    ]
    for idx, manifest in (
        (1, previous),
        (5, previous),
        (11, candidate),
        (17, previous),
    ):
        revision = manifest["source_sha"]
        steps[idx]["evidence"] = {
            "source_sha": revision,
            "build": {"build": {"revision": revision}},
            "matched": True,
            "images": [
                {
                    "service": k,
                    "component": k,
                    "candidate": img["source_ref"],
                    "running": img["source_ref"].split("@")[1],
                    "matched": True,
                }
                for k, img in manifest["images"].items()
            ],
        }
    for idx, manifest in (
        (2, previous),
        (3, previous),
        (6, previous),
        (12, candidate),
        (13, candidate),
        (18, previous),
    ):
        revision = manifest["source_sha"]
        steps[idx]["evidence"] = {
            "ok": True,
            "expected_revision": revision,
            "observed_revision": revision,
            "checks": dict.fromkeys(
                (
                    "project",
                    "case_ids_and_counts",
                    "membership_and_viewer_access",
                    "policy",
                    "baseline_object_checksum",
                    "baseline_object_metadata_and_acl_hash",
                    "baseline_mongo_document",
                    "candidate_sentinels_removed",
                    "candidate_project_present",
                    "candidate_object_present",
                    "candidate_mongo_document_present",
                ),
                True,
            ),
        }
    steps[4]["evidence"] = {
        "migration_returned_success": False,
        "app_replacement_allowed": False,
    }
    steps[8]["evidence"] = {
        key: {"size": 10, "sha256": "a" * 64} for key in ("postgres", "mongo", "minio")
    }
    steps[9]["evidence"] = {"migration_succeeded": True}
    raw = {
        "schema_version": 1,
        "id": merger.CASE,
        "target": "podman",
        "started": started,
        "ended": started,
        "status": "PASSED",
        "steps": steps,
        "provenance": provenance,
        "owned_environment": {"project": "tl-upgrade-" + "a" * 12},
        "cleanup": {
            "project": "tl-upgrade-" + "a" * 12,
            "down_exit_code": 0,
            "clean": True,
            "verified": True,
            "containers": [],
            "volumes": [],
            "networks": [],
        },
    }
    results = {
        "manifest_hash": provenance["candidate_manifest_hash"],
        "observed_build": {"revision": candidate["source_sha"]},
        "target": "ephemeral",
        "execution_backend": "podman",
        "tier": "regression",
        "images": candidate["images"],
        "start": started,
        "end": started,
        "tag": candidate["version"],
        "errors": [],
        "verdict": "NO_GO",
        "reason": "Awaiting operator",
        "selected_cases_passed": True,
        "cases": [
            {
                "id": key,
                "priority": priority,
                "status": "PASSED",
                "evidence": "Measured",
            }
            for key, priority in merger.JOURNEYS.items()
        ],
    }
    deployment = next(row for row in results["cases"] if row["id"] == merger.CASE)
    deployment.update(
        status="BLOCKED", evidence="Required case has no recorded execution."
    )
    return tmp_path, candidate_file, results, raw


def execute(inputs, platform="podman", **kwargs):
    directory, candidate, report, raw = inputs
    save(directory / "results.json", report)
    save(directory / "operator.json", raw)
    code = merger.merge(
        directory, directory / "operator.json", candidate, directory, platform, **kwargs
    )
    return code, json.loads((directory / "results.json").read_bytes())


def test_verified_rehearsal_renders_public_reports(inputs):
    directory, candidate, results, raw = inputs
    assert merger.pending_only_upgrade(results)
    code, report = execute(inputs)
    assert code == 0 and report["verdict"] == "GO"
    assert report["upgrade_proof"] == upgrade.public_proof(raw)
    assert raw["owned_environment"]["project"] not in json.dumps(report)
    assert ET.parse(directory / "release-junit.xml").getroot().get("errors") == "0"
    assert "GO" in (directory / "summary.md").read_text(encoding="utf-8")
    assert (directory / "index.html").is_file()
    assert (
        merger.merge(
            directory, directory / "operator.json", candidate, directory, "podman"
        )
        == 0
    )


def test_kubernetes_namespace_and_registry_child_image(inputs):
    _, _, report, raw = inputs
    raw["target"] = report["execution_backend"] = "kubernetes"
    namespace = "testlookup-e2e-upgrade-" + "f" * 12
    raw["owned_environment"] = {
        "project": namespace,
        "namespace": namespace,
        "namespace_uid": "owned-uid",
        "context": "isolated",
    }
    raw["cleanup"].update(
        project=namespace, namespace_uid="owned-uid", namespace_deleted=True
    )
    image = raw["steps"][11]["evidence"]["images"][0]
    image["running"] = "sha256:" + "f" * 64
    image["children"] = [image["running"]]
    assert execute(inputs, platform="kubernetes")[0] == 0
    raw["cleanup"]["namespace_uid"] = "different-uid"
    assert execute(inputs, platform="kubernetes")[0] != 0


def test_nonzero_ordinary_exit_preserves_the_measured_p1_defect(inputs):
    _, _, ordinary, _ = inputs
    row = next(c for c in ordinary["cases"] if c["id"] == "TL-TCM-001")
    row.update(status="FAILED", evidence="Measured recurrence mismatch.")
    code, report = execute(inputs, suite_exit=1)
    assert code == 1 and report["verdict"] == "NO_GO" and report["suite_exit"] == 1
    assert report["errors"] == [] and "P1" in report["reason"]
    assert next(c for c in report["cases"] if c["id"] == "TL-TCM-001") == row
    assert (
        next(c for c in report["cases"] if c["id"] == merger.CASE)["status"] == "PASSED"
    )


def test_unexplained_ordinary_exit_blocks_an_otherwise_passing_release(inputs):
    code, report = execute(inputs, suite_exit=1)
    assert code == 1 and report["verdict"] == "NO_GO" and report["suite_exit"] == 1
    assert "launcher exited" in report["errors"][0]


@pytest.mark.parametrize(
    "fault",
    (
        "other_platform",
        "other_manifest",
        "baseline",
        "missing_stage",
        "wrong_image",
        "different_project",
        "dirty",
        "missing_marker",
        "missing_case",
        "failed_operator",
        "ordinary_failure",
        "suite_exit",
        "operator_exit",
    ),
)
def test_bad_evidence_or_other_failed_case_cannot_promote(inputs, fault):
    directory, _, results, raw = inputs
    if fault == "other_platform":
        raw["target"] = "kubernetes"
    elif fault == "other_manifest":
        results["manifest_hash"] = "0" * 64
    elif fault == "baseline":
        raw["provenance"]["archive_sha256"] = "0" * 64
    elif fault == "missing_stage":
        del raw["steps"][9]
    elif fault == "wrong_image":
        raw["steps"][11]["evidence"]["images"][0]["running"] = "sha256:" + "0" * 64
    elif fault == "different_project":
        raw["cleanup"]["project"] = "tl-upgrade-" + "f" * 12
    elif fault == "dirty":
        raw["cleanup"]["volumes"].append("remaining")
    elif fault == "missing_marker":
        raw["steps"][-1]["evidence"]["checks"]["candidate_sentinels_removed"] = False
    elif fault == "missing_case":
        results["cases"] = [c for c in results["cases"] if c["id"] != "TL-SPINE-001"]
    elif fault == "failed_operator":
        raw["status"] = "FAILED"
    elif fault == "ordinary_failure":
        next(c for c in results["cases"] if c["id"] == "TL-SPINE-001")["status"] = (
            "FAILED"
        )
        assert not merger.pending_only_upgrade(results)
    code, report = execute(
        inputs,
        suite_exit=int(fault == "suite_exit"),
        operator_exit=int(fault == "operator_exit"),
    )
    assert code and report["verdict"] == "NO_GO"
    if fault == "failed_operator":
        assert (
            next(r for r in report["cases"] if r["id"] == merger.CASE)["status"]
            == "FAILED"
        )
    if fault == "ordinary_failure":
        assert (
            next(r for r in report["cases"] if r["id"] == "TL-SPINE-001")["status"]
            == "FAILED"
        )
