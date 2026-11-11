"""Fault-focused tests for upgrade gating and evidence provenance."""
import copy
from io import BytesIO
import json
from types import SimpleNamespace
import zipfile

import pytest
import upgrade
import upgrade_baseline


PRIOR = {"source_sha": "a" * 40}
CANDIDATE = {"source_sha": "b" * 40}


class Environment:
    def __init__(self, negative_success=False, positive_success=True, broken_restore=False):
        self.current = PRIOR["source_sha"]
        self.data = {"preserved_id": "fixture-id", "count": 4}
        self.saved = None
        self.calls = []
        self.negative_success = negative_success
        self.positive_success = positive_success
        self.broken_restore = broken_restore

    def install(self):
        self.calls.append("install")

    def observe(self, manifest):
        self.calls.append("observe:" + manifest["source_sha"])
        return {"matched": self.current == manifest["source_sha"]}

    def migrate(self, manifest, negative=False):
        self.calls.append("negative" if negative else "positive")
        return self.negative_success if negative else self.positive_success

    def quiesce(self):
        self.calls.append("quiesce")

    def snapshot(self):
        self.saved = copy.deepcopy(self.data)
        return {key: {"sha256": "c" * 64, "size": 10, "path": "/tmp/owned-snapshot"}
                for key in ("postgres", "mongo", "minio")}

    def activate(self, manifest):
        self.calls.append("activate:" + manifest["source_sha"])
        self.current = manifest["source_sha"]

    def restore(self):
        if not self.broken_restore:
            self.data = copy.deepcopy(self.saved)


def fake_fixture(environment, action, state=None, revision=None):
    if environment.current != revision:
        raise upgrade.ProofFailure("wrong observed revision")
    if action == "seed":
        return {"ok": True, "state": copy.deepcopy(environment.data)}
    if action == "candidate_mark":
        environment.data["candidate_only"] = True
        return {"ok": True, "state": state}
    if environment.data != state:
        raise upgrade.ProofFailure("snapshot did not restore exact data")
    return {"ok": True, "state": state}


def test_failed_negative_control_cannot_reach_candidate_activation(monkeypatch):
    monkeypatch.setattr(upgrade, "fixture", fake_fixture)
    environment = Environment(negative_success=True)
    with pytest.raises(upgrade.ProofFailure, match="fail closed"):
        upgrade.exercise(environment, PRIOR, CANDIDATE, {"steps": []})
    assert "positive" not in environment.calls
    assert not any(call.startswith("activate:") for call in environment.calls)


def test_failed_candidate_migration_cannot_replace_previous_build(monkeypatch):
    monkeypatch.setattr(upgrade, "fixture", fake_fixture)
    environment = Environment(positive_success=False)
    with pytest.raises(upgrade.ProofFailure, match="replacement withheld"):
        upgrade.exercise(environment, PRIOR, CANDIDATE, {"steps": []})
    assert environment.current == PRIOR["source_sha"]
    assert not any(call.startswith("activate:") for call in environment.calls)


def test_merge_restore_cannot_pass_exact_snapshot_oracle(monkeypatch):
    monkeypatch.setattr(upgrade, "fixture", fake_fixture)
    environment = Environment(broken_restore=True)
    with pytest.raises(upgrade.ProofFailure, match="exact data"):
        upgrade.exercise(environment, PRIOR, CANDIDATE, {"steps": []})


def test_true_transition_observes_each_build_and_restores_data(monkeypatch):
    monkeypatch.setattr(upgrade, "fixture", fake_fixture)
    environment = Environment()
    report = {"steps": []}
    upgrade.exercise(environment, PRIOR, CANDIDATE, report)
    assert environment.data == {"preserved_id": "fixture-id", "count": 4}
    assert [x for x in environment.calls if x.startswith("observe:")] == [
        "observe:" + PRIOR["source_sha"], "observe:" + PRIOR["source_sha"],
        "observe:" + CANDIDATE["source_sha"], "observe:" + PRIOR["source_sha"]]
    assert all(step["status"] == "PASSED" for step in report["steps"])


