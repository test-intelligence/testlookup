#!/usr/bin/env python3
"""Post-launch smoke check — verify a freshly started TestLookup stack is healthy.

Run after `make quickstart` / `make dev` / `make demo`:

    python scripts/smoke.py            # probes localhost:8000 / :3000
    TL_API=http://host:8000 TL_WEB=http://host:3000 python scripts/smoke.py

Stdlib only (urllib) — no extra install needed on the host. Probes the real
readiness/liveness/API/frontend endpoints and prints a clear PASS/FAIL report;
exits non-zero if any CORE check fails so it's usable in CI / a setup script.
Demo-data checks are informational (a bare `make dev` has no seed data yet).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

API = os.environ.get("TL_API", "http://localhost:8000").rstrip("/")
WEB = os.environ.get("TL_WEB", "http://localhost:3000").rstrip("/")
TIMEOUT = float(os.environ.get("TL_SMOKE_TIMEOUT", "10"))


@dataclass
class Check:
    name: str
    ok: bool
    core: bool       # core failures fail the smoke; info ones only warn
    detail: str = ""


def probe(url: str, *, method: str = "GET") -> tuple[int, str, str]:
    """Return (status, body, error). Never raises — a down endpoint → (0, '', err)."""
    try:
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (local URL)
            return resp.status, resp.read(2048).decode("utf-8", "replace"), ""
    except urllib.error.HTTPError as e:
        # Keep the body: a 503 from /health/ready names the dependency that is down.
        try:
            body = e.read(2048).decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body, f"HTTP {e.code}"
    except Exception as e:  # connection refused, timeout, DNS, …
        return 0, "", f"{type(e).__name__}: {e}"


def run_checks() -> list[Check]:
    checks: list[Check] = []

    # /health/ready is public and reports PostgreSQL, MongoDB and Redis by name.
    # (/health/details reports more, but needs a sign-in; this script holds no
    # credential.)
    st, ready_body, err = probe(f"{API}/health/ready")
    deps = _deps_summary(_json(ready_body))
    checks.append(Check("backend readiness (/health/ready)", st == 200, True,
                        " -- ".join(part for part in (err or f"HTTP {st}", deps) if part)))

    st, _, err = probe(f"{API}/health/live")
    checks.append(Check("backend liveness (/health/live)", st == 200, True,
                        err or f"HTTP {st}"))

    st, _, err = probe(f"{API}/api-docs/openapi.json")
    if st == 404:
        # API_DOCS_ENABLED=false turns the API reference off; that is a choice,
        # not a fault. Any other failure is still a core failure.
        checks.append(Check("API schema (/api-docs/openapi.json)", False, False,
                            "HTTP 404 -- not served (API_DOCS_ENABLED=false turns it off)"))
    else:
        checks.append(Check("API schema (/api-docs/openapi.json)", st == 200, True,
                            err or f"HTTP {st}"))

    st, body, err = probe(f"{WEB}/")
    web_ok = st == 200 and ("root" in body or "<title" in body.lower() or "<!doctype" in body.lower())
    checks.append(Check("frontend (/)", web_ok, True, err or f"HTTP {st}"))

    # Informational: the per-dependency status the readiness probe reported.
    if deps:
        checks.append(Check("dependency details", True, False, deps))
    return checks


def _json(body: str) -> dict:
    try:
        data = json.loads(body) if body else {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _deps_summary(data: dict) -> str:
    deps = data.get("dependencies") or data.get("checks") or {}
    if isinstance(deps, dict) and deps:
        return ", ".join(f"{k}={(v.get('status') if isinstance(v, dict) else v)}" for k, v in deps.items())
    return str(data.get("status", ""))


def summarize(checks: list[Check]) -> tuple[bool, str]:
    """Pure: fold checks into (all_core_ok, report_text). Core failures fail."""
    core_failures = [c for c in checks if c.core and not c.ok]
    ok = not core_failures
    lines = []
    for c in checks:
        if c.core:
            mark = "PASS" if c.ok else "FAIL"
        else:
            mark = "info"
        suffix = f" -- {c.detail}" if (c.detail and (not c.ok or not c.core)) else ""
        lines.append(f"  [{mark}] {c.name}{suffix}")
    header = (
        "OK: Smoke check passed -- the stack is up."
        if ok else
        f"FAILED: smoke check -- {len(core_failures)} core check(s) down."
    )
    return ok, header + "\n" + "\n".join(lines)


def main() -> int:
    print(f"Smoke-checking API={API} WEB={WEB} ...")
    ok, report = summarize(run_checks())
    print(report)
    if not ok:
        print("\nIs the stack up? Try: make quickstart  (then re-run: make smoke)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
