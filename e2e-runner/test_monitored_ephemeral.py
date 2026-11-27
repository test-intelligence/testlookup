"""Offline orchestration contracts, with an optional real Compose render."""
from __future__ import annotations

import io
import hashlib
import json
import os
import shutil
import tarfile
from types import SimpleNamespace
import uuid

import httpx
import pytest

import monitored_ephemeral as harness
import monitored_signal_entry as entry
import monitored_signal_seed as seed
import monitored_signal_beat as beat

PROJECT = "tl-e2e-" + "a" * 12
MANIFEST = {
    "schema_version": 1, "source_sha": "a" * 40, "version": "0.1.0",
    "images": {component: {"source_ref": f"example.invalid/testlookup/{component}@sha256:" + digit * 64}
               for component, digit in (("backend", "1"), ("frontend", "2"), ("mcp", "3"))},
}


def rendered(directory, project=PROJECT):
    return {
        "name": project,
        "services": {
            **{role: {"image": MANIFEST["images"]["mcp" if role == "mcp" else "backend"]["source_ref"],
                       "ports": ["0.0.0.0:8000:8000"], "container_name": "old-global-name", "networks": {"testlookup_net": {}, "chroma_net": {}},
                       "environment": {"CELERY_BROKER_URL": "redis://synthetic-broker", "CELERY_RESULT_BACKEND": "redis://synthetic-results"}}
               for role in harness.ROLES},
            "loki": {"image": "example.invalid/loki@sha256:" + "4" * 64, "volumes": [{"type": "bind", "source": str(directory / "loki.yml")}]},
            "beat": {"image": MANIFEST["images"]["backend"]["source_ref"]},
        },
        "networks": {name: {"internal": False} for name in (*harness.NETWORKS, "chroma_net")},
        "volumes": {"database": {}}, "secrets": {"metrics": {"file": str(directory / "token")}},
    }


def test_rendered_topology_has_no_published_services_or_application_query_access(tmp_path):
    config = harness.restrict_rendered(rendered(tmp_path), tmp_path, MANIFEST, PROJECT)
    assert set(config["services"]["beat"]["networks"]) == {"testlookup_net"}
    assert all("ports" not in service and "container_name" not in service for service in config["services"].values())
    for role in harness.ROLES:
        assert set(config["services"][role]["networks"]) == {"testlookup_net", "telemetry_ingress"}
    assert all(value["internal"] and value["name"].startswith(PROJECT + "_") for value in config["networks"].values())
    assert config["volumes"]["database"]["name"] == PROJECT + "_database"


@pytest.mark.parametrize("change", ["external_network", "external_volume", "foreign_bind", "foreign_secret", "floating_image", "privileged", "candidate_mismatch"])
def test_rendered_unsafe_or_unbound_inputs_fail_before_start(tmp_path, change):
    config = rendered(tmp_path)
    if change == "external_network":
        config["networks"]["testlookup_net"]["external"] = True
    elif change == "external_volume":
        config["volumes"]["database"]["external"] = True
    elif change == "foreign_bind":
        config["services"]["loki"]["volumes"][0]["source"] = str(tmp_path.parent / "site-config")
    elif change == "foreign_secret":
        config["secrets"]["metrics"]["file"] = str(tmp_path.parent / "site-token")
    elif change == "floating_image":
        config["services"]["loki"]["image"] = "example.invalid/loki:latest"
    elif change == "privileged":
        config["services"]["backend"]["privileged"] = True
    else:
        config["services"]["backend"]["image"] = MANIFEST["images"]["frontend"]["source_ref"]
    with pytest.raises(harness.HarnessError):
        harness.restrict_rendered(config, tmp_path, MANIFEST, PROJECT)


def test_archive_uses_exact_sha_and_never_a_site_environment_file(tmp_path, monkeypatch):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path in harness.ARCHIVE_PATHS:
            if path == "infra/monitoring":
                continue
            payload = b"candidate-source"
            member = tarfile.TarInfo(path)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    calls = []
    monkeypatch.setattr(harness, "command", lambda argv, **kwargs: calls.append(argv) or buffer.getvalue())
    digest = harness.archive_candidate(MANIFEST["source_sha"], tmp_path)
    assert len(digest) == 64
    assert calls[0][:4] == ["git", "archive", "--format=tar", MANIFEST["source_sha"]]
    assert not any(path.endswith(".env") for path in calls[0])
    assert (tmp_path / "scripts/monitoring/release_signal_gate.py").is_file()


