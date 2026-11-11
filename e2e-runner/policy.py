"""Evaluate recorded attempts against independently enumerated release obligations."""

from datetime import date


def evaluate(cases, errors, tier, waivers=(), defects=()):
    if errors:
        return "NO_GO", "Runner or evidence validation failed."
    if any(d["severity"] in {"S1", "S2"} and d["open"] and d["new"] for d in defects):
        return "NO_GO", "A new critical or high-severity defect is open."
    p0 = [c for c in cases if c["priority"] == "P0"]
    incomplete = [c["id"] for c in p0 if c["status"] != "PASSED"]
    if not p0 or incomplete:
        return (
            "NO_GO",
            f"{len(incomplete)} required P0 cases lack a first-attempt pass.",
        )
    if tier == "smoke":
        return (
            "NO_GO",
            "Smoke passed. Promotion requires the full release-regression denominator.",
        )
    p1 = [c for c in cases if c["priority"] == "P1"]
    if not p1:
        return "NO_GO", "Release-regression P1 denominator is empty."
    valid = {
        w["test_id"]
        for w in waivers
        if w.get("owner")
        and w.get("approved_by")
        and w.get("defect_id")
        and date.fromisoformat(w["expires"]) >= date.today()
    }
    missed = [c for c in p1 if c["status"] != "PASSED"]
    if any(
        c["status"] in {"BLOCKED", "SKIPPED"} or c["id"] not in valid for c in missed
    ):
        return (
            "NO_GO",
            "A P1 case is unmeasured or lacks an approved, unexpired waiver.",
        )
    if (len(p1) - len(missed)) / len(p1) < 0.98 or any(
        c["priority"] == "P2" and c["status"] != "PASSED" for c in cases
    ):
        return (
            "CONDITIONAL_GO",
            "A named approval is required for the waived P1 score or P2 outcome.",
        )
    return (
        "GO",
        "All required P0 cases passed first time and P1 met the approved threshold.",
    )
