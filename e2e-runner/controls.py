"""Check deployment and identity oracles using deliberately wrong E1 expectations."""

import json
import re
import subprocess

from run import IDENTITIES, ROOT, specs


def verify(directory, healthy, manifest, env):
    if env.get("TL_TARGET") != "ephemeral":
        raise ValueError("Negative controls are eligible only on the disposable target")
    selected = ["TL-DEPLOY-002", *IDENTITIES]
    if any(healthy[k]["status"] != "PASSED" for k in selected):
        return {
            "status": "BLOCKED",
            "reason": "A baseline oracle did not pass first time.",
        }
    target = directory / "negative-controls"
    target.mkdir()
    wrong_sha = ("f" if manifest["source_sha"][0] != "f" else "0") + manifest[
        "source_sha"
    ][1:]
    mutant = {**env, "TL_RESULTS": str(target), "TL_EXPECTED_SHA": wrong_sha}
    for key in IDENTITIES:
        mutant["TL_USER_" + key.split()[1]] = (
            "intentionally-mismatched-release-principal"
        )
    with (target / "private.log").open("w", encoding="utf-8") as log:
        proc = subprocess.run(
            [
                "node",
                "node_modules/@playwright/test/cli.js",
                "test",
                "--config",
                "playwright.release.config.ts",
                "--grep",
                "TL-(DEPLOY|ROLE)-002",
                "--retries=0",
            ],
            env=mutant,
            cwd=ROOT / "frontend",
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    raw = json.loads((target / "playwright.json").read_text(encoding="utf-8"))
    evidence = {}
    for spec in specs(raw["suites"]):
        key = next(k for k in selected if spec["title"].startswith(k + " "))
        result = spec["tests"][0]["results"][0]
        message = re.sub(
            r"\x1b\[[0-9;]*m", "", result.get("error", {}).get("message", "")
        )
        needle = (
            wrong_sha
            if key == "TL-DEPLOY-002"
            else "intentionally-mismatched-release-principal"
        )
        evidence[key] = (
            result["status"] == "failed" and needle in message and "toBe" in message
        )
    proven = (
        proc.returncode != 0
        and set(evidence) == set(selected)
        and all(evidence.values())
    )
    return {
        "status": "PASSED" if proven else "FAILED",
        "detected_by_oracle": evidence,
        "evidence": "negative-controls/playwright.json",
    }