def baseline_files(tmp_path):
    def manifest(sha, digit):
        return {"schema_version": 1, "source_sha": sha, "version": "v0.1.0",
                "images": {name: {"source_ref": f"ghcr.io/example/{name}@sha256:" + upgrade.sha256((name + digit).encode())}
                           for name in ("backend", "frontend", "mcp")}}
    prior = tmp_path / "prior.json"
    prior.write_text(json.dumps(manifest("a" * 40, "1")), encoding="utf-8")
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps(manifest("b" * 40, "2")), encoding="utf-8")
    archive = tmp_path / "images.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("release-manifest.json", prior.read_bytes())
    provenance = tmp_path / "provenance.json"
    provenance.write_text(json.dumps({"repository": "example/project", "artifact": {"id": 7}}))
    artifact = {"id": 7, "name": "verified-images-" + "a" * 40, "expired": False,
                "expires_at": "2099-01-01T00:00:00Z", "digest": "sha256:" + upgrade.sha256(archive.read_bytes()),
                "workflow_run": {"id": 8, "head_sha": "a" * 40}}
    run = {"id": 8, "head_sha": "a" * 40, "head_branch": "main", "event": "push",
           "path": ".github/workflows/ci.yml", "status": "completed", "conclusion": "success", "html_url": "https://example.invalid/run"}
    def query(endpoint):
        if "?name=" in endpoint:
            return {"artifacts": [artifact]}
        return artifact if "/artifacts/" in endpoint else run
    return (prior, candidate, provenance, archive), artifact, run, query


