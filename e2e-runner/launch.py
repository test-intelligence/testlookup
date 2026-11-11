"""One launcher for local Podman, Jenkins and GitHub Actions."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    from run import timestamp

    started = timestamp()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--env", choices=("ephemeral", "homelab", "external"), default="ephemeral"
    )
    parser.add_argument(
        "--tier", choices=("smoke", "regression", "full"), default="smoke"
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Independent disposable-stack suite executions",
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--engine", choices=("podman", "docker"), default="podman")
    opts = parser.parse_args()
    manifest = opts.manifest.resolve(strict=True)
    out = opts.out.resolve()
    if out.exists():
        parser.error("--out must be a new directory")
    if opts.env != "ephemeral":
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "e2e-runner/run.py"),
                "--out",
                str(out),
                "--tier",
                opts.tier,
            ],
            env={**os.environ, "TL_TARGET": opts.env, "TL_MANIFEST": str(manifest)},
            cwd=ROOT,
        ).returncode
    engine = opts.engine
    tag = "localhost/testlookup-release-e2e:" + uuid.uuid4().hex
    build = [engine, "build", "-f", "e2e-runner/Dockerfile", "-t", tag]
    if engine == "podman":
        build.extend(
            ["--network", "host", "--ignorefile", "e2e-runner/containerignore"]
        )
    try:
        subprocess.run([*build, "."], cwd=ROOT, check=True)
        digest = subprocess.check_output(
            [engine, "image", "inspect", "--format", "{{.Id}}", tag], text=True
        ).strip()
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "e2e-runner/ephemeral.py"),
                "--engine",
                engine,
                "--manifest",
                str(manifest),
                "--runner-image",
                digest,
                "--tier",
                opts.tier,
                "--out",
                str(out),
                "--repeat",
                str(opts.repeat),
            ],
            cwd=ROOT,
        ).returncode
    finally:
        if (
            not (out / "results.json").exists()
            and not (out / f"run-{opts.repeat:02}" / "results.json").exists()
        ):
            # A missing report after a build/start failure must be visible to the CI gate.
            from run import JOURNEYS, render, timestamp

            out.mkdir(parents=True, exist_ok=True)
            import hashlib

            report = {
                "target": opts.env,
                "tier": opts.tier,
                "tag": json.loads(manifest.read_bytes()).get("version", "unknown"),
                "manifest_hash": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "observed_build": {},
                "start": started,
                "end": timestamp(),
                "verdict": "NO_GO",
                "reason": "Provisioning or runner failed before results were available.",
                "errors": ["See the orchestrator log for the first failed operation."],
                "cases": [
                    {
                        "id": k,
                        "priority": v,
                        "status": "BLOCKED",
                        "evidence": "No execution record.",
                    }
                    for k, v in JOURNEYS.items()
                ],
            }
            render(out, report)
        subprocess.run(
            [engine, "image", "rm", tag],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


if __name__ == "__main__":
    sys.exit(main())
