"""Run one command against an isolated, disposable candidate via Podman Compose."""

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import uuid

import yaml
from evidence import attach_logs
from fakes_config import configure as configure_fixtures

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "release"))
from release_manifest import validate_manifest  # noqa: E402 - shared repository validator


def prepare(directory, manifest, project, engine="podman", *, host_runner=False):
    config = yaml.safe_load(
        (ROOT / "docker-compose.release.yml").read_text(encoding="utf-8")
    )
    images = manifest["images"]
    infrastructure = {
        "postgres": "public.ecr.aws/docker/library/postgres@sha256:aa6cb33cf4c19c765cefcf48d96176258800870a9a1e930ad126200f9d825139",
        "mongo": "public.ecr.aws/docker/library/mongo@sha256:8bb4442761a63a8dda452744082cdac9644b429807eb977b42324a63cef59ca4",
        "redis": "docker.io/valkey/valkey@sha256:2602ace2be6569bc2bbb06f873776516ab9089821525be34f4305c67eb9db02a",
        "minio": "ghcr.io/versity/versitygw@sha256:30292fc2eeacc67a36993b01f7a7a5e3361a19cced0e80c1d71cfa2a4b0a2499",
    }
    config["name"] = project
    services = config["services"]
    for name in ("ollama", "chromadb", "seed-init", "beat"):
        services.pop(name, None)
    for name, service in services.items():
        service.pop("container_name", None)
        service.pop("ports", None)
        service["restart"] = "no"
        component = name if name in ("frontend", "mcp") else "backend"
        service["image"] = infrastructure.get(name, images[component]["source_ref"])
    # Ask Compose for free host ports. All database and worker ports remain internal.
    for name, port in (("backend", 8000), ("frontend", 80), ("mcp", 8002)):
        services[name]["ports"] = [f"127.0.0.1::{port}"]
    config["networks"]["testlookup_net"].pop("ipam", None)
    config["networks"]["testlookup_net"]["internal"] = True
    # This one-shot computes a connection budget without opening a DB connection.
    # Avoid silently attaching it to a second, external default bridge.
    services["db-budget-check"]["network_mode"] = "none"
    services["backend"]["depends_on"]["minio-setup"] = {
        "condition": "service_completed_successfully"
    }
    allowed_mcp_hosts = "mcp:8002,127.0.0.1:*" if host_runner else "mcp:8002"
    services["mcp"]["environment"].append(f"TESTLOOKUP_MCP_ALLOWED_HOSTS={allowed_mcp_hosts}")
    configure_fixtures(config, directory, engine, images["backend"]["source_ref"])
    if host_runner:
        services["release-fixture"]["ports"] = ["127.0.0.1::8080"]
    (directory / "compose.json").write_text(json.dumps(config), encoding="utf-8")
    (directory / "scripts").mkdir()
    shutil.copyfile(
        ROOT / "scripts" / "init-db.sql", directory / "scripts" / "init-db.sql"
    )
    settings = dict.fromkeys(
        (
            "POSTGRES_PASSWORD MONGO_PASSWORD REDIS_PASSWORD MINIO_ACCESS_KEY MINIO_SECRET_KEY APP_SECRET_KEY JWT_SECRET_KEY WEBHOOK_SECRET INITIAL_ADMIN_PASSWORD FLOWER_PASSWORD"
        ).split()
    )
    settings = {key: secrets.token_hex(24) for key in settings}
    settings.update(
        APP_ENV="development",
        DEV_AUTO_LOGIN_ENABLED="true",
        AI_OFFLINE_MODE="true",
        SMTP_HOST="",
        PG_WORKER_POOL_SIZE="1",
        PG_WORKER_MAX_OVERFLOW="1",
    )
    env_file = directory / ".env"
    env_file.touch(mode=0o600)
    env_file.write_text(
        "".join(f"{key}={value}\n" for key, value in settings.items()), encoding="utf-8"
    )
    return settings


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--engine", choices=["podman", "docker"], default="podman")
    p.add_argument("--runner-image", help="Execute on the private Compose network")
    p.add_argument("--out", type=Path)
    p.add_argument("--tier", choices=["smoke", "regression", "full"], default="smoke")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--defer-upgrade", action="store_true",
                   help="Allow an intermediate result pending only the separate upgrade operator")
    p.add_argument("command", nargs=argparse.REMAINDER)
    args = p.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.runner_image:
        if command or args.out is None or args.out.exists():
            p.error(
                "--runner-image requires a fresh --out directory and no host command"
            )
        if args.repeat < 1:
            p.error("--repeat must be positive")
    elif not command:
        p.error("provide --runner-image and --out, or a host command after --")
    manifest_file = args.manifest.resolve()
    manifest = validate_manifest(json.loads(manifest_file.read_text(encoding="utf-8")))
    project = "tl-e2e-" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix=project) as name:
        directory = Path(name)
        settings = prepare(directory, manifest, project, args.engine, host_runner=not args.runner_image)
        compose = [
            args.engine,
            "compose",
            "--project-name",
            project,
            "-f",
            "compose.json",
        ]
        # Compose gives process variables precedence over .env. Pin the generated
        # credentials in both places so existing profile settings cannot select a real DB.
        env = {**os.environ, **settings, "COMPOSE_PARALLEL_LIMIT": "1"}

        def invoke(*arguments, **kwargs):
            return subprocess.run(
                [*compose, *arguments], cwd=directory, env=env, check=True, **kwargs
            )

        try:
            invoke(
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "300",
                "frontend",
                "worker",
                "worker-children",
                "mcp",
            )
            invoke(
                "exec",
                "-T",
                "backend",
                "python",
                "-",
                input=(ROOT / "e2e-runner/bootstrap_admin.py").read_bytes(),
            )
            if args.runner_image:
                folder = args.out.resolve()
                folder.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(
                    prefix="report-", dir=folder.parent
                ) as staging:
                    runner = [
                        args.engine,
                        "run",
                        "--rm",
                        "--network",
                        project + "_testlookup_net",
                        "--shm-size",
                        "1g",
                        "-v",
                        f"{staging}:/results",
                        "-v",
                        f"{manifest_file}:/candidate.json:ro",
                        "-e",
                        "TL_TARGET=ephemeral",
                        "-e",
                        "TL_PLATFORM=" + args.engine,
                        "-e",
                        "TL_FAKE_URL=http://release-fixture:8080",
                        "-e",
                        "TL_API=http://backend:8000",
                        "-e",
                        "TL_WEB=http://frontend",
                        "-e",
                        "TL_MCP=http://mcp:8002",
                        "-e",
                        "TL_MANIFEST=/candidate.json",
                    ]
                    for role in ("VIEWER", "TESTER", "QA_ENGINEER", "QA_LEAD", "ADMIN"):
                        runner.extend(["-e", f"TL_USER_{role}={role.lower()}"])
                    if args.repeat > 1:
                        folder.mkdir()
                    codes = []
                    for number in range(1, args.repeat + 1):
                        target = (
                            folder if args.repeat == 1 else folder / f"run-{number:02}"
                        )
                        name = f"run-{number:02}"
                        result = subprocess.run(
                            [
                                *runner,
                                args.runner_image,
                                "--out",
                                "/results/" + name,
                                "--tier",
                                args.tier,
                            ]
                        )
                        generated = Path(staging) / name
                        if not generated.is_dir():
                            raise RuntimeError(
                                "Runner returned without producing a report"
                            )
                        generated.rename(target)
                        attach_logs(target, invoke)
                        code = result.returncode
                        if code == 1 and args.defer_upgrade:
                            from merge_upgrade import pending_only_upgrade

                            if pending_only_upgrade(json.loads((target / "results.json").read_bytes())):
                                code = 0
                        codes.append(code)
                    return int(any(codes))

            def address(service, port):
                binding = (
                    invoke("port", service, str(port), capture_output=True, text=True)
                    .stdout.strip()
                    .splitlines()[-1]
                )
                if not binding.startswith("127.0.0.1:"):
                    raise RuntimeError(f"Unexpected binding for {service}")
                return "http://" + binding

            runner_env = {
                **os.environ,
                "TL_TARGET": "ephemeral",
                "TL_PLATFORM": args.engine,
                "TL_COMPOSE_PROJECT": project,
                "TL_FAKE_URL": address("release-fixture", 8080),
                "TL_API": address("backend", 8000),
                "TL_WEB": address("frontend", 80),
                "TL_MCP": address("mcp", 8002),
                "TL_MANIFEST": str(manifest_file),
                "TL_EXPECTED_SHA": manifest["source_sha"],
            }
            for role in ("VIEWER", "TESTER", "QA_ENGINEER", "QA_LEAD", "ADMIN"):
                runner_env["TL_USER_" + role] = role.lower()
                runner_env.pop("TL_TOKEN_" + role, None)
            return subprocess.run(command, env=runner_env, cwd=ROOT).returncode
        finally:
            invoke("down", "--volumes", "--remove-orphans", "--timeout", "10")


if __name__ == "__main__":
    sys.exit(main())