def test_successful_pull_request_ci_is_not_previous_release_provenance(tmp_path, monkeypatch):
    files, artifact, run, query = baseline_files(tmp_path)
    run["event"] = "pull_request"
    monkeypatch.setattr(upgrade.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    with pytest.raises(upgrade.PreconditionError, match="main-push CI"):
        upgrade.verify_baseline(*files, repository="example/project", query=query)


def test_tampered_artifact_archive_is_rejected(tmp_path, monkeypatch):
    files, artifact, run, query = baseline_files(tmp_path)
    files[3].write_bytes(files[3].read_bytes() + b"changed")
    monkeypatch.setattr(upgrade.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    with pytest.raises(upgrade.PreconditionError, match="artifact digest"):
        upgrade.verify_baseline(*files, repository="example/project", query=query)


def test_same_binary_mirrored_to_another_repository_is_not_a_prior_build(tmp_path):
    files, artifact, run, query = baseline_files(tmp_path)
    candidate = json.loads(files[1].read_bytes())
    previous = json.loads(files[0].read_bytes())
    candidate["images"]["backend"]["source_ref"] = previous["images"]["backend"]["source_ref"].replace("example/", "another/")
    files[1].write_text(json.dumps(candidate), encoding="utf-8")
    with pytest.raises(upgrade.PreconditionError, match="distinct prior image"):
        upgrade.verify_baseline(*files, repository="example/project", query=query)


def test_truncated_artifact_listing_cannot_establish_unique_verified_run(tmp_path, monkeypatch):
    files, artifact, run, query = baseline_files(tmp_path)
    monkeypatch.setattr(upgrade.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    def truncated(endpoint):
        return {"total_count": 101, "artifacts": [artifact]} if "?name=" in endpoint else query(endpoint)
    with pytest.raises(upgrade.PreconditionError, match="enumeration is incomplete"):
        upgrade.verify_baseline(*files, repository="example/project", query=truncated)


def test_public_proof_excludes_raw_namespace_fixture_and_host_details():
    report = {"id": "TL-DEPLOY-001", "target": "kubernetes", "status": "PASSED", "started": "now",
        "owned_environment": {"namespace": "private-namespace", "namespace_uid": "private-uid"},
        "cleanup": {"clean": True, "project": "private-project"},
        "steps": [{"name": "observe candidate", "status": "PASSED", "duration_seconds": 1,
            "evidence": {"source_sha": "b" * 40, "build": {"build": {"revision": "b" * 40}},
                "namespace": "private-namespace", "fixture_id": "private-fixture", "host_path": "private-path",
                "matched": True, "images": [{"service": "backend", "pod": "private-pod", "component": "backend",
                    "candidate": "registry/backend@sha256:digest", "running": "sha256:digest", "matched": True}]}}]}
    proof = upgrade.public_proof(report)
    text = json.dumps(proof)
    assert "private-" not in text
    assert proof["cleanup_verified"] is True
    assert proof["observations"][0]["observed_revision"] == "b" * 40
    assert proof["manifest_hash"] is None  # An omitted manifest can never acquire invented provenance.


def test_baseline_resolver_rejects_archive_path_traversal():
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("../release-manifest.json", "{}")
    with pytest.raises(upgrade_baseline.BaselineBlocked, match="exactly one valid"):
        upgrade_baseline._archive_manifest(stream.getvalue(), "a" * 40)


def test_baseline_resolver_rejects_incomplete_paginated_metadata(monkeypatch):
    api = upgrade_baseline.GitHubAPI()
    name = "verified-images-" + "a" * 40
    monkeypatch.setattr(api, "json", lambda *args, **kwargs: [{"total_count": 2, "artifacts": [{"id": 1, "name": name}]}])
    with pytest.raises(upgrade_baseline.BaselineBlocked, match="pagination was incomplete"):
        api.artifacts("example/project", name=name)


@pytest.mark.parametrize("target", ["podman", "kubernetes"])
def test_changed_snapshot_aborts_before_any_destructive_store_command(target, monkeypatch):
    if target == "podman":
        from upgrade_podman import PodmanUpgrade as Adapter
        guard = "_require_quiesced"
    else:
        from upgrade_kubernetes import KubernetesUpgrade as Adapter
        guard = "_guard_stores"
    adapter = object.__new__(Adapter)
    adapter._snapshot = {name: {"path": path, "size": 10, "sha256": "a" * 64}
                         for name, path in Adapter.STORES.items()}
    mutations = []
    monkeypatch.setattr(adapter, guard, lambda: None)
    monkeypatch.setattr(adapter, "_archive_metadata", lambda name, path: {"path": path, "size": 10, "sha256": "b" * 64})
    monkeypatch.setattr(adapter, "_store_exec", lambda *args: mutations.append(args))
    with pytest.raises(RuntimeError, match="checksum"):
        adapter.restore()
    assert mutations == []


def test_uncertain_namespace_creation_cannot_delete_an_unowned_namespace(monkeypatch):
    from upgrade_kubernetes import KubernetesUpgrade
    adapter = object.__new__(KubernetesUpgrade)
    adapter._cleaned = False
    adapter.uid = None
    adapter.namespace = "testlookup-e2e-upgrade-" + "a" * 12
    adapter._namespace_create_attempted = False
    calls = []
    def observed(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"metadata": {
            "uid": "someone-else", "labels": {"testlookup.io/e2e-run": "someone-else"}}}))
    monkeypatch.setattr(adapter, "_k", observed)
    with pytest.raises(RuntimeError, match="ownership"):
        adapter.cleanup()
    assert not any("delete" in args for args in calls)


def test_baseline_graph_distance_matches_git_for_merge_side_ancestors(tmp_path):
    import subprocess

    repository = tmp_path / "graph"
    subprocess.run(["git", "init", "--quiet", str(repository)], check=True, capture_output=True)
    def git(*args, stdin=None):
        return subprocess.run(["git", "-C", str(repository), "-c", "user.name=Fixture",
            "-c", "user.email=fixture@example.test", *args], input=stdin, text=True,
            capture_output=True, check=True).stdout.strip()
    tree = git("mktree", stdin="")
    def commit(label, *parents):
        return git("commit-tree", tree, *[part for parent in parents for part in ("-p", parent)], "-m", label)
    root = commit("root")
    main = commit("main", root)
    side = commit("side", root)
    candidate = commit("merge", main, side)
    ranks, mainline = upgrade_baseline._rank_ancestor_graph(
        git("rev-list", "--parents", "--topo-order", candidate), candidate)
    assert {source for _, _, source in ranks} == {root, main, side}
    assert ranks[0][2] == main  # Equal graph distance uses first-parent order.
    assert side not in mainline
    for distance, _, source in ranks:
        assert distance == int(git("rev-list", "--count", source + ".." + candidate))


def test_baseline_discovery_ignores_unrelated_ci_artifact_churn(tmp_path, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    files, artifact, run, _ = baseline_files(tmp_path)
    previous, candidate = "a" * 40, "b" * 40
    nearer_side = "c" * 40
    graph = f"{candidate} {nearer_side}\n{nearer_side} {previous}\n{previous}"
    artifact["workflow_run"]["head_branch"] = "main"
    artifact["size_in_bytes"] = len(files[3].read_bytes())
    archive_raw = files[3].read_bytes()
    queries = []
    def local_git(*args):
        if args == ("rev-parse", "--is-shallow-repository"):
            return "false"
        assert args == ("rev-list", "--parents", "--topo-order", candidate)
        return graph
    monkeypatch.setattr(upgrade_baseline, "_git", local_git)
    monkeypatch.setattr(upgrade.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    class ExactAPI(upgrade_baseline.GitHubAPI):
        def _request(self, endpoint, *, paginate=False):
            parsed = urlsplit(endpoint)
            if parsed.path == "repos/example/project/actions/artifacts":
                name = parse_qs(parsed.query).get("name", [])
                assert len(name) == 1  # No repository-wide population exists in this simulation.
                queries.append(name[0])
                assert name[0] in {"verified-images-" + previous, "verified-images-" + nearer_side}
                # Unrelated uploads may change on every request; only the
                # exact-name total and population determine completeness.
                items = [artifact] if name[0].endswith(previous) else []
                return json.dumps([{"total_count": len(items), "artifacts": items}]).encode()
            if parsed.path.endswith("/artifacts/7/zip"):
                return archive_raw
            if parsed.path.endswith("/artifacts/7"):
                return json.dumps(artifact).encode()
            assert parsed.path.endswith("/runs/8")
            return json.dumps(run).encode()
    output = tmp_path / "resolved"
    result = upgrade_baseline.resolve(files[1], "example/project", output, api=ExactAPI())
    assert result["status"] == "RESOLVED" and result["previous_source_sha"] == previous
    assert result["selection"]["queried_ancestor_sources"] == 2
    assert queries == ["verified-images-" + nearer_side, "verified-images-" + previous, "verified-images-" + previous]
    assert (output / "release-manifest.json").read_bytes() == files[0].read_bytes()
    assert (output / "verified-images.zip").read_bytes() == archive_raw


def test_exact_name_artifact_upload_race_retries_complete_population(monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    api = upgrade_baseline.GitHubAPI()
    name = "verified-images-" + "a" * 40
    calls = []
    def metadata(endpoint, **kwargs):
        assert parse_qs(urlsplit(endpoint).query)["name"] == [name]
        calls.append(endpoint)
        if len(calls) == 1:
            return [{"total_count": 2, "artifacts": [{"id": 1, "name": name}]}]
        return [{"total_count": 2, "artifacts": [{"id": 1, "name": name}, {"id": 2, "name": name}]}]
    monkeypatch.setattr(api, "json", metadata)
    assert [item["id"] for item in api.artifacts("example/project", name=name)] == [1, 2]
    assert len(calls) == 2


def test_malformed_exact_name_artifact_pagination_remains_blocked(monkeypatch):
    api = upgrade_baseline.GitHubAPI()
    name = "verified-images-" + "a" * 40
    calls = []
    def duplicated(endpoint, **kwargs):
        calls.append(endpoint)
        return [{"total_count": 2, "artifacts": [{"id": 1, "name": name}, {"id": 1, "name": name}]}]
    monkeypatch.setattr(api, "json", duplicated)
    with pytest.raises(upgrade_baseline.BaselineBlocked, match="bounded retries"):
        api.artifacts("example/project", name=name)
    assert len(calls) == 3
