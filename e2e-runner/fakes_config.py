"""Configure the application's real clients for private release-test services."""

from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FAKE_HOST = "release-fixture"
APP_SETTINGS = {
    "AI_OFFLINE_MODE": "false",
    "LLM_PROVIDER": "ollama",
    "LLM_MODEL": "release-fixture",
    "OLLAMA_BASE_URL": "http://release-fixture:8080",
    "AI_MAX_RETRIES": "0",
    "JIRA_ENABLED": "true",
    "JIRA_DOMAIN": "release-fixture:8443",
    "JIRA_EMAIL": "release-bot@example.test",
    "JIRA_API_TOKEN": "synthetic-release-fixture-token",
    "JIRA_DEFAULT_PROJECT_KEY": "QA",
    "HTTP_CA_BUNDLE": "/fixture-trust/ca.pem",
    "OUTBOUND_WEBHOOK_PRIVATE_ALLOWED_HOSTS": "release-fixture",
    "REVIEW_GATE_ENFORCED": "true",
    "CHROMA_HOST": "release-fixture",
    "CHROMA_PORT": "8080",
}


def prepare_material(directory, engine, image, *, openssl=None):
    for origin in (
        ROOT / "backend/tests/fakes/fake_integrations.py",
        ROOT / "e2e-runner/fakes/server.py",
        ROOT / "e2e-runner/fakes/probe.py",
    ):
        shutil.copyfile(origin, directory / origin.name)
    if openssl is not None:
        command = [openssl]
        cert_dir = directory
    else:
        command = [
            engine,
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0:0",
            "-v",
            f"{directory}:/fixture",
            "--entrypoint",
            "openssl",
            image,
        ]
        cert_dir = Path("/fixture")
    subprocess.run(
        [
            *command,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "2",
            "-subj",
            "/CN=release-fixture",
            "-addext",
            "subjectAltName=DNS:release-fixture",
            "-keyout",
            (cert_dir / "key.pem").as_posix(),
            "-out",
            (cert_dir / "ca.pem").as_posix(),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def configure(config, directory, engine, image):
    folder = directory / "release-fixture"
    folder.mkdir()
    prepare_material(folder, engine, image)
    services = config["services"]
    services[FAKE_HOST] = {
        "image": image,
        "user": "0:0",
        "entrypoint": ["python", "/fixture/server.py"],
        "restart": "no",
        "networks": ["testlookup_net"],
        "environment": {
            "FAKE_BASE_URL": "https://release-fixture:8443",
            "FAKE_JIRA_EMAIL": APP_SETTINGS["JIRA_EMAIL"],
            "FAKE_JIRA_TOKEN": APP_SETTINGS["JIRA_API_TOKEN"],
        },
        "volumes": [
            "./release-fixture:/fixture:ro",
            "./release-fixture:/fixture-tls:ro",
        ],
        "healthcheck": {
            "test": ["CMD", "python", "/fixture/probe.py"],
            "interval": "2s",
            "timeout": "5s",
            "retries": 30,
        },
    }
    for name in ("backend", "worker", "worker-children"):
        service = services[name]
        service["environment"] = [
            item
            for item in service["environment"]
            if item.split("=", 1)[0] not in APP_SETTINGS
        ]
        service["environment"].extend(f"{k}={v}" for k, v in APP_SETTINGS.items())
        service.setdefault("volumes", []).append(
            "./release-fixture/ca.pem:/fixture-trust/ca.pem:ro"
        )
        service.setdefault("depends_on", {})[FAKE_HOST] = {
            "condition": "service_healthy"
        }
