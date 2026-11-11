"""Run the release foundation and write a report even when collection fails."""

import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
from html import escape
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

from policy import evaluate

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/release"))
from release_manifest import validate_manifest  # noqa: E402 - shared repository validator

ROLES = ("VIEWER", "TESTER", "QA_ENGINEER", "QA_LEAD", "ADMIN")
PUBLIC = ["TL-DEPLOY-002", "TL-AUTH-002"]
IDENTITIES = ["TL-ROLE-002 " + role for role in ROLES]
# Each required but as-yet unimplemented journey remains visible in the verdict.
CATALOGUE = {
    entry["id"]: entry
    for entry in json.loads((ROOT / "e2e-runner/catalogue.yaml").read_bytes())
}
JOURNEYS = {
    key: entry["priority"]
    for key, entry in CATALOGUE.items()
    if entry["status"] == "designed"
}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def specs(suites):
    for suite in suites:
        yield from suite.get("specs", [])
        yield from specs(suite.get("suites", []))


def render(directory, report):
    (directory / "results.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    counts = Counter((r["priority"], r["status"]) for r in report["cases"])
    public_xml = ET.Element(
        "testsuite",
        name="release-quality",
        tests=str(len(report["cases"])),
        failures=str(sum(r["status"] in ("FAILED", "FLAKY") for r in report["cases"])),
        errors=str(sum(r["status"] == "BLOCKED" for r in report["cases"])),
        skipped=str(sum(r["status"] == "SKIPPED" for r in report["cases"])),
    )
    for r in report["cases"]:
        node = ET.SubElement(
            public_xml, "testcase", classname=r["priority"], name=r["id"]
        )
        tag = {
            "BLOCKED": "error",
            "FAILED": "failure",
            "FLAKY": "failure",
            "SKIPPED": "skipped",
        }.get(r["status"])
        if tag:
            ET.SubElement(node, tag, type=r["status"], message=r["evidence"])
    ET.ElementTree(public_xml).write(
        directory / "release-junit.xml", encoding="utf-8", xml_declaration=True
    )
    text = [
        "# TestLookup release quality",
        "",
        f"**{report['verdict']}** — {report['reason']}",
        "",
        f"Target {report['target']} · {report['tier']} · candidate {report['tag']}",
        f"Observed revision {report['observed_build'].get('revision', 'unmeasured')}",
        f"Manifest SHA-256 {report['manifest_hash']}",
        f"Start {report['start']} · End {report['end']}",
        "",
        "| Priority | Passed | Failed | Flaky | Blocked | Skipped |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for p in ("P0", "P1", "P2", "P3"):
        text.append(
            "| "
            + " | ".join(
                [p]
                + [
                    str(counts[p, s])
                    for s in ("PASSED", "FAILED", "FLAKY", "BLOCKED", "SKIPPED")
                ]
            )
            + " |"
        )
    text.extend(["", "| Test | Priority | Outcome | Evidence |", "|---|---|---|---|"])
    for r in sorted(report["cases"], key=lambda v: (v["priority"], v["id"])):
        text.append(
            f"| {r['id']} | {r['priority']} | {r['status']} | {r['evidence']} |"
        )
    if report["errors"]:
        text.extend(["", *report["errors"]])
    text.extend(
        [
            "",
            "Image verification: "
            + report.get("image_verification", "disposable manifest deployment"),
        ]
    )
    failures = [
        (c, req)
        for c in report["cases"]
        for req in c.get("requests", [])
        if req["status"] != req["expected"]
    ]
    for c, req in failures:
        text.append(
            f"- {c['id']}: {req['method']} {req['path']} returned {req['status']}, expected {req['expected']}. Request ID {req['request_id']}."
        )
    (directory / "summary.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    log_matches = report.get("log_evidence", {}).get("matched", {})
    request_rows = []
    for case in report["cases"]:
        for request in case.get("requests", []):
            log = log_matches.get(request["request_id"], {})
            values = (
                case["id"],
                request["at"],
                request["method"],
                request["path"],
                request["status"],
                request["expected"],
                request["request_id"],
                log.get("service", "Unmatched"),
            )
            request_rows.append(
                "<tr>"
                + "".join("<td>" + escape(str(value)) + "</td>" for value in values)
                + "</tr>"
            )
    images = "".join(
        "<li>"
        + escape(component)
        + " <code>"
        + escape(image["source_ref"])
        + "</code></li>"
        for component, image in report.get("images", {}).items()
    )
    errors = "".join("<li>" + escape(error) + "</li>" for error in report["errors"])
    coverage = "".join(
        "<tr>"
        + "".join(
            "<td>" + escape(str(case.get(key, ""))) + "</td>"
            for key in ("id", "title", "use_case", "journey", "role", "layer", "status")
        )
        + "</tr>"
        for case in report["cases"]
    )
    duration = datetime.fromisoformat(report["end"]) - datetime.fromisoformat(
        report["start"]
    )
    rows = "".join(
        "<tr>"
        + "".join(
            "<td>" + escape(r[k]) + "</td>"
            for k in ("id", "priority", "status", "evidence")
        )
        + "</tr>"
        for r in report["cases"]
    )
    (directory / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>TestLookup release quality</title>"
        "<style>body{font:16px system-ui;max-width:72em;margin:3em auto}td,th{padding:.5em;text-align:left;border-bottom:1px solid #ccc}</style>"
        "<h1>TestLookup release quality</h1><h2>"
        + escape(report["verdict"])
        + "</h2><p>"
        + escape(report["reason"])
        + "</p><p>"
        + escape(
            " · ".join(
                [report["target"], report["tier"], report["tag"], report["start"]]
            )
        )
        + "</p><p>Observed build "
        + escape(report["observed_build"].get("revision", "unmeasured"))
        + "</p><p>Manifest SHA-256 "
        + escape(report["manifest_hash"])
        + "</p><h2>Scoreboard</h2><table><tr><th>Priority<th>Passed<th>Failed<th>Flaky<th>Blocked<th>Skipped"
        + "".join(
            "<tr><th>"
            + p
            + "".join(
                "<td>" + str(counts[p, s])
                for s in ("PASSED", "FAILED", "FLAKY", "BLOCKED", "SKIPPED")
            )
            for p in ("P0", "P1", "P2", "P3")
        )
        + "</table><h2>Test outcomes</h2><table><tr><th>Test<th>Priority<th>Outcome<th>Evidence"
        + rows
        + "</table><h2>Deployment evidence</h2><p>Finished "
        + escape(report["end"])
        + f" · {duration.total_seconds():.2f} seconds</p><ul>"
        + images
        + errors
        + "</ul><h2>Use-case coverage</h2><table><tr><th>Test<th>Title<th>Use case<th>Journey<th>Role<th>Layer<th>Outcome"
        + coverage
        + "</table><h2>HTTP evidence</h2><table><tr><th>Test<th>Time<th>Method<th>Path<th>Status<th>Expected<th>Request ID<th>Service log"
        + "".join(request_rows)
        + "</table><p><a href='results.json'>Machine-readable evidence</a> · <a href='summary.md'>Markdown summary</a></p>",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--tier", choices=("smoke", "regression", "full"), default="smoke"
    )
    args = parser.parse_args()
    directory = args.out.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    target = os.environ.get("TL_TARGET", "external")
    expected = (
        PUBLIC + IDENTITIES + (["TL-INGEST-002"] if target == "ephemeral" else [])
    )
    inventory = {k: "P0" for k in expected}
    if args.tier != "smoke":
        inventory.update(JOURNEYS)
    cases = {
        k: {
            "id": k,
            "priority": p,
            "status": "BLOCKED",
            "evidence": "Required case has no recorded execution.",
        }
        for k, p in inventory.items()
    }
    clients = {
        "test_cli_ready": "TL-CLI-002",
        "test_mcp_stdio_public_health": "TL-MCP-002",
    }
    if target == "ephemeral" and args.tier != "smoke":
        clients.update(
            {
                "test_gate_empty_evidence": "TL-GATE-002",
                "test_mcp_sse_health": "TL-MCP-003",
                "test_sdk_pytest_reporter": "TL-SDK-001",
                "test_file_counts[allure]": "TL-INGEST-003",
                "test_file_counts[cypress]": "TL-INGEST-004",
                "test_file_counts[playwright]": "TL-INGEST-005",
                "test_file_counts[pytest]": "TL-INGEST-006",
                "test_file_counts[robot]": "TL-INGEST-007",
            }
        )
    for key in clients.values():
        cases[key] = {
            "id": key,
            "priority": "P1" if key in ("TL-CLI-002", "TL-MCP-002") else "P0",
            "status": "BLOCKED",
            "evidence": "No client JUnit result.",
        }
    report = {
        "target": target,
        "tier": args.tier,
        "start": timestamp(),
        "tag": "unmeasured",
        "manifest_hash": "unmeasured",
        "observed_build": {},
        "images": {},
        "errors": [],
    }
    try:
        if target not in {"ephemeral", "homelab", "external"}:
            raise ValueError("Unknown TL_TARGET")
        raw = Path(os.environ["TL_MANIFEST"]).read_bytes()
        manifest = validate_manifest(
            json.loads(raw), expected_source_sha=os.environ.get("TL_EXPECTED_SHA")
        )
        report.update(
            manifest_hash=hashlib.sha256(raw).hexdigest(),
            tag=manifest["version"],
            images=manifest["images"],
        )
        if target != "ephemeral":
            image_file = os.environ.get("TL_DEPLOYMENT_IMAGES")
            report["image_verification"] = "unverified"
            if image_file:
                entries = json.loads(Path(image_file).read_text(encoding="utf-8-sig"))
                observed_images = {entry["component"]: entry for entry in entries}
                same = set(observed_images) == set(manifest["images"])
                for component, entry in observed_images.items():
                    wanted = manifest["images"][component]["source_ref"]
                    digest = entry["running"].rsplit("@", 1)[-1]
                    same = (
                        same
                        and entry["candidate"] == wanted
                        and digest in [wanted.rsplit("@", 1)[-1], *entry["children"]]
                    )
                report["deployed_images"] = observed_images
                report["image_verification"] = "matched" if same else "different"
            if report["image_verification"] != "matched":
                report["errors"].append(
                    "Running images differ from the candidate or lack digest evidence."
                )
        for key in ("TL_API", "TL_WEB"):
            url = urlparse(os.environ[key])
            if (
                url.scheme not in ("http", "https")
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.path not in ("", "/")
            ):
                raise ValueError(key + " must be a plain service origin")
        env = {
            **os.environ,
            "TL_EXPECTED_SHA": manifest["source_sha"],
            "TL_RESULTS": str(directory),
            "TL_PYTHON": sys.executable,
        }
        cmd = [
            "node",
            "node_modules/@playwright/test/cli.js",
            "test",
            "--config",
            "playwright.release.config.ts",
            "--grep",
            "@" + args.tier,
        ]
        if target != "ephemeral":
            cmd.extend(["--grep-invert", "@ephemeral-only"])
        with (directory / "playwright-private.log").open("w", encoding="utf-8") as log:
            process = subprocess.run(
                cmd,
                cwd=ROOT / "frontend",
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        raw_results = json.loads(
            (directory / "playwright.json").read_text(encoding="utf-8")
        )
        if raw_results.get("errors"):
            report["errors"].append(
                "Playwright reported runner errors. Inspect private playwright.json."
            )
        seen = set()
        for spec in specs(raw_results.get("suites", [])):
            key = next(
                (k for k in inventory if spec["title"].startswith(k + " ")), None
            )
            if key is None or key in seen:
                raise ValueError("Unexpected or duplicate release case")
            seen.add(key)
            details = CATALOGUE[key.split()[0]]
            role = key.split()[1] if details["role"] == "per role" else details["role"]
            required_tags = {
                details["priority"],
                details["eligibility"],
                "area:" + details["area"],
                "journey:" + details["journey"],
                "role:" + role,
                args.tier,
            }
            if not required_tags.issubset({t.lstrip("@") for t in spec["tags"]}):
                raise ValueError("Playwright tags differ from catalogue: " + key)
            entry = spec["tests"]
            if len(entry) != 1:
                raise ValueError("Expected one Chromium project per case")
            results = entry[0]["results"]
            statuses = [r["status"] for r in results]
            messages = [str(r.get("error", {}).get("message", "")) for r in results]
            blocked = any(
                any(
                    marker in msg
                    for marker in (
                        "BLOCKED:",
                        "ECONNREFUSED",
                        "ERR_CONNECTION_REFUSED",
                        "ENOTFOUND",
                    )
                )
                for msg in messages
            )
            outcome = (
                "BLOCKED"
                if not results
                else "FLAKY"
                if statuses[-1] == "passed" and len(results) > 1
                else "PASSED"
                if statuses == ["passed"]
                else "BLOCKED"
                if blocked or statuses[-1] == "interrupted"
                else "SKIPPED"
                if statuses[-1] == "skipped"
                else "FAILED"
            )
            cases[key].update(
                status=outcome,
                attempts=statuses,
                evidence=spec["file"]
                + ":"
                + str(spec["line"])
                + " · "
                + ", ".join(statuses),
            )
            if blocked:
                cases[key]["classification"] = "environment"
                reason = next(
                    (
                        m.split("BLOCKED:", 1)[1].splitlines()[0].strip()
                        for m in messages
                        if "BLOCKED:" in m
                    ),
                    "Target connection unavailable.",
                )
                cases[key]["evidence"] += " · " + reason
            for r in results:
                for a in r.get("attachments", []):
                    if a["name"] not in {"observed-build", "request-evidence"}:
                        continue
                    observed = (
                        json.loads(base64.b64decode(a["body"]))
                        if "body" in a
                        else json.loads(Path(a["path"]).read_bytes())
                    )
                    if a["name"] == "observed-build":
                        report["observed_build"] = observed
                    else:
                        cases[key].setdefault("requests", []).append(observed)
        if process.returncode and all(cases[k]["status"] == "PASSED" for k in expected):
            report["errors"].append(
                "Playwright exited nonzero despite the recorded case results."
            )
        xml = directory / "clients.xml"
        with (directory / "pytest-private.log").open("w", encoding="utf-8") as log:
            selection = (
                "live"
                if target == "ephemeral" and args.tier != "smoke"
                else "live and not ephemeral"
            )
            client_process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "e2e-runner/live",
                    "-m",
                    selection,
                    "-q",
                    "--tb=short",
                    "--junitxml=" + str(xml),
                ],
                cwd=ROOT,
                env={**env, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        for test_case in ET.parse(xml).iter("testcase"):
            key = clients.get(test_case.get("name"))
            if key is None:
                raise ValueError("Unexpected client test result")
            status = next(
                (
                    value
                    for tag, value in (
                        ("error", "BLOCKED"),
                        ("failure", "FAILED"),
                        ("skipped", "SKIPPED"),
                    )
                    if test_case.find(tag) is not None
                ),
                "PASSED",
            )
            cases[key].update(
                status=status, evidence="clients.xml · " + test_case.get("name")
            )
        if client_process.returncode and all(
            cases[k]["status"] == "PASSED" for k in clients.values()
        ):
            report["errors"].append(
                "pytest exited nonzero after reporting client passes."
            )
        if target == "ephemeral" and args.tier != "smoke":
            from controls import verify

            report["controls"] = verify(directory, cases, manifest, env)
            if report["controls"]["status"] != "PASSED":
                report["errors"].append(
                    "Disposable build or identity negative controls lack proof."
                )
    except (
        KeyError,
        OSError,
        ValueError,
        ET.ParseError,
        subprocess.SubprocessError,
    ) as exc:
        report["errors"].append(
            f"Runner precondition or evidence error: {type(exc).__name__}: {exc}"
        )
    # The commissioning slice cannot sign a promotion until the full required set is implemented.
    for key, case in cases.items():
        details = CATALOGUE[key.split()[0]]
        case.update(
            title=details["title"],
            journey=details["journey"],
            use_case=details["use_case"],
            layer=details["layer"],
            role=key.split()[1] if details["role"] == "per role" else details["role"],
            priority=details["priority"],
        )
    verdict, reason = evaluate(list(cases.values()), report["errors"], args.tier)
    report.update(
        end=timestamp(), cases=list(cases.values()), verdict=verdict, reason=reason
    )
    report["selected_cases_passed"] = not report["errors"] and all(
        cases[k]["status"] == "PASSED" for k in [*expected, *clients.values()]
    )
    render(directory, report)
    print(f"{report['verdict']} · {directory / 'index.html'}", flush=True)
    return 0 if verdict == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
