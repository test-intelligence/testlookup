"""Summarize completed reports for one target, tier and candidate."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
import statistics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "runs", type=Path, help="Directory containing run-01, run-02, ..."
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    files = sorted(args.runs.glob("run-*/results.json"))
    if not files:
        parser.error("No completed run reports found")
    reports = [json.loads(f.read_bytes()) for f in files]
    identity = {(r["target"], r["tier"], r["manifest_hash"]) for r in reports}
    if len(identity) != 1:
        parser.error("Runs must share one target, tier and candidate manifest")
    sequence = []
    outcomes = Counter()
    by_case = defaultdict(Counter)
    seconds = []
    for f, r in zip(files, reports):
        counts = Counter(c["status"] for c in r["cases"])
        outcomes.update(counts)
        for c in r["cases"]:
            by_case[c["id"]][c["status"]] += 1
        seconds.append(
            (
                datetime.fromisoformat(r["end"]) - datetime.fromisoformat(r["start"])
            ).total_seconds()
        )
        sequence.append(
            {
                "run": f.parent.name,
                "verdict": r["verdict"],
                "seconds": round(seconds[-1], 2),
                "counts": dict(counts),
                "controls": r.get("controls", {}).get("status"),
            }
        )
    completed = sum(outcomes[k] for k in ("PASSED", "FAILED", "FLAKY"))
    summary = {
        "target": reports[0]["target"],
        "tier": reports[0]["tier"],
        "manifest_hash": reports[0]["manifest_hash"],
        "runs": sequence,
        "outcomes": dict(outcomes),
        "completed_executions": completed,
        "flaky_fraction": outcomes["FLAKY"] / completed if completed else None,
        "median_test_seconds": statistics.median(seconds),
        "slowest_test_window_seconds": max(seconds),
        "by_case": dict(by_case),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"{len(files)} reports; {completed} executed; {dict(outcomes)}")


if __name__ == "__main__":
    main()
