"""Opt-in monitored candidate proof in one disposable private Compose project."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid

import yaml

import ephemeral
from release_manifest import validate_manifest

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_PATHS = (
    "docker-compose.release.yml", "docker-compose.monitoring.yml", "docker-compose.telemetry.yml",
    "docker-compose.monitoring-auth.yml", "infra/monitoring", "scripts/init-db.sql",
    "e2e-runner/bootstrap_admin.py", "scripts/monitoring/release_signal_gate.py",
    "scripts/monitoring/inspect_compose_candidate.py", "scripts/release/release_manifest.py",
    "scripts/release/verify_kubernetes_rollout.py",
)
ROLES = ("backend", "worker", "worker-children", "mcp")
NETWORKS = ("testlookup_net", "telemetry_ingress", "telemetry_storage", "telemetry_metrics")
SAFE_ENGINE_ENV = (
    "PATH", "HOME", "USERPROFILE", "SystemRoot", "WINDIR", "TEMP", "TMP", "XDG_RUNTIME_DIR",
    "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "CONTAINER_HOST", "CONTAINER_CONNECTION",
    "CONTAINERS_CONF", "CONTAINERS_STORAGE_CONF", "REGISTRY_AUTH_FILE", "SSH_AUTH_SOCK",
)
GATE_ENV_KEYS = (
    "TESTLOOKUP_DISPOSABLE_SIGNAL_GATE", "TESTLOOKUP_API_URL", "TESTLOOKUP_PROMETHEUS_URL",
    "TESTLOOKUP_LOKI_URL", "TESTLOOKUP_JAEGER_URL", "TESTLOOKUP_MCP_URL", "TESTLOOKUP_API_KEY",
    "TESTLOOKUP_SIGNAL_PROJECT_ID", "OBSERVABILITY_INSTALLATION", "APP_ENV",
    "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND",
)


class HarnessError(Exception):
    """A public finite stage code, with no command output or credentials."""


def command(argv, *, cwd, env=None, timeout=300):
    try:
        return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, timeout=timeout, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        raise HarnessError("command_failed") from None


def archive_candidate(source_sha: str, destination: Path) -> str:
    if not re.fullmatch(r"[a-f0-9]{40}", source_sha):
        raise HarnessError("invalid_source_sha")
    raw = command(["git", "archive", "--format=tar", source_sha, "--", *ARCHIVE_PATHS], cwd=ROOT)
    if len(raw) > 134_217_728:
        raise HarnessError("archive_budget_exceeded")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        members = archive.getmembers()
        if len(members) > 10_000:
            raise HarnessError("archive_budget_exceeded")
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                raise HarnessError("archive_member_invalid")
            target = destination.joinpath(*path.parts).resolve()
            if not target.is_relative_to(destination.resolve()) or "\\" in member.name or ":" in member.name:
                raise HarnessError("archive_member_invalid")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output:
                    output.write(archive.extractfile(member).read())
    required = [value for value in ARCHIVE_PATHS if value != "infra/monitoring"]
    if any(not (destination / path).is_file() for path in required):
        raise HarnessError("candidate_missing_monitoring_harness_inputs")
    return hashlib.sha256(raw).hexdigest()


def private_file(path: Path, value: str | bytes, *, mode=0o600):
    path.touch(mode=0o600, exist_ok=False)
    path.write_bytes(value.encode() if isinstance(value, str) else value)
    path.chmod(mode)


def overlay(project: str, installation: str) -> dict:
    environment = {
        "APP_ENV": "development", "OBSERVABILITY_INSTALLATION": installation, "OTEL_ENABLED": "true",
        "OTEL_TRACES_SAMPLER": "parentbased_always_on", "OTEL_TRACES_SAMPLER_ARG": "1.0",
    }
    services = {}
    for role in ROLES:
        services[role] = {"networks": {"testlookup_net": {}, "telemetry_ingress": {}}}
        services[role]["environment"] = ({
            "TESTLOOKUP_TELEMETRY_INSTALLATION": installation, "TESTLOOKUP_TELEMETRY_ENVIRONMENT": "development",
        } if role == "mcp" else environment)
    # Probe traffic and the stores remain on private project bridges. The
    # application containers never join the telemetry storage/query networks.
    services["prometheus"] = {"networks": {"testlookup_net": {}, "telemetry_metrics": {}}}
    return {"name": project, "services": services, "networks": {name: {"internal": True} for name in NETWORKS}}


def prepare(directory: Path, source: Path, manifest: dict, project: str, engine: str) -> tuple[dict, list[str]]:
    old_app_root = ephemeral.ROOT
    try:
        ephemeral.ROOT = source
        settings = ephemeral.prepare(directory, manifest, project, engine, with_fixtures=False)
    finally:
        ephemeral.ROOT = old_app_root
    shutil.copytree(source / "infra", directory / "infra")
    # Only the existing downstream relay may run periodically in this fixture.
    # Its task name, routing and interval come from the candidate's own schedule.
    base = json.loads((directory / "compose.json").read_bytes())
    beat = yaml.safe_load((source / "docker-compose.release.yml").read_text(encoding="utf-8"))["services"]["beat"]
    beat["image"] = manifest["images"]["backend"]["source_ref"]
    beat.pop("container_name", None)
    beat["command"] = ["python", "/signal-harness/monitored_signal_beat.py"]
    beat["healthcheck"] = {"disable": True}
    beat["read_only"] = True
    beat["tmpfs"] = ["/tmp:rw,nosuid,size=64m"]
    beat["volumes"] = ["./scripts/monitored_signal_beat.py:/signal-harness/monitored_signal_beat.py:ro"]
    shutil.copyfile(ROOT / "e2e-runner/monitored_signal_beat.py", directory / "scripts/monitored_signal_beat.py")
    base["services"]["beat"] = beat
    (directory / "compose.json").write_text(json.dumps(base), encoding="utf-8")
    installation = "release-signal-" + project.removeprefix("tl-e2e-")
    token = secrets.token_hex(32) + "\n"
    # A 0700 parent protects host copies. Read-only bind files must also be
    # readable by the different API/Prometheus runtime UIDs.
    private_file(directory / "metrics-api.token", token, mode=0o444)
    private_file(directory / "metrics-prometheus.token", token, mode=0o444)
    settings.update({
        "OBSERVABILITY_INSTALLATION": installation, "APP_ENV": "development",
        "GF_SECURITY_ADMIN_PASSWORD": secrets.token_hex(24),
        "METRICS_API_TOKEN_SOURCE": str(directory / "metrics-api.token"),
        "METRICS_PROMETHEUS_TOKEN_SOURCE": str(directory / "metrics-prometheus.token"),
        "COMPOSE_PARALLEL_LIMIT": "1",
    })
    with (directory / ".env").open("a", encoding="utf-8") as stream:
        stream.write("".join(f"{key}={value}\n" for key, value in settings.items()))
    private_file(directory / "signal-overlay.json", json.dumps(overlay(project, installation)))
    files = [str(directory / "compose.json"), *[
        str(source / name) for name in ("docker-compose.monitoring.yml", "docker-compose.telemetry.yml", "docker-compose.monitoring-auth.yml")
    ], str(directory / "signal-overlay.json")]
    return settings, files


def restrict_rendered(config: dict, directory: Path, manifest: dict, project: str) -> dict:
    """Enforce the private test topology after Compose resolves real overlays."""
    if config.get("name") != project:
        raise HarnessError("compose_project_mismatch")
    services = config["services"]
    for name, service in services.items():
        service.pop("ports", None)
        service.pop("container_name", None)
        service["restart"] = "no"
        if service.get("build") or service.get("privileged") or service.get("network_mode") not in {None, "none"} or service.get("external_links"):
            raise HarnessError("unsafe_disposable_service")
        if "@sha256:" not in service.get("image", ""):
            raise HarnessError("unpinned_disposable_image")
        if name in (*ROLES, "beat"):
            component = "mcp" if name == "mcp" else "backend"
            if service["image"] != manifest["images"][component]["source_ref"]:
                raise HarnessError("application_candidate_mismatch")
            service["networks"] = {"testlookup_net": {}, **({"telemetry_ingress": {}} if name != "beat" else {})}
        for mount in service.get("volumes", []):
            if mount.get("type") == "bind":
                path = Path(mount["source"]).resolve()
                if not path.is_relative_to(directory.resolve()):
                    raise HarnessError("bind_outside_private_scratch")
    for name, network in config.get("networks", {}).items():
        if network.get("external") or network.get("driver") not in {None, "bridge"}:
            raise HarnessError("external_network_forbidden")
        network["internal"] = True
        network["name"] = f"{project}_{name}"
    for name, volume in config.get("volumes", {}).items():
        if volume.get("external") or volume.get("driver_opts") or volume.get("driver") not in {None, "local"}:
            raise HarnessError("external_volume_forbidden")
        volume["name"] = f"{project}_{name}"
    for secret in config.get("secrets", {}).values():
        if not secret.get("file") or not Path(secret["file"]).resolve().is_relative_to(directory.resolve()):
            raise HarnessError("secret_outside_private_scratch")
    return config


def gate_container(engine: str, project: str, candidate: str, source: Path, proof: Path) -> list[str]:
    result = [engine, "run", "--rm", "--name", project + "-signal-gate", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--memory=512m", "--cpus=1", "--pids-limit=64", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--label", "com.docker.compose.project=" + project]
    for network in ("testlookup_net", "telemetry_storage", "telemetry_metrics"):
        result.extend(["--network", project + "_" + network])
    if hasattr(os, "getuid"):
        if engine == "podman":
            result.append("--userns=keep-id")
        result.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
    # The engine copies only these named values from its process environment.
    # Pass gate credentials without another file or credential values in argv.
    for key in GATE_ENV_KEYS:
        result.extend(["--env", key])
    result.extend(["-v", f"{source}:/signal-source:ro", "-v", f"{proof}:/proof", "--entrypoint", "python", candidate,
                   "/signal-source/e2e-runner/monitored_signal_entry.py", "--manifest", "/proof/release-manifest.json", "--runtime-proof", "/proof/runtime-proof.json", "--out", "/proof/release-signal-proof.json"])
    return result


def cleanup(engine: str, project: str, invoke, *, cwd: Path, env: dict):
    if not re.fullmatch(r"tl-e2e-[a-f0-9]{12}", project):
        raise HarnessError("invalid_cleanup_project")
    subprocess.run([engine, "rm", "--force", project + "-signal-gate"], capture_output=True, timeout=30, env=env)
    invoke("down", "--volumes", "--remove-orphans", "--timeout", "10", timeout=120)
    for kind in ("container", "volume", "network"):
        args = [engine, kind, "ls"]
        if kind == "container":
            args.append("--all")
        args.extend(["--filter", "label=com.docker.compose.project=" + project, "--quiet"])
        if command(args, cwd=cwd, env=env, timeout=30).strip():
            raise HarnessError("disposable_resources_remain")


def write_result(path: Path, result: dict, *, initial=False):
    if initial:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, indent=2) + "\n")
    else:
        pending = path.with_name(path.name + ".tmp")
        with pending.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, indent=2) + "\n")
        pending.replace(path)


def validate_signal_result(signal: dict, manifest: dict, manifest_raw: bytes, runtime_raw: bytes):
    if (
        signal.get("schema_version") != 1 or signal.get("status") != "passed"
        or signal.get("source_sha") != manifest["source_sha"] or signal.get("profile") != "compose"
        or signal.get("manifest_sha256") != hashlib.sha256(manifest_raw).hexdigest()
        or signal.get("runtime_proof_sha256") != hashlib.sha256(runtime_raw).hexdigest()
        or not isinstance(signal.get("checked"), list) or not signal["checked"]
    ):
        raise HarnessError("signal_proof_binding_failed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--engine", choices=("podman", "docker"), default="podman")
    parser.add_argument("--startup-timeout", type=int, default=600)
    args = parser.parse_args(argv)
    evidence = args.out.with_name(args.out.stem + "-evidence")
    if args.out.exists() or evidence.exists() or args.out.suffix != ".json" or not 30 <= args.startup_timeout <= 1800:
        parser.error("use a fresh JSON output and a startup timeout from 30 to 1800 seconds")
    raw = args.manifest.read_bytes()
    manifest = validate_manifest(json.loads(raw))
    project = "tl-e2e-" + uuid.uuid4().hex[:12]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    evidence.mkdir(mode=0o700)
    result = {"schema_version": 1, "profile": "compose", "source_sha": manifest["source_sha"], "manifest_sha256": hashlib.sha256(raw).hexdigest(), "compose_project": project, "status": "blocked", "cleanup": "pending", "evidence_directory": evidence.name}
    result["orchestrator_sha256"] = hashlib.sha256(b"\0".join((ROOT / "e2e-runner" / name).read_bytes() for name in ("monitored_ephemeral.py", "monitored_signal_seed.py", "monitored_signal_entry.py", "monitored_signal_beat.py"))).hexdigest()
    # Persist the generated project identity before any container can start.
    write_result(args.out, result, initial=True)
    stage, invoke = "prepare", None
    with tempfile.TemporaryDirectory(prefix=project) as temporary:
        private = Path(temporary)
        source, directory, proof = (private / name for name in ("source", "stack", "proof"))
        for path in (source, directory, proof):
            path.mkdir(mode=0o700)
        try:
            result["source_archive_sha256"] = archive_candidate(manifest["source_sha"], source)
            # This entrypoint belongs to the current harness. Its orchestration
            # digest is separate from the archived application's signal checker.
            shutil.copyfile(ROOT / "e2e-runner/monitored_signal_entry.py", source / "e2e-runner/monitored_signal_entry.py")
            settings, files = prepare(directory, source, manifest, project, args.engine)
            env = {key: os.environ[key] for key in SAFE_ENGINE_ENV if key in os.environ}
            env.update(settings)
            compose = [args.engine, "compose", "--project-name", project]
            for path in files:
                compose.extend(["-f", path])
            stage = "compose_configuration"
            config = restrict_rendered(json.loads(command([*compose, "config", "--format=json"], cwd=directory, env=env)), directory, manifest, project)
            private_file(directory / "monitored-compose.json", json.dumps(config))
            compose = [args.engine, "compose", "--project-name", project, "-f", str(directory / "monitored-compose.json")]

            def invoke(*values, **kwargs):
                return command([*compose, *values], cwd=directory, env=env, **kwargs)

            stage = "startup"
            invoke("up", "-d", "--wait", "--wait-timeout", str(args.startup_timeout), "frontend", "worker", "worker-children", "mcp", "beat", "prometheus", "otel-collector", "loki", "jaeger", timeout=args.startup_timeout + 60)
            stage = "bootstrap_admin"
            # Seed code and bootstrap run inside the isolated backend, with its
            # locked dependencies and generated environment only.
            bootstrap = (source / "e2e-runner/bootstrap_admin.py").read_bytes()
            subprocess.run([*compose, "exec", "-T", "backend", "python", "-"], input=bootstrap, cwd=directory, env=env, capture_output=True, check=True, timeout=180)
            stage = "seed"
            seeded = subprocess.run([*compose, "exec", "-T", "backend", "python", "-"], input=(ROOT / "e2e-runner/monitored_signal_seed.py").read_bytes(), cwd=directory, env=env, capture_output=True, check=True, timeout=120)
            identity = json.loads(seeded.stdout)
            project_id = str(uuid.UUID(identity["project_id"]))
            if not isinstance(identity.get("api_key"), str) or any(char in identity["api_key"] for char in "\r\n"):
                raise HarnessError("invalid_seed_identity")
            private_file(proof / "release-manifest.json", raw)
            stage = "runtime_identity"
            command([sys.executable, str(source / "scripts/monitoring/inspect_compose_candidate.py"), "--engine", args.engine, "--compose-project", project, "--project-id", project_id, "--manifest", str(proof / "release-manifest.json"), "--source-sha", manifest["source_sha"], "--output", str(proof / "runtime-proof.json")], cwd=source, env=env)
            gate_env = {
                "TESTLOOKUP_DISPOSABLE_SIGNAL_GATE": "1", "TESTLOOKUP_API_URL": "http://backend:8000",
                "TESTLOOKUP_PROMETHEUS_URL": "http://prometheus:9090", "TESTLOOKUP_LOKI_URL": "http://loki:3100",
                "TESTLOOKUP_JAEGER_URL": "http://jaeger:16686", "TESTLOOKUP_MCP_URL": "http://mcp:8002",
                "TESTLOOKUP_API_KEY": identity["api_key"], "TESTLOOKUP_SIGNAL_PROJECT_ID": project_id,
                "OBSERVABILITY_INSTALLATION": settings["OBSERVABILITY_INSTALLATION"], "APP_ENV": "development",
                "CELERY_BROKER_URL": config["services"]["backend"]["environment"]["CELERY_BROKER_URL"],
                "CELERY_RESULT_BACKEND": config["services"]["backend"]["environment"]["CELERY_RESULT_BACKEND"],
            }
            stage = "signal_gate"
            command(gate_container(args.engine, project, manifest["images"]["backend"]["source_ref"], source, proof), cwd=directory, env={**env, **gate_env}, timeout=1800)
            signal = json.loads((proof / "release-signal-proof.json").read_bytes())
            validate_signal_result(signal, manifest, raw, (proof / "runtime-proof.json").read_bytes())
            result["status"] = "passed"
        except (HarnessError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            result.update(stage=stage, code="isolated_monitoring_acceptance_failed")
        finally:
            if invoke is not None:
                try:
                    cleanup(args.engine, project, invoke, cwd=directory, env=env)
                    result["cleanup"] = "completed"
                except (HarnessError, OSError, subprocess.SubprocessError):
                    result["cleanup"] = "failed"
                    result["status"] = "failed"
            else:
                result["cleanup"] = "not_started"
            for filename in ("runtime-proof.json", "release-signal-proof.json"):
                path = proof / filename
                if path.is_file():
                    shutil.copyfile(path, evidence / filename)
    write_result(args.out, result)
    print("Disposable monitored stack: " + result["status"])
    return 0 if result["status"] == "passed" and result["cleanup"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
