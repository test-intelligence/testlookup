"""Run the repeatable API slice of the agentic live definition of done.

The probe uses the generated Postman collection as the request contract, then
invokes every independently invokable capability that is sync-eligible or
report-producing. It writes no credentials and emits a JSON evidence record.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

PUBLIC_STATUSES = frozenset({"in_progress", "completed", "failed", "passed"})


class VerificationError(RuntimeError):
    """A live response violated the published agent API contract."""


def _walk_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item.get("request"), dict):
            requests.append(item)
        if isinstance(item.get("item"), list):
            requests.extend(_walk_items(item["item"]))
    return requests


def verify_postman_contract(path: Path) -> None:
    """Fail if the generated collection no longer carries the live request."""
    collection = json.loads(path.read_text(encoding="utf-8"))
    matches = [item for item in _walk_items(collection.get("item", [])) if item.get("name") == "Invoke Agent"]
    if len(matches) != 1:
        raise VerificationError(f"expected one generated 'Invoke Agent' request, found {len(matches)}")
    request = matches[0]["request"]
    raw_url = request.get("url", {}).get("raw")
    raw_body = request.get("body", {}).get("raw", "")
    if request.get("method") != "POST" or raw_url != "{{baseUrl}}/api/v1/agents/{{agent_id}}/invoke":
        raise VerificationError("generated collection has the wrong invoke method or route")
    for variable in ("{{project_id}}", "{{agent_id}}", "{{test_run_id}}"):
        if variable not in raw_body:
            raise VerificationError(f"generated invoke body omits {variable}")


def select_targets(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Capabilities covered by the live invocation acceptance criterion."""
    targets = [
        entry
        for entry in catalog
        if entry.get("invokable") is True
        and (entry.get("sync_eligible") is True or entry.get("produces_report") is True)
    ]
    if not targets:
        raise VerificationError("catalog exposed no invokable sync/report capabilities")
    if not any(entry.get("sync_eligible") is True for entry in targets):
        raise VerificationError("catalog exposed no independently invokable sync capability")
    if not any(entry.get("produces_report") is True for entry in targets):
        raise VerificationError("catalog exposed no independently invokable report capability")
    return targets


def expected_terminal(entry: dict[str, Any]) -> str:
    """Reports await review at completed; non-report sync agents auto-pass."""
    return "completed" if entry.get("produces_report") is True else "passed"


def verify_invocation_result(
    entry: dict[str, Any],
    *,
    initial_http: int,
    payload: dict[str, Any],
) -> None:
    if initial_http != 202:
        raise VerificationError(f"{entry['agent_id']} initial response was HTTP {initial_http}, expected 202")
    status = payload.get("status")
    if status not in PUBLIC_STATUSES:
        raise VerificationError(f"{entry['agent_id']} exposed non-public status {status!r}")
    wanted = expected_terminal(entry)
    if status != wanted:
        raise VerificationError(f"{entry['agent_id']} ended {status!r}, expected {wanted!r}")
    review_state = (payload.get("review") or {}).get("state")
    wanted_review = "pending_review" if entry.get("produces_report") is True else "not_applicable"
    if review_state != wanted_review:
        raise VerificationError(
            f"{entry['agent_id']} review state was {review_state!r}, expected {wanted_review!r}"
        )


class JsonApi:
    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[int, Any]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise VerificationError(f"{method} {path} returned HTTP {exc.code}: {detail}") from exc


def run_live(
    *,
    base_url: str,
    collection: Path,
    token: str | None,
    role: str,
    timeout_seconds: float,
    poll_interval: float,
) -> dict[str, Any]:
    verify_postman_contract(collection)
    api = JsonApi(base_url, token)
    if not api.token:
        _, login = api.request("POST", f"/api/v1/auth/dev-login?role={role}")
        api.token = login["access_token"]

    catalog_http, catalog = api.request("GET", "/api/v1/agents/catalog")
    if catalog_http != 200 or not isinstance(catalog, list):
        raise VerificationError("catalog did not return HTTP 200 with a list")
    targets = select_targets(catalog)
    _, run_page = api.request("GET", "/api/v1/runs?size=100")
    runs = run_page.get("items", []) if isinstance(run_page, dict) else []
    if len(runs) < len(targets):
        raise VerificationError(f"seed data has {len(runs)} runs; {len(targets)} are required")

    results: list[dict[str, Any]] = []
    for entry, run in zip(targets, runs[: len(targets)], strict=True):
        agent_id = entry["agent_id"]
        body = {
            "project_id": run["project_id"],
            "input": {"agent_id": agent_id, "payload": {"test_run_id": run["id"]}},
            "mode": "async",
        }
        initial_http, payload = api.request(
            "POST",
            f"/api/v1/agents/{agent_id}/invoke",
            body=body,
            idempotency_key="live-dod-" + str(uuid.uuid4()),
        )
        deadline = time.monotonic() + timeout_seconds
        while payload.get("status") == "in_progress" and time.monotonic() < deadline:
            time.sleep(poll_interval)
            _, payload = api.request("GET", f"/api/v1/agents/invocations/{payload['id']}")
        verify_invocation_result(entry, initial_http=initial_http, payload=payload)
        results.append(
            {
                "agent_id": agent_id,
                "initial_http": initial_http,
                "terminal_status": payload["status"],
                "attempt": payload["attempt"],
                "review_state": payload["review"]["state"],
            }
        )
    return {
        "schema_version": 1,
        "base_url": base_url.rstrip("/"),
        "catalog_count": len(catalog),
        "target_count": len(targets),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--collection",
        type=Path,
        default=Path("architecture/api/agents.postman_collection.json"),
    )
    parser.add_argument("--token", default=os.environ.get("TESTLOOKUP_ACCESS_TOKEN"))
    parser.add_argument("--role", default="qa_engineer")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    evidence = run_live(
        base_url=args.base_url,
        collection=args.collection,
        token=args.token,
        role=args.role,
        timeout_seconds=args.timeout_seconds,
        poll_interval=args.poll_interval,
    )
    rendered = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
