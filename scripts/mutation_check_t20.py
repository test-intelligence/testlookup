"""Prove T20's API and UI tests kill review-summary regressions."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_TEST = (
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:testlookup",
    "backend/tests/regression/test_agents_pipeline_review_summary.py",
)
FRONTEND_TEST = (
    "npm.cmd",
    "run",
    "test",
    "--",
    "src/pages/AgentStatusPage.publicStatus.test.tsx",
)

# (test kind, file, correct source, mutation, exact replacement count).  The
# count assertion prevents a refactor from making a mutation silently stop
# applying while the harness still reports success.
MUTATIONS = (
    (
        "backend",
        "backend/app/routers/agents.py",
        'ReviewRequest.subject_type == "pipeline_run"',
        'ReviewRequest.subject_type == "invocation"',
        1,
    ),
    (
        "backend",
        "backend/app/routers/agents.py",
        '{"state": row.state, "settled_at": row.reviewed_at}',
        '{"state": row.state, "settled_at": None}',
        1,
    ),
    (
        "backend",
        "backend/app/routers/agents.py",
        "    await _attach_run_context(db, pipelines)\n    await _attach_review_summaries(db, pipelines)\n    return pipelines",
        "    await _attach_run_context(db, pipelines)\n    return pipelines",
        1,
    ),
    (
        "backend",
        "backend/app/models/schemas.py",
        "    settled_at: Optional[datetime] = None",
        "    reviewed_at: Optional[datetime] = None",
        1,
    ),
    (
        "frontend",
        "frontend/src/pages/AgentStatusPage.tsx",
        "pipeline.review_summary?.state === 'accepted'",
        "pipeline.review_summary?.state === 'rejected'",
        1,
    ),
    (
        "frontend",
        "frontend/src/pages/AgentStatusPage.tsx",
        "new Date(pipeline.review_summary.settled_at).toLocaleString()",
        "new Date(pipeline.completed_at ?? '').toLocaleString()",
        1,
    ),
)


def main() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "backend")
    for index, (kind, relative, good, bad, count) in enumerate(MUTATIONS, start=1):
        path = ROOT / relative
        original = path.read_bytes()
        good_bytes = good.encode("utf-8")
        if original.count(good_bytes) != count:
            raise AssertionError(
                f"mutation did not apply {count} time(s): {relative}: {good!r}"
            )
        mutated = original.replace(good_bytes, bad.encode("utf-8"))
        if mutated == original:
            raise AssertionError(f"mutation did not change source: {relative}: {good!r}")
        path.write_bytes(mutated)
        try:
            command = (
                BACKEND_TEST
                + ()
                if kind == "backend"
                else FRONTEND_TEST
            )
            run = subprocess.run(
                command,
                cwd=ROOT if kind == "backend" else ROOT / "frontend",
                env=env,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            if run.returncode == 0:
                raise AssertionError(f"mutation survived: {relative}: {bad!r}")
        finally:
            path.write_bytes(original)
    print(f"T20 mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