def test_gate_container_uses_backend_digest_and_exact_private_query_networks(tmp_path):
    argv = harness.gate_container("podman", PROJECT, MANIFEST["images"]["backend"]["source_ref"], tmp_path / "source", tmp_path / "proof", tmp_path / "private.env")
    assert {argv[index + 1] for index, value in enumerate(argv) if value == "--network"} == {
        PROJECT + "_testlookup_net", PROJECT + "_telemetry_storage", PROJECT + "_telemetry_metrics",
    }
    assert "--read-only" in argv and "--cap-drop=ALL" in argv
    assert MANIFEST["images"]["backend"]["source_ref"] in argv
    assert not any("TESTLOOKUP_API_KEY=" in value for value in argv)
    assert str(tmp_path / "private.env") in argv


def test_rootless_podman_keeps_caller_identity_for_private_bind_mounts(tmp_path, monkeypatch):
    monkeypatch.setattr(harness.os, "getuid", lambda: 1234, raising=False)
    monkeypatch.setattr(harness.os, "getgid", lambda: 1234, raising=False)
    args = (PROJECT, MANIFEST["images"]["backend"]["source_ref"], tmp_path / "source", tmp_path / "proof", tmp_path / "private.env")
    podman = harness.gate_container("podman", *args)
    docker = harness.gate_container("docker", *args)
    assert "--userns=keep-id" in podman
    assert podman[podman.index("--user") + 1] == "1234:1234"
    assert "--userns=keep-id" not in docker


@pytest.mark.parametrize("name", ["../outside", "C:/outside", "C:outside", "..\\outside"])
def test_archive_rejects_parent_and_windows_drive_qualified_members(tmp_path, monkeypatch, name):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo(name)
        member.size = 1
        archive.addfile(member, io.BytesIO(b"x"))
    monkeypatch.setattr(harness, "command", lambda *args, **kwargs: buffer.getvalue())
    with pytest.raises(harness.HarnessError, match="archive_member_invalid"):
        harness.archive_candidate(MANIFEST["source_sha"], tmp_path)


def test_seed_uses_normal_routes_and_a_project_bound_restricted_engineer_key():
    project_id, engineer_id = str(uuid.uuid4()), str(uuid.uuid4())
    requests = []

    def reply(request):
        requests.append(request)
        path = request.url.path
        if path.endswith("/auth/dev-login"):
            body = {"access_token": "synthetic-" + request.url.params["role"]}
        elif path.endswith("/auth/me"):
            body = {"id": engineer_id, "role": "QA_ENGINEER"}
        elif path.endswith("/projects"):
            assert json.loads(request.content)["slug"].startswith("release-signal-")
            body = {"id": project_id}
        elif path.endswith("/members"):
            assert json.loads(request.content) == {"user_id": engineer_id, "role": "QA_ENGINEER"}
            body = {"id": engineer_id}
        else:
            payload = json.loads(request.content)
            assert payload["project_id"] == project_id and payload["target_user_id"] == engineer_id
            assert payload["expires_days"] == 1 and set(payload["scopes"]) == {"project:write", "stream:write"}
            body = {"project_id": project_id, "scopes": payload["scopes"], "raw_key": "synthetic-private-key"}
        status = 201 if path.endswith(("/projects", "/members", "/keys")) else 200
        return httpx.Response(status, json=body)

    with httpx.Client(base_url="http://backend/api/v1/", transport=httpx.MockTransport(reply)) as client:
        result = seed.seed(client)
    assert result == {"project_id": project_id, "api_key": "synthetic-private-key"}
    assert len(requests) == 6
    assert all(request.url.host == "backend" for request in requests)


