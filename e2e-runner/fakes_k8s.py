"""Install private contract fixtures in an existing, explicitly selected disposable test namespace."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from fakes_config import APP_SETTINGS, FAKE_HOST, prepare_material


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--namespace-uid", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--openssl",
        type=Path,
        help="Use a local OpenSSL binary instead of the Podman image to generate this fixture's certificate",
    )
    args = parser.parse_args()
    # Only the UID checked by the deployment owner is eligible for mutation.
    if not args.namespace.startswith(("testlookup-e2e-", "tl-e2e-")):
        parser.error("A disposable release namespace is required")
    kubectl = ["kubectl", "--context", args.context, "-n", args.namespace]

    def kube(*argv, body=None):
        result = subprocess.run(
            [*kubectl, *argv],
            input=json.dumps(body) if body is not None else None,
            text=True,
            capture_output=True,
        )
        if result.returncode:
            raise RuntimeError(f"kubectl {argv[0]} failed: {result.stderr[:300]}")
        return result.stdout

    namespace = json.loads(kube("get", "namespace", args.namespace, "-o", "json"))
    if namespace["metadata"]["uid"] != args.namespace_uid:
        raise SystemExit("Namespace UID changed")
    manifest = json.loads(args.manifest.read_bytes())
    image = manifest["images"]["backend"]["source_ref"]
    if "@sha256:" not in image:
        raise SystemExit("Expected immutable candidate backend image")
    with tempfile.TemporaryDirectory() as path:
        folder = Path(path)
        prepare_material(folder, "podman", image, openssl=args.openssl)
        revision = hashlib.sha256((folder / "ca.pem").read_bytes()).hexdigest()

        def apply(obj):
            kube(
                "apply",
                "--server-side",
                "--field-manager=release-fixture",
                "-f",
                "-",
                body=obj,
            )

        apply(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "release-fixture-code"},
                "data": {
                    f: (folder / f).read_text(encoding="utf-8")
                    for f in ("server.py", "fake_integrations.py", "ca.pem")
                },
            }
        )
        import base64

        apply(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "release-fixture-key"},
                "data": {
                    "key.pem": base64.b64encode(
                        (folder / "key.pem").read_bytes()
                    ).decode()
                },
            }
        )
    labels = {"app": FAKE_HOST}
    volumes = [
        {"name": "fixture-code", "configMap": {"name": "release-fixture-code"}},
        {"name": "fixture-key", "secret": {"secretName": "release-fixture-key"}},
    ]
    mounts = [
        {"name": "fixture-code", "mountPath": "/fixture", "readOnly": True},
        {
            "name": "fixture-code",
            "mountPath": "/fixture-tls/ca.pem",
            "subPath": "ca.pem",
            "readOnly": True,
        },
        {
            "name": "fixture-key",
            "mountPath": "/fixture-tls/key.pem",
            "subPath": "key.pem",
            "readOnly": True,
        },
    ]
    apply(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": FAKE_HOST},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": labels},
                "template": {
                    "metadata": {
                        "labels": labels,
                        "annotations": {"testlookup.io/fixture-revision": revision},
                    },
                    "spec": {
                        "automountServiceAccountToken": False,
                        "containers": [
                            {
                                "name": "receiver",
                                "image": image,
                                "command": ["python", "/fixture/server.py"],
                                "env": [
                                    {
                                        "name": "FAKE_BASE_URL",
                                        "value": "https://release-fixture:8443",
                                    },
                                    {
                                        "name": "FAKE_JIRA_EMAIL",
                                        "value": APP_SETTINGS["JIRA_EMAIL"],
                                    },
                                    {
                                        "name": "FAKE_JIRA_TOKEN",
                                        "value": APP_SETTINGS["JIRA_API_TOKEN"],
                                    },
                                ],
                                "resources": {
                                    "requests": {"cpu": "20m", "memory": "64Mi"},
                                    "limits": {"memory": "192Mi"},
                                },
                                "readinessProbe": {
                                    "httpGet": {"path": "/api/tags", "port": 8080}
                                },
                                "volumeMounts": mounts,
                            }
                        ],
                        "volumes": volumes,
                    },
                },
            },
        }
    )
    apply(
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": FAKE_HOST},
            "spec": {
                "selector": labels,
                "ports": [
                    {"name": "http", "port": 8080},
                    {"name": "https", "port": 8443},
                ],
            },
        }
    )
    kube("rollout", "status", "deployment/" + FAKE_HOST, "--timeout=180s")
    deployments = json.loads(kube("get", "deployment", "-o", "json"))["items"]
    configured = []
    for deploy in deployments:
        name = deploy["metadata"]["name"]
        if not (
            name == "testlookup-backend"
            or name.startswith(("testlookup-worker", "testlookup-celery"))
        ):
            continue
        containers = deploy["spec"]["template"]["spec"]["containers"]
        applicable = [c for c in containers if c["image"] == image]
        if not applicable:
            raise SystemExit(f"{name} does not use the verified candidate image")
        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {"testlookup.io/fixture-revision": revision}
                    },
                    "spec": {
                        "volumes": [
                            {
                                "name": "fixture-trust",
                                "configMap": {
                                    "name": "release-fixture-code",
                                    "items": [{"key": "ca.pem", "path": "ca.pem"}],
                                },
                            }
                        ],
                        "containers": [
                            {
                                "name": c["name"],
                                "env": [
                                    {"name": k, "value": v}
                                    for k, v in APP_SETTINGS.items()
                                ],
                                "volumeMounts": [
                                    {
                                        "name": "fixture-trust",
                                        "mountPath": "/fixture-trust",
                                        "readOnly": True,
                                    }
                                ],
                            }
                            for c in applicable
                        ],
                    },
                }
            }
        }
        kube(
            "patch",
            "deployment",
            name,
            "--type",
            "strategic",
            "--patch",
            json.dumps(patch),
        )
        configured.append(name)
    if "testlookup-backend" not in configured or len(configured) < 2:
        raise SystemExit("Backend and worker deployments were not both configured")
    for name in configured:
        kube("rollout", "status", "deployment/" + name, "--timeout=300s")
    print("Configured isolated fixture and " + ", ".join(configured))


if __name__ == "__main__":
    main()
