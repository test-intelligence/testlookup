"""Run the ordinary Podman suite and the independently provisioned upgrade rehearsal."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from merge_upgrade import merge

HERE = Path(__file__).resolve().parent


def run_tool(name, *args, env):
    return subprocess.run(
        [sys.executable, HERE / name, *args], env=env, cwd=HERE.parent
    ).returncode


def campaign(manifest, tier, out, repository):
    if out.exists():
        raise ValueError("Campaign output must be a fresh directory")
    ordinary_env = os.environ.copy()
    for key in ("GH_TOKEN", "GITHUB_TOKEN"):
        ordinary_env.pop(key, None)
    common = [
        "--engine",
        "podman",
        "--manifest",
        manifest,
        "--tier",
        tier,
        "--out",
        out,
    ]
    if tier == "smoke":
        return run_tool("launch.py", *common, env=ordinary_env)

    with tempfile.TemporaryDirectory(prefix="testlookup-release-") as scratch:
        baseline = Path(scratch) / "verified-previous"
        rehearsal = Path(scratch) / "upgrade-rehearsal"
        operator_exit = run_tool(
            "upgrade_baseline.py",
            "--repository",
            repository,
            "--candidate",
            manifest,
            "--out",
            baseline,
            env=os.environ,
        )
        if operator_exit == 0:
            operator_exit = run_tool(
                "upgrade.py",
                "--target",
                "podman",
                "--repository",
                repository,
                "--candidate",
                manifest,
                "--previous",
                baseline / "release-manifest.json",
                "--baseline-provenance",
                baseline / "provenance.json",
                "--baseline-archive",
                baseline / "verified-images.zip",
                "--out",
                rehearsal,
                env=os.environ,
            )
        suite_exit = run_tool("launch.py", *common, "--defer-upgrade", env=ordinary_env)
        return merge(
            out,
            rehearsal / "upgrade-results.json",
            manifest,
            baseline,
            "podman",
            suite_exit,
            operator_exit,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--tier", choices=("smoke", "regression", "full"), default="regression"
    )
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--repository", default="test-intelligence/testlookup")
    args = parser.parse_args()
    return campaign(
        args.manifest.resolve(strict=True),
        args.tier,
        args.out.resolve(),
        args.repository,
    )


if __name__ == "__main__":
    raise SystemExit(main())
