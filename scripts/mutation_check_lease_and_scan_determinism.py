"""Prove the two de-flaked timing tests still kill the bugs they were written for.

Both tests used to assert a real property by timing it on a wall clock, and
both were rewritten to measure the property itself -- the cluster-slot lease
races on a virtual clock, the pre-parse scan in characters walked rather than
seconds. A rewrite like that can quietly stop testing anything, so every fix
the two files exist for is broken here and the tests have to notice.

Corrupts the tree while it runs: each mutation asserts it applied exactly
once, the tests are run against it, and the file is restored from the bytes
read before, whatever happened. Never run it as part of the push gate.

    python scripts/mutation_check_lease_and_scan_determinism.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

SEMAPHORE = "backend/app/services/llm_cluster_semaphore.py"
UPLOAD_LIMITS = "backend/app/services/upload_limits.py"
LEASE_TESTS = "tests/services/test_llm_slot_lease_loss.py"
LINEARITY_TEST = (
    "tests/regression/test_upload_structural_count.py::test_the_count_is_linear_in_the_report"
)


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str
    safe: str
    unsafe: str
    test: str


MUTATIONS = (
    Mutation(
        "renew-deadline-timed-from-the-reply",
        SEMAPHORE,
        "            if renewed:\n                lease_from = sent\n",
        "            if renewed:\n                lease_from = self._now()\n",
        LEASE_TESTS,
    ),
    Mutation(
        "acquire-lease-timed-from-the-reply",
        SEMAPHORE,
        "            acquire_sent = self._now()\n            try:\n"
        "                held = await self.try_acquire(token)\n",
        "            try:\n                held = await self.try_acquire(token)\n"
        "                acquire_sent = self._now()\n",
        LEASE_TESTS,
    ),
    Mutation(
        "renew-not-bounded-by-the-lease",
        SEMAPHORE,
        "                    renewed = await asyncio.wait_for(self.renew(token), timeout=deadline - sent)",
        "                    renewed = await self.renew(token)",
        LEASE_TESTS,
    ),
    Mutation(
        "failing-renew-judged-only-when-redis-says-so",
        SEMAPHORE,
        "            if renewed is False or self._now() + interval >= deadline:",
        "            if renewed is False:",
        LEASE_TESTS,
    ),
    Mutation(
        "no-margin-before-the-lease-lapses",
        SEMAPHORE,
        "        return min(interval / 2.0, max(0.01, self.lease_seconds * 0.05))",
        "        return 0.0",
        LEASE_TESTS,
    ),
    Mutation(
        "scan-rewalks-the-tail-from-every-quote",
        UPLOAD_LIMITS,
        "            start, position = position - 1, rest.end()",
        "            start = position - 1",
        LINEARITY_TEST,
    ),
)


def run_test(target: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:testlookup", "-x", target],
        cwd=BACKEND,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )


def restore(path: Path, content: bytes) -> None:
    for attempt in range(10):
        try:
            path.write_bytes(content)
            return
        except OSError:
            if attempt == 9:
                raise
            time.sleep(0.1)


def main() -> int:
    for mutation in MUTATIONS:
        path = ROOT / mutation.path
        original = path.read_bytes()
        source = original.decode("utf-8")
        if source.count(mutation.safe) != 1:
            raise AssertionError(
                f"{mutation.name}: the mutation site appears "
                f"{source.count(mutation.safe)} times, not once"
            )
        baseline = run_test(mutation.test)
        if baseline.returncode != 0:
            raise AssertionError(
                f"{mutation.name} baseline failed\n{baseline.stdout}{baseline.stderr}"
            )
        try:
            mutated_source = source.replace(mutation.safe, mutation.unsafe, 1)
            path.write_bytes(mutated_source.encode("utf-8"))
            if path.read_bytes() == original:
                raise AssertionError(f"{mutation.name}: the mutation never reached disk")
            mutated = run_test(mutation.test)
            if mutated.returncode == 0:
                raise AssertionError(
                    f"{mutation.name} SURVIVED\n{mutated.stdout}{mutated.stderr}"
                )
        finally:
            restore(path, original)
        print(f"killed: {mutation.name}")
    print(f"lease and scan determinism: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