def test_cleanup_is_project_scoped_and_checks_all_resource_types(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(harness.subprocess, "run", lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(harness, "command", lambda argv, **kwargs: calls.append(argv) or b"")
    harness.cleanup("podman", PROJECT, lambda *args, **kwargs: calls.append(list(args)), cwd=tmp_path, env={})
    assert calls[0] == ["podman", "rm", "--force", PROJECT + "-signal-gate"]
    assert calls[1][:3] == ["down", "--volumes", "--remove-orphans"]
    assert {argv[1] for argv in calls[2:]} == {"container", "volume", "network"}
    assert all("label=com.docker.compose.project=" + PROJECT in argv for argv in calls[2:])


@pytest.mark.skipif(not shutil.which("docker"), reason="Docker Compose CLI not installed")
def test_real_compose_renders_reference_overlays_without_a_running_engine(tmp_path, monkeypatch):
    # No application/provider fixture or OpenSSL container may run during
    # preparation. Only the actual Compose config command below is allowed.
    def unexpected_fixture(*_args, **_kwargs):
        raise AssertionError("monitored preparation must not configure browser/provider fixtures")

    actual_run = harness.subprocess.run
    commands = []

    def only_render(argv, *args, **kwargs):
        assert argv[:2] == ["docker", "compose"] and "config" in argv, "preparation must not invoke a container"
        commands.append(argv)
        return actual_run(argv, *args, **kwargs)

    monkeypatch.setattr(harness.subprocess, "run", only_render)
    monkeypatch.setattr(harness.ephemeral, "configure_fixtures", unexpected_fixture)
    settings, files = harness.prepare(tmp_path, harness.ROOT, MANIFEST, PROJECT, "docker")
    assert settings["AI_OFFLINE_MODE"] == "true"
    assert "release-fixture" not in json.loads((tmp_path / "compose.json").read_bytes())["services"]
    assert not (tmp_path / "release-fixture").exists()
    env = {key: os.environ[key] for key in harness.SAFE_ENGINE_ENV if key in os.environ}
    env.update(settings)
    argv = ["docker", "compose", "--project-name", PROJECT]
    for path in files:
        argv.extend(["-f", path])
    raw = harness.command([*argv, "config", "--format=json"], cwd=tmp_path, env=env)
    config = harness.restrict_rendered(json.loads(raw), tmp_path, MANIFEST, PROJECT)
    assert all("ports" not in service for service in config["services"].values())
    assert set(config["services"]["backend"]["networks"]) == {"testlookup_net", "telemetry_ingress"}
    assert config["services"]["backend"]["environment"]["METRICS_TOKEN_FILE"] == "/run/secrets/testlookup-metrics-token"
    for role in ("backend", "worker", "worker-children"):
        environment = config["services"][role]["environment"]
        assert environment["OBSERVABILITY_INSTALLATION"] == settings["OBSERVABILITY_INSTALLATION"]
        assert environment["OTEL_ENABLED"] == "true"
        assert "BUILD_REVISION" not in environment and "APP_VERSION" not in environment
    assert config["services"]["mcp"]["environment"]["TESTLOOKUP_TELEMETRY_INSTALLATION"] == settings["OBSERVABILITY_INSTALLATION"]
    assert config["services"]["beat"]["command"] == ["python", "/signal-harness/monitored_signal_beat.py"]
    assert config["services"]["beat"]["healthcheck"]["disable"] is True
    assert len(commands) == 1


def test_ordinary_release_preparation_preserves_fixture_default(tmp_path, monkeypatch):
    from unittest.mock import Mock

    configure = Mock()
    monkeypatch.setattr(harness.ephemeral, "configure_fixtures", configure)
    harness.ephemeral.prepare(tmp_path, MANIFEST, PROJECT, "docker")
    configure.assert_called_once()
    assert configure.call_args.args[3] == MANIFEST["images"]["backend"]["source_ref"]


def test_only_relay_entry_preserves_its_name_interval_and_routing():
    entry = {"task": beat.RELAY_TASK, "schedule": object(), "options": {"queue": "default", "expires": 55}}
    schedule = {"candidate-defined-name": entry, "unrelated-maintenance": {"task": "other.task", "schedule": object()}}
    selected = beat.relay_schedule(schedule)
    assert len(selected) == 1
    assert selected == {"candidate-defined-name": entry}
    assert selected["candidate-defined-name"] is entry
    with pytest.raises(ValueError, match="exactly one"):
        beat.relay_schedule({})


def test_preflight_checks_only_fixed_private_read_endpoints():
    paths = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps({"data": {"activeTargets": [{"scrapePool": job, "health": "up"} for job in ("testlookup-backend", "testlookup-worker", "testlookup-worker")]}}).encode()

    class Opener:
        def open(self, url, **kwargs):
            paths.append(url)
            return Response()

    assert entry.ready(Opener()) is True
    assert len(paths) == 6
    assert all(path.startswith(("http://backend:", "http://prometheus:", "http://loki:", "http://jaeger:", "http://otel-collector:")) for path in paths)


@pytest.mark.parametrize("fail_stage", [None, "startup", "seed", "runtime_identity", "signal_gate", "cleanup"])
def test_main_keeps_sanitized_result_and_runs_cleanup_after_every_started_path(tmp_path, monkeypatch, fail_stage):
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(MANIFEST), encoding="utf-8")
    out = tmp_path / "release-results/signal-gate.json"
    calls = []
    private_project = []

    def archive(_sha, target):
        (target / "e2e-runner").mkdir()
        (target / "e2e-runner/bootstrap_admin.py").write_text("bootstrap_fixture", encoding="utf-8")
        return "a" * 64

    def prepare(directory, source, manifest, project, engine):
        private_project.append(project)
        (directory / "compose.json").write_text("{}", encoding="utf-8")
        return {"OBSERVABILITY_INSTALLATION": "synthetic-installation"}, [str(directory / "compose.json")]

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["podman", "rm"]:
            return SimpleNamespace(returncode=0)
        if b"def seed" in kwargs.get("input", b""):
            if fail_stage == "seed":
                raise harness.HarnessError("synthetic_seed_failure")
            return SimpleNamespace(stdout=json.dumps({"project_id": str(uuid.uuid4()), "api_key": "synthetic-private-key"}).encode(), returncode=0)
        return SimpleNamespace(stdout=b"", returncode=0)

    def command(argv, **kwargs):
        calls.append(argv)
        project = private_project[0]
        # The project identity is durable before the first startup operation.
        assert json.loads(out.read_text())["compose_project"] == project
        if "config" in argv:
            return json.dumps(rendered(kwargs["cwd"], project)).encode()
        if "up" in argv and fail_stage == "startup":
            raise harness.HarnessError("synthetic_start_failure")
        if "down" in argv and fail_stage == "cleanup":
            raise harness.HarnessError("synthetic_cleanup_failure")
        if any("inspect_compose_candidate.py" in value for value in argv):
            if fail_stage == "runtime_identity":
                raise harness.HarnessError("synthetic_identity_failure")
            target = argv[argv.index("--output") + 1]
            from pathlib import Path
            Path(target).write_text(json.dumps({"schema_version": 1, "synthetic_unit_fixture": True}), encoding="utf-8")
        if "--entrypoint" in argv:
            if fail_stage == "signal_gate":
                raise harness.HarnessError("synthetic_signal_failure")
            from pathlib import Path
            mount = next(value for value in argv if value.endswith(":/proof"))
            proof = Path(mount.removesuffix(":/proof"))
            signal = {"schema_version": 1, "source_sha": MANIFEST["source_sha"], "profile": "compose", "status": "passed", "synthetic_unit_fixture": True,
                      "manifest_sha256": hashlib.sha256((proof / "release-manifest.json").read_bytes()).hexdigest(),
                      "runtime_proof_sha256": hashlib.sha256((proof / "runtime-proof.json").read_bytes()).hexdigest(), "checked": ["synthetic_unit_fixture"]}
            (proof / "release-signal-proof.json").write_text(json.dumps(signal), encoding="utf-8")
        return b""

    monkeypatch.setattr(harness, "archive_candidate", archive)
    monkeypatch.setattr(harness, "prepare", prepare)
    monkeypatch.setattr(harness.subprocess, "run", run)
    monkeypatch.setattr(harness, "command", command)
    code = harness.main(["--manifest", str(manifest_file), "--out", str(out), "--engine", "podman"])
    result = json.loads(out.read_text())
    assert code == (0 if fail_stage is None else 1)
    assert result["status"] == ("passed" if fail_stage is None else "failed" if fail_stage == "cleanup" else "blocked")
    assert result["cleanup"] == ("failed" if fail_stage == "cleanup" else "completed")
    assert any("down" in argv for argv in calls)
    assert "synthetic-private-key" not in out.read_text()
    assert not any("synthetic-private-key" in " ".join(argv) for argv in calls)


def test_successful_child_result_must_match_both_proof_files_and_candidate():
    raw, runtime = b"manifest", b"runtime"
    signal = {"schema_version": 1, "status": "passed", "profile": "compose", "source_sha": MANIFEST["source_sha"],
              "manifest_sha256": hashlib.sha256(raw).hexdigest(), "runtime_proof_sha256": hashlib.sha256(runtime).hexdigest(), "checked": ["observed"]}
    harness.validate_signal_result(signal, MANIFEST, raw, runtime)
    for field in ("source_sha", "manifest_sha256", "runtime_proof_sha256", "profile"):
        with pytest.raises(harness.HarnessError, match="binding_failed"):
            harness.validate_signal_result({**signal, field: "different"}, MANIFEST, raw, runtime)
