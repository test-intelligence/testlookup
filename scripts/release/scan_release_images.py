#!/usr/bin/env python3
"""Scan the exact immutable image set selected for a release.

The release manifest is untrusted input until ``release_manifest`` validates
it.  Every image is scanned by digest, and all components are attempted even
when one violates policy so the retained evidence describes the whole release.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import release_manifest


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True)


def _write_json(path: Path, document: object) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _valid_json_report(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _finding_count(report: dict[str, Any]) -> int:
    results = report.get("Results", [])
    if not isinstance(results, list):
        return 0
    return sum(
        len(result.get("Vulnerabilities") or [])
        for result in results
        if isinstance(result, dict) and isinstance(result.get("Vulnerabilities") or [], list)
    )


def _write_diagnostics(path: Path, result: subprocess.CompletedProcess[str]) -> None:
    diagnostics = f"return_code: {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}\n"
    diagnostics = re.sub(r"(?im)^(authorization\s*:\s*).*$", r"\1[REDACTED]", diagnostics)
    diagnostics = re.sub(r"(?i)\b(token|password|secret)=([^&\s]+)", r"\1=[REDACTED]", diagnostics)
    diagnostics = re.sub(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@", diagnostics)
    path.write_text(
        diagnostics,
        encoding="utf-8",
    )


def scan_release(
    manifest_path: Path,
    output_dir: Path,
    *,
    trivy: str = "trivy",
    runner: Runner = _run,
) -> int:
    """Scan all manifest components and return nonzero on findings or errors."""

    manifest = release_manifest.load_manifest(manifest_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "release-manifest.json", manifest)

    version = runner((trivy, "--version"))
    (output_dir / "trivy-version.txt").write_text(
        (version.stdout + version.stderr).strip() + "\n", encoding="utf-8"
    )

    failed = version.returncode != 0
    components: dict[str, dict[str, object]] = {}
    for component in release_manifest.COMPONENTS:
        image_ref = manifest["images"][component]["source_ref"]
        vulnerability_path = output_dir / f"{component}.vulnerabilities.json"
        sbom_path = output_dir / f"{component}.cyclonedx.json"

        vulnerability = runner((
            trivy, "image", "--scanners", "vuln", "--format", "json",
            "--output", str(vulnerability_path), "--severity", "HIGH,CRITICAL",
            "--ignore-unfixed", "--exit-code", "1", "--timeout", "3m", image_ref,
        ))
        _write_diagnostics(output_dir / f"{component}.vulnerabilities.log", vulnerability)
        vulnerability_report = _valid_json_report(vulnerability_path)
        finding_count = _finding_count(vulnerability_report) if vulnerability_report else 0
        if vulnerability.returncode == 0 and vulnerability_report is not None:
            vulnerability_status = "passed"
        elif vulnerability.returncode == 1 and vulnerability_report is not None and finding_count:
            vulnerability_status = "blocked"
        else:
            vulnerability_status = "scanner_error"

        sbom = runner((
            trivy, "image", "--scanners", "vuln", "--format", "cyclonedx",
            "--output", str(sbom_path), "--exit-code", "0", "--timeout", "3m", image_ref,
        ))
        _write_diagnostics(output_dir / f"{component}.sbom.log", sbom)
        sbom_status = "generated" if sbom.returncode == 0 and _valid_json_report(sbom_path) else "scanner_error"

        components[component] = {
            "image_ref": image_ref,
            "actionable_high_or_critical": finding_count,
            "vulnerability_status": vulnerability_status,
            "sbom_status": sbom_status,
        }
        if vulnerability_status != "passed" or sbom_status != "generated":
            failed = True

    summary = {
        "policy": "fixed HIGH or CRITICAL vulnerabilities block promotion",
        "source_sha": manifest["source_sha"],
        "version": manifest["version"],
        "components": components,
        "result": "failed" if failed else "passed",
    }
    _write_json(output_dir / "summary.json", summary)
    return 1 if failed else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--trivy", default="trivy")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return scan_release(args.manifest, args.output_dir, trivy=args.trivy)
    except (release_manifest.ManifestError, OSError) as exc:
        print(f"release image scan error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
