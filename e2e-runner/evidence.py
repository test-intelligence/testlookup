"""Correlate disposable service logs without publishing raw log payloads."""

import json
import re

from run import render

REQUEST_ID = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", re.I)


def attach_logs(directory, invoke):
    report = json.loads((directory / "results.json").read_bytes())
    requests = {
        r["request_id"]: r
        for c in report["cases"]
        for r in c.get("requests", [])
        if isinstance(r.get("request_id"), str)
        and REQUEST_ID.fullmatch(r["request_id"])
    }
    excerpts = {}
    if requests:
        result = invoke(
            "logs",
            "--no-color",
            "--since",
            report["start"],
            "backend",
            "worker",
            "worker-children",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for line in result.stdout.splitlines():
            service, sep, body = line.partition(" | ")
            if not sep:
                continue
            for identifier in set(REQUEST_ID.findall(body)) & requests.keys():
                if identifier in excerpts:
                    continue
                # Only project the record's service, ID and numeric HTTP status.
                # Raw access lines and arbitrary message text stay in the stack.
                match = re.search(
                    r'\bstatus(?:_code)?["\s]*[:=]\s*"?([1-5]\d\d)\b', body
                )
                excerpts[identifier] = {
                    "service": service.strip(),
                    "request_id": identifier,
                    "logged_status": int(match[1]) if match else None,
                }
    report["log_evidence"] = {
        "matched": excerpts,
        "unmatched": sorted(requests.keys() - excerpts.keys()),
    }
    render(directory, report)
