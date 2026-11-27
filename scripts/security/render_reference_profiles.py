"""Render supported local profiles with synthetic inputs and retain policy evidence.

Only `docker compose ... config` and `kustomize build` are invoked. No image
resolution, daemon, Kubernetes context, provider plan or deployment is used.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid

try:
    from . import check_rendered_infrastructure as policy
except ImportError:
    import check_rendered_infrastructure as policy

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PROFILES = {
    "compose-source": ("docker-compose.yml",),
    "compose-release": ("docker-compose.release.yml",),
    "compose-dev-lite": ("docker-compose.dev-lite.yml",),
    "compose-gcp-vm": ("docker-compose.yml", "docker-compose.gcp-vm.yml"),
    "compose-airgap": ("docker-compose.release.yml", "docker-compose.airgap.yml"),
    "compose-demo": ("docker-compose.release.yml", "docker-compose.demo.yml"),
}
for base, file in (("source", "docker-compose.yml"), ("release", "docker-compose.release.yml")):
    monitoring = (file, "docker-compose.monitoring.yml")
    COMPOSE_PROFILES[f"compose-{base}-monitoring"] = monitoring
    COMPOSE_PROFILES[f"compose-{base}-telemetry"] = (*monitoring, "docker-compose.telemetry.yml")
    COMPOSE_PROFILES[f"compose-{base}-observability"] = (
        *monitoring, "docker-compose.monitoring-auth.yml", "docker-compose.telemetry.yml",
        "docker-compose.alerting.yml", "docker-compose.objectives.yml",
    )
    COMPOSE_PROFILES[f"compose-{base}-infrastructure"] = (
        *COMPOSE_PROFILES[f"compose-{base}-observability"], "docker-compose.infrastructure-metrics.yml",
    )
COMPOSE_PROFILES["compose-release-observability-auth-last"] = (
    "docker-compose.release.yml", "docker-compose.monitoring.yml", "docker-compose.telemetry.yml",
    "docker-compose.alerting.yml", "docker-compose.objectives.yml", "docker-compose.monitoring-auth.yml",
)


def _environment() -> dict[str, str]:
    # Preserve executable/runtime discovery only. Ambient Compose substitutions
    # and real credentials cannot replace the controlled interpolation values.
    keys = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "PROGRAMFILES", "PROGRAMFILES(X86)",
            "USERPROFILE", "HOME", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP"}
    return {key: value for key, value in os.environ.items() if key.upper() in keys}


def _run(command: list[str], root: Path, environment: dict[str, str]) -> bytes:
    try:
        result = subprocess.run(command, cwd=root, env=environment, capture_output=True, timeout=90, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise policy.PolicyInputError("Local renderer is unavailable or exceeded its deadline.") from error
    if result.returncode:
        # Renderer stderr can include interpolated secrets or host paths.
        raise policy.PolicyInputError("Local render failed. Reproduce with synthetic inputs to inspect it.")
    if not result.stdout or len(result.stdout) > policy.MAX_BYTES:
        raise policy.PolicyInputError("Local render is empty or exceeds its size limit.")
    return result.stdout


def _fixture_environment(root: Path, scratch: Path, profile: str, files: tuple[str, ...]) -> Path:
    values = {}
    for file in files:
        text = (root / file).read_text(encoding="utf-8")
        for key in re.findall(r"\$\{([A-Z][A-Z0-9_]*):\?", text):
            values[key] = uuid.uuid4().hex
    values.update({"APP_ENV": "development" if profile in {"compose-source", "compose-dev-lite", "compose-source-monitoring", "compose-source-telemetry"} else "production",
                   "GCP_PROJECT_ID": "policy-fixture", "IMAGE_TAG": "policy-fixture", "TESTLOOKUP_VERSION": "policy-fixture",
                   "TESTLOOKUP_REGISTRY": "registry.invalid/policy-fixture", "OBSERVABILITY_INSTALLATION": "policy-fixture",
                   "SLO_PROMETHEUS_RETENTION_SIZE": "1GB"})
    secret = scratch / "synthetic-secret"
    secret.write_text(uuid.uuid4().hex, encoding="utf-8")
    for key in ("METRICS_API_TOKEN_SOURCE", "METRICS_PROMETHEUS_TOKEN_SOURCE", "ALERTMANAGER_INCIDENT_URL_SOURCE",
                "ALERTMANAGER_INCIDENT_TOKEN_SOURCE", "ALERTMANAGER_WATCHDOG_URL_SOURCE", "ALERTMANAGER_WATCHDOG_TOKEN_SOURCE",
                "INFRASTRUCTURE_SCRAPE_CONFIG_SOURCE", "INFRASTRUCTURE_RULES_SOURCE", "INFRASTRUCTURE_CA_SOURCE",
                "INFRASTRUCTURE_CLIENT_CERT_SOURCE", "INFRASTRUCTURE_CLIENT_KEY_SOURCE"):
        values[key] = secret.as_posix()
    values["ALERTING_PROMETHEUS_CONFIG_SOURCE"] = (root / "infra/monitoring/prometheus.yml").as_posix()
    values["SLO_ADOPTION_RULES_FILE"] = (root / "infra/monitoring/objectives/adoption.example.yml").as_posix()
    envfile = scratch / "synthetic.env"
    envfile.write_text("\n".join(f"{key}={value}" for key, value in sorted(values.items())) + "\n", encoding="utf-8")
    return envfile


def defensive_controls(data: bytes, kind: str) -> dict[str, bool]:
    """Seed defects in memory only, and require a new finding for each defect."""
    original = policy.load_render(data, kind)
    check = policy.check_compose if kind == "compose" else policy.check_kubernetes
    before = {(f["rule"], f["resource"], f["field"]) for f in check(original)}
    public, privileged = deepcopy(original), deepcopy(original)
    if kind == "compose":
        # All reference Compose roots include the actual PostgreSQL listener.
        service = public["services"].get("postgres")
        if service is None:
            raise policy.PolicyInputError("Reference Compose control lacks its expected datastore.")
        service["ports"] = [{"target": 5432, "published": "5432", "host_ip": "0.0.0.0", "protocol": "tcp"}]
        privileged["services"]["backend"]["privileged"] = True
    else:
        services = [d for d in public if d.get("kind") == "Service" and d.get("metadata", {}).get("name") == "testlookup-backend"]
        workloads = [d for d in privileged if d.get("kind") == "Deployment" and d.get("metadata", {}).get("name") == "testlookup-backend"]
        if len(services) != 1 or len(workloads) != 1:
            raise policy.PolicyInputError("Reference Kubernetes control lacks its expected API workload.")
        services[0]["spec"]["type"] = "NodePort"
        workloads[0]["spec"]["template"]["spec"]["containers"][0].setdefault("securityContext", {})["privileged"] = True
    def rejected(document, rule):
        return any((f["rule"], f["resource"], f["field"]) not in before and f["rule"] == rule for f in check(document))
    return {"public_operational_endpoint_rejected": rejected(public, "OPS001"),
            "privileged_container_rejected": rejected(privileged, "CTR001")}


def run_matrix(root: Path, *, source_sha: str | None = None, profiles: list[str] | None = None, exceptions=None) -> dict:
    if source_sha is not None and re.fullmatch(r"[0-9a-f]{40}", source_sha) is None:
        raise policy.PolicyInputError("Source SHA must be a complete lowercase Git object ID.")
    root = root.resolve(strict=True)
    kubernetes = {"kubernetes-base": "k8s/base"}
    for directory in sorted((root / "k8s/overlays").iterdir()):
        if directory.is_dir() and (directory / "kustomization.yaml").is_file():
            kubernetes["kubernetes-" + directory.name] = directory.relative_to(root).as_posix()
    known = set(COMPOSE_PROFILES) | set(kubernetes)
    selected = set(profiles or known)
    if not selected or not selected <= known:
        raise policy.PolicyInputError("Unknown or empty reference profile selection.")
    docker = shutil.which("docker") if selected & COMPOSE_PROFILES.keys() else None
    kustomize = shutil.which("kustomize") if selected & kubernetes.keys() else None
    if (selected & COMPOSE_PROFILES.keys() and not docker) or (selected & kubernetes.keys() and not kustomize):
        raise policy.PolicyInputError("Reference validation requires Docker Compose and Kustomize executables.")
    environment, results = _environment(), []
    with tempfile.TemporaryDirectory(prefix="testlookup-infrastructure-policy-") as name:
        scratch = Path(name).resolve(strict=True)
        for profile in sorted(selected):
            if profile in COMPOSE_PROFILES:
                files = COMPOSE_PROFILES[profile]
                envfile = _fixture_environment(root, scratch, profile, files)
                command = [str(docker), "compose", "--env-file", str(envfile), "--project-directory", str(root), "--profile", "*"]
                for file in files:
                    command.extend(["--file", str(root / file)])
                # Per-service env_file entries are not read. The global envfile
                # above is synthetic, so neither form reads the local .env.
                command.extend(["config", "--format", "json", "--no-env-resolution"])
                kind = "compose"
            else:
                command = [str(kustomize), "build", str(root / kubernetes[profile])]
                kind = "kubernetes"
            data = _run(command, root, environment)
            report = policy.evaluate(data, kind, profile, exceptions=exceptions, source_sha=source_sha)
            report["defensive_controls"] = defensive_controls(data, kind)
            report["passed"] = report["passed"] and all(report["defensive_controls"].values())
            results.append(report)
    return {"schema_version": 1, "policy_version": policy.POLICY_VERSION, "source_sha": source_sha,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "scope": "reference-matrix" if profiles is None else "focused-subset", "expected_profiles": sorted(known),
            "renderer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "passed": all(result["passed"] for result in results), "profiles": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--source-sha")
    parser.add_argument("--profile", action="append", help="Repeat for a focused subset. CI should omit this flag.")
    parser.add_argument("--exceptions", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        exceptions = json.loads(args.exceptions.read_text(encoding="utf-8")) if args.exceptions else None
        report = run_matrix(args.root, source_sha=args.source_sha, profiles=args.profile, exceptions=exceptions)
        code = 0 if report["passed"] else 1
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        report = {"schema_version": 1, "passed": False,
                  "source_sha": args.source_sha if args.source_sha and re.fullmatch(r"[0-9a-f]{40}", args.source_sha) else None,
                  "error": str(error) if isinstance(error, policy.PolicyInputError) else "Reference policy validation could not complete."}
        code = 2
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        print('{"passed": false, "error": "Policy evidence could not be written."}')
        return 2
    print(json.dumps({"passed": report["passed"], "profile_count": len(report.get("profiles", []))}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
