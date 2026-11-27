"""Exact-SHA secret scans must authorize publication and release, fail closed."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import verify_secret_scan as verifier  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
REPOSITORY = "acme/testlookup"
SHA = "a" * 40
WORKFLOW_ID = 42


def _run(**changes):
    run = {
        "id": 101, "workflow_id": WORKFLOW_ID, "run_number": 9, "run_attempt": 1,
        "path": verifier.WORKFLOW_PATH, "head_sha": SHA, "head_branch": "main", "event": "push",
        "repository": {"full_name": REPOSITORY}, "head_repository": {"full_name": REPOSITORY},
        "status": "completed", "conclusion": "success",
    }
    run.update(changes)
    return run


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


class FakeAPI:
    def __init__(self, snapshots, *, workflow=None, detail=None):
        self.snapshots = snapshots
        self.workflow = workflow or {"id": WORKFLOW_ID, "path": verifier.WORKFLOW_PATH, "state": "active"}
        self.detail = detail
        self.poll = -1
        self.calls = []

    def __call__(self, path, *, timeout):
        self.calls.append((path, timeout))
        if path.endswith("/workflows/secret-scan.yml"):
            return deepcopy(self.workflow)
        if "/workflows/42/runs?" in path:
            query = parse_qs(urlsplit(path).query)
            assert query["head_sha"] == [SHA]
            assert query["event"] == ["push"] and query["branch"] == ["main"]
            page = int(query["page"][0])
            if page == 1:
                self.poll += 1
            current = self.snapshots[min(self.poll, len(self.snapshots) - 1)]
            return {"total_count": len(current), "workflow_runs": deepcopy(current[(page - 1) * 100:page * 100])}
        if "/actions/runs/" in path:
            if self.detail is not None:
                return deepcopy(self.detail)
            current = self.snapshots[min(self.poll, len(self.snapshots) - 1)]
            return deepcopy(next(run for run in current if run["id"] == int(path.rsplit("/", 1)[1])))
        raise AssertionError(f"Unexpected endpoint: {path}")


def _verify(api, clock=None):
    clock = clock or Clock()
    return verifier.verify_secret_scan(
        REPOSITORY, SHA, api_get=api, clock=clock, sleep=clock.sleep,
        timeout_seconds=3, poll_seconds=1,
    )


def test_exact_main_push_success_authorizes_the_sha_and_records_run_identity():
    api = FakeAPI([[_run()]])
    assert _verify(api) == {
        "repository": REPOSITORY, "source_sha": SHA, "workflow_path": verifier.WORKFLOW_PATH,
        "workflow_id": WORKFLOW_ID, "run_id": 101, "run_attempt": 1,
    }
    assert len(api.calls) == 3
    assert all(0 < timeout <= 3 for _, timeout in api.calls)


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "skipped", "neutral", None])
def test_completed_non_success_never_authorizes(conclusion):
    clock = Clock()
    with pytest.raises(verifier.SecretScanVerificationError, match="did not succeed"):
        _verify(FakeAPI([[_run(conclusion=conclusion)]]), clock)
    assert clock.waits == []


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "pending"])
def test_pending_wait_is_bounded_and_never_authorizes(status):
    clock = Clock()
    with pytest.raises(verifier.SecretScanVerificationError, match="Timed out"):
        _verify(FakeAPI([[_run(status=status, conclusion=None)]]), clock)
    assert clock.now == 3
    assert clock.waits == [1, 1, 1]


def test_missing_evidence_waits_for_cross_workflow_delivery_then_accepts_success():
    clock = Clock()
    api = FakeAPI([[], [_run(status="in_progress", conclusion=None)], [_run()]])
    assert _verify(api, clock)["run_id"] == 101
    assert clock.waits == [1, 1]


def test_missing_evidence_times_out_without_triggering_a_scan():
    api, clock = FakeAPI([[]]), Clock()
    with pytest.raises(verifier.SecretScanVerificationError, match="Timed out"):
        _verify(api, clock)
    assert clock.now == 3
    assert all("dispatch" not in path and "rerun" not in path for path, _ in api.calls)


@pytest.mark.parametrize("change", [
    {"head_sha": "b" * 40}, {"head_branch": "develop"}, {"event": "pull_request"},
    {"event": "workflow_dispatch"}, {"workflow_id": 999},
    {"path": ".github/workflows/foreign.yml"},
    {"repository": {"full_name": "foreign/testlookup"}},
    {"head_repository": {"full_name": "fork/testlookup"}},
    {"run_attempt": None}, {"id": None},
])
def test_wrong_sha_branch_event_workflow_or_repository_cannot_authorize(change):
    with pytest.raises(verifier.SecretScanVerificationError, match="Timed out"):
        _verify(FakeAPI([[_run(**change)]]))


@pytest.mark.parametrize("latest", [
    {"status": "completed", "conclusion": "failure"},
    {"status": "in_progress", "conclusion": None},
])
def test_earlier_success_cannot_bypass_a_newer_failed_or_pending_run(latest):
    runs = [_run(), _run(id=102, run_number=10, **latest)]
    with pytest.raises(verifier.SecretScanVerificationError):
        _verify(FakeAPI([runs]))


def test_direct_run_refresh_cannot_reuse_a_stale_successful_attempt():
    current = _run(run_attempt=2, status="in_progress", conclusion=None)
    with pytest.raises(verifier.SecretScanVerificationError, match="mismatched scope"):
        _verify(FakeAPI([[current]], detail=_run(run_attempt=1)))


@pytest.mark.parametrize("change", [{"head_sha": "b" * 40}, {"id": 102}, {"path": "other.yml"}])
def test_refreshed_run_must_still_match_selected_identity_and_scope(change):
    with pytest.raises(verifier.SecretScanVerificationError, match="mismatched scope"):
        _verify(FakeAPI([[_run()]], detail=_run(**change)))


def test_complete_filtered_run_listing_is_paginated():
    api = FakeAPI([[_run(id=200 + n, head_sha="b" * 40) for n in range(100)] + [_run()]])
    assert _verify(api)["run_id"] == 101
    assert any("page=2" in path for path, _ in api.calls)


@pytest.mark.parametrize("workflow", [
    {"id": WORKFLOW_ID, "path": verifier.WORKFLOW_PATH, "state": "disabled_manually"},
    {"id": WORKFLOW_ID, "path": "foreign.yml", "state": "active"},
    {"path": verifier.WORKFLOW_PATH, "state": "active"},
])
def test_untrusted_or_disabled_workflow_metadata_is_rejected(workflow):
    with pytest.raises(verifier.SecretScanVerificationError, match="trusted secret-scan workflow"):
        _verify(FakeAPI([[_run()]], workflow=workflow))


def test_invalid_inputs_fail_before_any_api_call():
    api = FakeAPI([[_run()]])
    for repo, sha in [(REPOSITORY, "main"), (REPOSITORY, "A" * 40), ("acme/repo?bad", SHA)]:
        with pytest.raises(verifier.SecretScanVerificationError):
            verifier.verify_secret_scan(repo, sha, api_get=api)
    assert api.calls == []


def test_api_uses_get_only_and_suppresses_sensitive_failure_diagnostics(monkeypatch):
    seen = []

    def reject(command, **kwargs):
        seen.append((command, kwargs))
        return subprocess.CompletedProcess(command, 1, "secret stdout", "Bearer sensitive diagnostic")

    monkeypatch.setattr(verifier.subprocess, "run", reject)
    with pytest.raises(verifier.SecretScanVerificationError) as exc:
        verifier._github_api("/repos/acme/testlookup/actions/workflows/secret-scan.yml", timeout=5)
    assert "secret" not in str(exc.value).replace("secret-scan", "scan")
    assert "Bearer" not in str(exc.value)
    assert seen[0][0][:4] == ["gh", "api", "--method", "GET"]
    assert seen[0][1]["timeout"] == 5


def test_api_error_or_invalid_response_never_authorizes(monkeypatch):
    for stdout in ("not JSON", "[]"):
        monkeypatch.setattr(verifier.subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(command, 0, stdout, ""))
        with pytest.raises(verifier.SecretScanVerificationError, match="Invalid"):
            verifier._github_api("/repos/acme/testlookup/actions/workflows/secret-scan.yml", timeout=1)


def test_incomplete_or_inconsistent_listing_never_authorizes():
    for listing in (
        {"total_count": 1, "workflow_runs": []},
        {"total_count": 0, "workflow_runs": [_run()]},
        {"total_count": None, "workflow_runs": [_run()]},
    ):
        api = FakeAPI([[_run()]])

        def respond(path, **kwargs):
            return listing if "/runs?" in path else api(path, **kwargs)

        with pytest.raises(verifier.SecretScanVerificationError, match="run listing"):
            _verify(respond)


def test_cli_refuses_failed_verification_and_keeps_evidence_out_of_logs(monkeypatch, capsys):
    evidence = {"source_sha": SHA, "run_id": 101, "internal": "synthetic-private-detail"}
    monkeypatch.setattr(verifier, "verify_secret_scan", lambda *args, **kwargs: evidence)
    arguments = ["--repository", REPOSITORY, "--source-sha", SHA]
    assert verifier.main(arguments) == 0
    assert capsys.readouterr().out == f"Exact-SHA scan authorized {SHA}\n"

    def refused(*args, **kwargs):
        raise verifier.SecretScanVerificationError("The latest exact-SHA secret scan did not succeed")

    monkeypatch.setattr(verifier, "verify_secret_scan", refused)
    assert verifier.main(arguments) == 1
    output = capsys.readouterr()
    assert output.out == "" and "authorization refused" in output.err


def _workflow(name):
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


def test_first_image_publication_waits_for_read_only_exact_sha_gate():
    jobs = _workflow("ci.yml")["jobs"]
    gate, build = jobs["verify-secret-scan"], jobs["build-images"]
    assert "verify-secret-scan" in build["needs"]
    assert "security-deps" in build["needs"]
    assert gate["permissions"] == {"contents": "read", "actions": "read"}
    assert "actions" not in build["permissions"]
    assert gate["if"] == build["if"] == "github.ref == 'refs/heads/main' && github.event_name == 'push'"
    assert not gate.get("continue-on-error")
    step = next(s for s in gate["steps"] if "verify_secret_scan.py" in s.get("run", ""))
    assert step["env"]["SOURCE_SHA"] == "${{ github.sha }}"
    assert '--source-sha "$SOURCE_SHA"' in step["run"]
    assert not step.get("if") and not step.get("continue-on-error")
    assert "--timeout-seconds 900" in step["run"] and gate["timeout-minutes"] > 15


def test_release_authorization_rechecks_secret_scan_without_weakening_manifest_gate():
    workflow = _workflow("require-verified-sha.yml")
    assert workflow["permissions"] == {"actions": "read", "contents": "read"}
    steps = workflow["jobs"]["require"]["steps"]
    manifest = next(s for s in steps if s.get("id") == "verify")
    gate = next(s for s in steps if "verify_secret_scan.py" in s.get("run", ""))
    checkout = next(s for s in steps if "actions/checkout@" in s.get("uses", ""))
    assert checkout["with"]["ref"] == "refs/heads/main"
    assert checkout["with"]["persist-credentials"] is False
    assert gate["env"]["SOURCE_SHA"] == "${{ inputs.source_sha }}"
    assert steps.index(manifest) < steps.index(checkout) < steps.index(gate)
    assert "verified-images-$SOURCE_SHA" in manifest["run"]
    assert "Expected exactly one successful main ci.yml run" in manifest["run"]
    assert not gate.get("if") and not gate.get("continue-on-error")
