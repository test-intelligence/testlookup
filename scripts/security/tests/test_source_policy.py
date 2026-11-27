"""Non-executed source fixtures drive the actual locked Ruff policy."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys

import pytest

from scripts.security import source_policy as policy

SHA = "a" * 40


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    for directory in policy.ROOTS:
        (tmp_path / directory).mkdir(parents=True)
    source = tmp_path / "backend/app/control.py"
    source.write_text("import requests\nrequests.get('https://service.invalid', verify=True)\n", encoding="utf-8")
    here = tmp_path / "policy"
    here.mkdir()
    (here / "source-policy.toml").write_bytes((policy.HERE / "source-policy.toml").read_bytes())
    (here / "source-policy-exceptions.json").write_text('{"schema_version":1,"exceptions":[]}', encoding="utf-8")
    monkeypatch.setattr(policy, "HERE", here)
    real_run = policy._run

    def controlled_git(argv, root):
        if argv[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(argv, 0, SHA + "\n", "")
        if argv[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ["git", "ls-files"]:
            return subprocess.CompletedProcess(argv, 0, "backend/app/control.py\0", "")
        return real_run(argv, root)

    monkeypatch.setattr(policy, "_run", controlled_git)
    return tmp_path, source, here


@pytest.fixture
def ruff():
    command = [os.environ["SOURCE_POLICY_RUFF"]] if os.environ.get("SOURCE_POLICY_RUFF") else [sys.executable, "-m", "ruff"]
    result = subprocess.run([*command, "--version"], capture_output=True, text=True, check=False)
    assert result.returncode == 0 and result.stdout.strip() == "ruff 0.17.3", "Install the repository's locked Ruff0.17.3 or set SOURCE_POLICY_RUFF to the verified binary"
    return command


def test_healthy_source_passes_with_bound_metadata_and_no_source_contents(candidate, ruff):
    root, _, _ = candidate
    result = policy.analyze(root, SHA, ruff=ruff)
    assert (result["status"], result["candidate_eligible"], result["snapshot_kind"]) == ("PASS", True, "commit")
    assert result["checked_files"] == 1
    assert result["findings"] == result["exceptions"] == []
    assert len(result["source_snapshot_sha256"]) == len(result["policy_sha256"]) == 64
    assert "service.invalid" not in json.dumps(result)


@pytest.mark.parametrize("seed,rule", [
    ("import requests\nrequests.get('https://service.invalid', verify=False)  # noqa: S501\n", "S501"),
    ("import httpx\nhttpx.Client(verify=False)\n", "S501"),
    ("import yaml\nyaml.load('key: value', Loader=yaml.Loader)\n", "S506"),
])
def test_nonexecuted_controlled_source_defect_blocks_even_with_noqa(candidate, ruff, seed, rule):
    root, source, _ = candidate
    source.write_text(seed, encoding="utf-8")
    result = policy.analyze(root, SHA, ruff=ruff)
    assert result["status"] == "FAIL"
    assert [finding["rule"] for finding in result["findings"]] == [rule]
    finding = result["findings"][0]
    assert finding["path"] == "backend/app/control.py"
    assert finding["disposition"] == "blocking"
    assert "verify=False" not in json.dumps(result)
    assert "service.invalid" not in json.dumps(result)


def test_exact_source_exception_retains_owner_and_expiry_and_cannot_outlive_change(candidate, ruff):
    root, source, here = candidate
    source.write_text("import requests\nrequests.get('https://service.invalid', verify=False)\n", encoding="utf-8")
    result = policy.analyze(root, SHA, ruff=ruff)
    exception = {"fingerprint": result["findings"][0]["fingerprint"], "owner": "AppSec", "reason": "Synthetic fixture only", "expires": (dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=7)).isoformat()}
    (here / "source-policy-exceptions.json").write_text(json.dumps({"schema_version": 1, "exceptions": [exception]}), encoding="utf-8")
    accepted = policy.analyze(root, SHA, ruff=ruff)
    assert accepted["status"] == "PASS"
    assert accepted["exceptions"] == [exception]
    assert accepted["findings"][0]["disposition"] == "accepted_until_expiry"
    source.write_text(source.read_text() + "# changed source\n", encoding="utf-8")
    with pytest.raises(policy.PolicyError, match="exception_stale"):
        policy.analyze(root, SHA, ruff=ruff)


@pytest.mark.parametrize("days", [-1, 91])
def test_expired_and_unbounded_exceptions_fail_closed(days):
    today = dt.date(2026, 11, 27)
    finding = {"fingerprint": "a" * 64, "disposition": "blocking"}
    exception = {"fingerprint": "a" * 64, "owner": "AppSec", "reason": "Synthetic fixture", "expires": (today + dt.timedelta(days=days)).isoformat()}
    with pytest.raises(policy.PolicyError, match="exception_expiry_out_of_policy"):
        policy._exceptions({"schema_version": 1, "exceptions": [exception]}, [finding], today)


def test_wrong_sha_and_dirty_candidate_are_not_candidate_evidence(candidate, ruff, monkeypatch):
    root, _, _ = candidate
    with pytest.raises(policy.PolicyError, match="source_sha_mismatch"):
        policy.analyze(root, "b" * 40, ruff=ruff)
    actual = policy._run

    def dirty(argv, directory):
        if argv[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(argv, 0, " M backend/app/control.py\n", "")
        return actual(argv, directory)

    monkeypatch.setattr(policy, "_run", dirty)
    with pytest.raises(policy.PolicyError, match="candidate_source_dirty"):
        policy.analyze(root, SHA, ruff=ruff)
    diagnostic = policy.analyze(root, SHA, ruff=ruff, allow_dirty=True)
    assert (diagnostic["status"], diagnostic["candidate_eligible"], diagnostic["snapshot_kind"]) == ("WORKTREE_PASS", False, "worktree")


def test_ignored_untracked_source_is_not_commit_bound(candidate, ruff):
    root, _, _ = candidate
    (root / "mcp/ignored-local-source.py").write_text("value = 1\n", encoding="utf-8")
    with pytest.raises(policy.PolicyError, match="candidate_source_dirty"):
        policy.analyze(root, SHA, ruff=ruff)


@pytest.mark.parametrize("returncode,output,error", [(2, "private raw source", "analyzer_execution_failed"), (0, "not json private source", "analyzer_output_invalid")])
def test_tool_error_never_becomes_a_pass_or_source_leak(candidate, ruff, monkeypatch, returncode, output, error):
    root, _, _ = candidate
    actual = policy._run

    def broken(argv, directory):
        if "check" in argv:
            return subprocess.CompletedProcess(argv, returncode, output, "private diagnostic")
        return actual(argv, directory)

    monkeypatch.setattr(policy, "_run", broken)
    with pytest.raises(policy.PolicyError) as raised:
        policy.analyze(root, SHA, ruff=ruff)
    assert str(raised.value) == error
    assert "private" not in str(raised.value)


def test_policy_rules_and_engine_version_match_existing_dependency_lock():
    import tomllib

    config = tomllib.loads((policy.HERE / "source-policy.toml").read_text(encoding="utf-8"))
    assert set(config["lint"]["select"]) == policy.RULES
    version = config["required-version"].removeprefix("==")
    assert f"ruff=={version}" in (policy.ROOT / "backend/requirements-lock.txt").read_text(encoding="utf-8")
    assert "ruff==" + version in (policy.ROOT / "backend/requirements.txt").read_text(encoding="utf-8")
