"""AI-history rendering for ``testlookup ai-history`` and ``testlookup
compare-ai`` (AIH-07) -- pure functions.

The bodies are the REST API's, unchanged (W03 contract section 6):

* ``GET /api/v1/runs/{run_id}/ai-history`` (``AIHistoryListResponse``): the
  stored AI versions of one run, newest first;
* ``GET /api/v1/runs/{run_id}/ai-history/diff`` (``AIDigestDiffResponse``): two
  versions of the same run, structurally diffed;
* ``GET /api/v1/runs/{run_id}/ai-compare`` (``AICompareResponse``): the run's AI
  analysis against the previous run of its suite and branch.

Nothing here makes a request or writes to a stream: every function returns
lines, so every view is testable byte for byte (``cli/tests/golden``).

**Honest by construction.** ``null`` is "not known", never 0 or "no change": a
version without a verdict prints ``-``; a compare without a baseline says
``no previous run: <why>``; a side that was never analysed says so instead of
reading as "nothing changed". The engine is printed beside every version and an
engine change (rules vs LLM) comes first in a diff, because a category change
under a new engine is not necessarily a product change.

**Names are untrusted.** Test names, suite names, branch names, cluster labels,
root-cause text and model names come from CI reports or LLM output: every one
is flattened to one line and passed through ``clean`` (a console that cannot
encode a CJK suite name gets ``?``). The command prints these lines as
:class:`rich.text.Text`, so ``[bold]`` in a test name is that text, never Rich
markup.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

#: A cell or figure the API did not have (``null``).
MISSING = "-"
#: Rows listed per section of a diff (the REST body keeps every row).
MAX_ROWS = 20
HEADLINE_CHARS = 60

KIND_LABELS = {"analysis_digest": "analysis digest", "run_summary": "run summary"}

Clean = Callable[[str], str]


def _identity(text: str) -> str:
    return text


def one_line(value: Any, clean: Clean = _identity, *, limit: int | None = None) -> str:
    """An untrusted string, flattened to one line, encodable, optionally cut."""
    if value is None:
        return MISSING
    text = " ".join(str(value).split())
    if not text:
        return MISSING
    if limit is not None and len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return clean(text)


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def format_when(value: Any) -> str:
    """``2026-10-14T10:00:00+02:00`` -> ``2026-10-14 08:00`` (UTC)."""
    if not value:
        return MISSING
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:16]
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.strftime("%Y-%m-%d %H:%M")


def _signed(value: Any) -> str:
    number = _int(value)
    return MISSING if number is None else f"{number:+d}"


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


# ── testlookup ai-history (the version list) ────────────────────────────────


def engine_text(version: dict, clean: Clean = _identity) -> str:
    """``llm llama3.1`` / ``rules`` / ``-``: the analysis engine and model."""
    parts = [one_line(version.get(key), clean) for key in ("analysis_mode", "llm_model") if version.get(key)]
    return " ".join(parts) or MISSING


def verdict_text(version: dict) -> str:
    verdict = version.get("verdict")
    risk = _int(version.get("risk_score"))
    if not verdict:
        return MISSING if risk is None else f"risk {risk}"
    return f"{verdict} risk {risk}" if risk is not None else str(verdict)


def version_flags(version: dict) -> str:
    flags = []
    if version.get("fallback_used"):
        flags.append("[fallback]")
    elif version.get("deterministic"):
        flags.append("[deterministic]")
    if version.get("trigger") == "backfill":
        flags.append("[backfilled]")
    if version.get("is_latest"):
        flags.append("[latest]")
    return " ".join(flags)


def version_rows(versions: list[dict], clean: Clean = _identity) -> list[str]:
    """``v3  2026-10-14 10:00  llm llama3.1  NO_GO risk 72  failing 7  [fallback]``,
    one per version, columns aligned."""
    rows: list[list[str]] = []
    for version in versions:
        failing = _int(version.get("failing_tests"))
        cells = [
            f"v{version.get('version', '?')}",
            format_when(version.get("generated_at") or version.get("created_at")),
            engine_text(version, clean),
            verdict_text(version),
            f"failing {failing if failing is not None else MISSING}",
            version_flags(version),
        ]
        if version.get("headline"):
            cells.append(one_line(version.get("headline"), clean, limit=HEADLINE_CHARS))
        rows.append(cells)
    widths = [max((len(row[i]) for row in rows if len(row) > i), default=0) for i in range(5)]
    lines = []
    for row in rows:
        padded = [cell.ljust(widths[i]) if i < 5 else cell for i, cell in enumerate(row)]
        lines.append("  ".join(cell for cell in padded if cell != "").rstrip())
    return lines


def history_lines(body: dict, *, separator: str = " · ", clean: Clean = _identity) -> list[str]:
    """The whole ``ai-history`` table view."""
    versions = [v for v in body.get("versions") or [] if isinstance(v, dict)]
    kind = KIND_LABELS.get(str(body.get("kind")), one_line(body.get("kind"), clean))
    total = _int(body.get("total"))
    total = len(versions) if total is None else total
    heading = [f"AI history{separator}run {str(body.get('run_id') or '')[:8]}", kind, _plural(total, "version")]
    latest = _int(body.get("latest_version"))
    if latest is not None:
        heading.append(f"latest v{latest}")
    lines = [separator.join(heading)]
    if not versions:
        lines.append("No AI versions stored for this run yet.")
        live = body.get("live") if isinstance(body.get("live"), dict) else {}
        if live.get("available"):
            lines.append("The run has a current AI analysis; it is stored as a version when the pipeline next completes.")
        return lines
    lines.append("")
    lines += version_rows(versions, clean)
    if body.get("truncated") or total > len(versions):
        lines.append(f"{len(versions)} of {total} versions shown (newest first; -n/--limit up to 100).")
    return lines


# ── the diff (shared by --diff and compare-ai's details) ────────────────────


def ref_label(ref: Any) -> str:
    """``v3``, or ``current data`` for a live side."""
    if not isinstance(ref, dict):
        return MISSING
    if ref.get("source") == "live" or ref.get("ref") == "live":
        return "current data"
    version = _int(ref.get("version"))
    return f"v{version}" if version is not None else one_line(ref.get("ref"))


def _pair(block: Any, arrow: str, clean: Clean = _identity) -> str | None:
    if not isinstance(block, dict):
        return None

    def side(value: Any) -> str:
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, dict):
            return ", ".join(f"{k} {v}" for k, v in sorted(value.items())) or MISSING
        return one_line(value, clean)

    return f"{side(block.get('from'))} {arrow} {side(block.get('to'))}"


def provenance_lines(provenance: Any, *, arrow: str, separator: str, clean: Clean) -> list[str]:
    if not isinstance(provenance, dict) or not provenance.get("changed"):
        return []
    parts = []
    for key, label in (("engine", "engine"), ("model", "model"), ("fallback_used", "fallback"),
                       ("deterministic", "deterministic"), ("mode_counts", "tests per engine")):
        pair = _pair(provenance.get(key), arrow, clean)
        if pair:
            parts.append(f"{label} {pair}")
    lines = ["Provenance changed: " + (separator.join(parts) if parts else "yes")]
    if provenance.get("note"):
        lines.append(f"  {one_line(provenance['note'], clean)}")
    if provenance.get("engine_only"):
        lines.append("  Only the analysis engine changed: nothing else material differs.")
    return lines


def verdict_line(verdict: Any, *, arrow: str, separator: str) -> str:
    verdict = verdict if isinstance(verdict, dict) else {}
    frm, to = verdict.get("from"), verdict.get("to")
    if frm is None and to is None:
        head = "Verdict: none on either side"
    elif verdict.get("changed") or frm != to:
        direction = verdict.get("direction")
        head = f"Verdict {frm or MISSING} {arrow} {to or MISSING}" + (f" ({direction})" if direction else "")
    else:
        head = f"Verdict {to} (unchanged)"
    from_risk, to_risk = _int(verdict.get("from_risk")), _int(verdict.get("to_risk"))
    if from_risk is not None or to_risk is not None:
        risk = (f"risk {from_risk if from_risk is not None else MISSING} {arrow} "
                f"{to_risk if to_risk is not None else MISSING}")
        if _int(verdict.get("risk_delta")) is not None:
            risk += f" ({_signed(verdict.get('risk_delta'))})"
        head += separator + risk
    return head


def _cluster_label(item: dict, clean: Clean) -> str:
    return one_line(item.get("label") or item.get("signature"), clean, limit=80)


def cluster_lines(clusters: Any, *, mode: str, arrow: str, clean: Clean) -> list[str]:
    if not isinstance(clusters, dict):
        return []
    added = [c for c in clusters.get("added") or [] if isinstance(c, dict)]
    removed = [c for c in clusters.get("removed") or [] if isinstance(c, dict)]
    matched = [c for c in clusters.get("matched") or [] if isinstance(c, dict)]
    resized = [c for c in matched if c.get("resized")]
    cross = mode == "cross_run"
    lines: list[str] = []
    for title, items, mark in (
        ("New root causes" if cross else "Added clusters", added, "+"),
        ("Resolved" if cross else "Removed clusters", removed, "-"),
    ):
        if not items:
            continue
        lines.append(f"{title} ({len(items)}):")
        for item in items[:MAX_ROWS]:
            size = _int(item.get("size"))
            lines.append(f"  {mark} {_cluster_label(item, clean)} ({_plural(size or 0, 'test')})")
        if len(items) > MAX_ROWS:
            lines.append(f"  ... {len(items) - MAX_ROWS} more")
    if cross and matched:
        lines.append(f"Persisting ({len(matched)}, {len(resized)} resized)" + (":" if resized else ""))
    elif resized:
        lines.append(f"Resized clusters ({len(resized)}):")
    for item in resized[:MAX_ROWS]:
        label = one_line(item.get("label_to") or item.get("label_from") or item.get("signature_to"), clean, limit=80)
        lines.append(f"  ~ {label} ({item.get('size_from', MISSING)} {arrow} {item.get('size_to', MISSING)} tests)")
    if len(resized) > MAX_ROWS:
        lines.append(f"  ... {len(resized) - MAX_ROWS} more")
    return lines


def _confidence(value: Any) -> str:
    number = _int(value)
    return MISSING if number is None else f"{number}%"


def changed_test_line(item: dict, *, arrow: str, clean: Clean) -> str:
    name = one_line(item.get("test_name") or item.get("test_fingerprint"), clean, limit=80)
    parts = []
    category = item.get("category") if isinstance(item.get("category"), dict) else None
    if category:
        parts.append(f"{category.get('from') or MISSING} {arrow} {category.get('to') or MISSING}")
    confidence = item.get("confidence") if isinstance(item.get("confidence"), dict) else None
    if confidence:
        parts.append(f"confidence {_confidence(confidence.get('from'))} {arrow} {_confidence(confidence.get('to'))}")
    flaky = item.get("flaky") if isinstance(item.get("flaky"), dict) else None
    if flaky:
        parts.append(f"flaky {'yes' if flaky.get('from') else 'no'} {arrow} {'yes' if flaky.get('to') else 'no'}")
    if not parts and item.get("root_cause_changed"):
        parts.append("root cause reworded")
    return f"  ~ {name}: " + ", ".join(parts) if parts else f"  ~ {name}"


def test_lines(tests: Any, *, mode: str, arrow: str, separator: str, clean: Clean) -> list[str]:
    if not isinstance(tests, dict):
        return []
    added = [t for t in tests.get("added") or [] if isinstance(t, dict)]
    removed = [t for t in tests.get("removed") or [] if isinstance(t, dict)]
    changed = [t for t in tests.get("changed") or [] if isinstance(t, dict)]
    counts = tests.get("counts") if isinstance(tests.get("counts"), dict) else {}
    n_added = _int(counts.get("added")) if _int(counts.get("added")) is not None else len(added)
    n_removed = _int(counts.get("removed")) if _int(counts.get("removed")) is not None else len(removed)
    n_changed = _int(counts.get("changed")) if _int(counts.get("changed")) is not None else len(changed)
    n_material = _int(counts.get("material_changed"))
    if not (n_added or n_removed or n_changed):
        return []
    cross = mode == "cross_run"
    summary = [
        f"{n_added} {'new failing' if cross else 'added'}",
        f"{n_removed} {'resolved' if cross else 'removed'}",
        f"{n_changed} changed" + (f" ({n_material} material)" if n_material is not None else ""),
    ]
    unchanged = _int(tests.get("persisting_unchanged"))
    if unchanged is not None:
        summary.append(f"{unchanged} unchanged")
    lines = ["Tests: " + separator.join(summary)]
    for item in changed[:MAX_ROWS]:
        lines.append(changed_test_line(item, arrow=arrow, clean=clean))
    if len(changed) > MAX_ROWS:
        lines.append(f"  ... {len(changed) - MAX_ROWS} more changed")
    for mark, items in (("+", added), ("-", removed)):
        for item in items[:MAX_ROWS]:
            name = one_line(item.get("test_name") or item.get("test_fingerprint"), clean, limit=80)
            lines.append(f"  {mark} {name} [{item.get('category') or MISSING} {_confidence(item.get('confidence'))}]")
        if len(items) > MAX_ROWS:
            lines.append(f"  ... {len(items) - MAX_ROWS} more")
    if tests.get("truncated"):
        lines.append("  (the server cut these lists at 200 rows; the counts are complete)")
    return lines


def diff_lines(diff: Any, *, arrow: str = "->", separator: str = " · ", clean: Clean = _identity) -> list[str]:
    """Every section of an ``AIDigestDiff``, provenance first."""
    if not isinstance(diff, dict):
        return ["No diff available."]
    if diff.get("identical"):
        return ["Identical: both sides have the same AI content."]
    if diff.get("has_changes") is False:
        return ["No differences."]
    mode = str(diff.get("mode") or "versions")
    lines = provenance_lines(diff.get("provenance"), arrow=arrow, separator=separator, clean=clean)
    lines.append(verdict_line(diff.get("verdict"), arrow=arrow, separator=separator))
    categories = diff.get("categories") if isinstance(diff.get("categories"), dict) else {}
    changed = [c for c in categories.get("changed") or [] if isinstance(c, dict)]
    if changed:
        lines.append("Categories: " + separator.join(
            f"{one_line(c.get('category'), clean)} {c.get('from', MISSING)} {arrow} {c.get('to', MISSING)} "
            f"({_signed(c.get('delta'))})"
            for c in changed
        ))
    lines += cluster_lines(diff.get("clusters"), mode=mode, arrow=arrow, clean=clean)
    lines += test_lines(diff.get("tests"), mode=mode, arrow=arrow, separator=separator, clean=clean)
    actions = diff.get("actions") if isinstance(diff.get("actions"), dict) else {}
    for title, key, mark in (("Actions added", "added", "+"), ("Actions removed", "removed", "-")):
        items = actions.get(key) or []
        if items:
            lines.append(f"{title} ({len(items)}):")
            lines += [f"  {mark} {one_line(item, clean, limit=120)}" for item in items[:MAX_ROWS]]
    return lines


def diff_view_lines(body: dict, *, arrow: str = "->", separator: str = " · ", clean: Clean = _identity) -> list[str]:
    """``ai-history --diff A..B``: a heading naming both sides, then the diff."""
    frm, to = body.get("from"), body.get("to")
    build = (to or {}).get("build_number") if isinstance(to, dict) else None
    run = f"run #{one_line(build, clean)}" if build else f"run {str(body.get('run_id') or '')[:8]}"
    heading = f"AI diff{separator}{run}{separator}{ref_label(frm)} {arrow} {ref_label(to)}"
    engines = []
    for ref in (frm, to):
        if isinstance(ref, dict):
            engines.append(engine_text({"analysis_mode": ref.get("analysis_mode"), "llm_model": ref.get("llm_model")},
                                       clean))
    lines = [heading]
    if len(engines) == 2:
        lines.append(f"Engines: {engines[0]} {arrow} {engines[1]}")
    lines.append("")
    return lines + diff_lines(body.get("diff"), arrow=arrow, separator=separator, clean=clean)


# ── testlookup compare-ai ───────────────────────────────────────────────────


def baseline_label(baseline: dict, clean: Clean = _identity) -> str:
    """``#1233``: the run's sequence number, else its build, else its id."""
    seq = _int(baseline.get("run_seq"))
    if seq is not None:
        return f"#{seq}"
    if baseline.get("build_number"):
        return f"build {one_line(baseline['build_number'], clean)}"
    return str(baseline.get("run_id") or "")[:8] or MISSING


def compare_headline(body: dict, *, arrow: str = "->", separator: str = " · ", clean: Clean = _identity) -> str:
    """``vs #1233 (checkout · main): verdict GO -> NO_GO · new root causes 1 ·
    resolved 1 · 3 tests changed category``."""
    baseline = body.get("baseline") if isinstance(body.get("baseline"), dict) else None
    message = one_line(body.get("message") or body.get("reason") or "no baseline", clean)
    if baseline is None:
        return f"no previous run: {message}"
    scope = separator.join((
        one_line(baseline.get("primary_suite_name") or "no suite", clean),
        one_line(baseline.get("branch") or "no branch", clean),
    ))
    head = f"vs {baseline_label(baseline, clean)} ({scope})"
    diff = body.get("diff") if isinstance(body.get("diff"), dict) else None
    if diff is None:
        return f"{head}: not compared: {message}"
    verdict = diff.get("verdict") if isinstance(diff.get("verdict"), dict) else {}
    frm, to = verdict.get("from"), verdict.get("to")
    if frm is None and to is None:
        verdict_part = "verdict unknown"
    elif frm == to:
        verdict_part = f"verdict {to} (unchanged)"
    else:
        verdict_part = f"verdict {frm or MISSING} {arrow} {to or MISSING}"
    clusters = diff.get("clusters") if isinstance(diff.get("clusters"), dict) else {}
    counts = clusters.get("counts") if isinstance(clusters.get("counts"), dict) else {}
    new_causes = _int(counts.get("added"))
    resolved = _int(counts.get("removed"))
    tests = diff.get("tests") if isinstance(diff.get("tests"), dict) else {}
    category_changes = sum(
        1 for item in tests.get("changed") or []
        if isinstance(item, dict) and "category" in (item.get("kinds") or [])
    )
    more = "+" if tests.get("truncated") else ""
    parts = [
        verdict_part,
        f"new root causes {new_causes if new_causes is not None else len(clusters.get('added') or [])}",
        f"resolved {resolved if resolved is not None else len(clusters.get('removed') or [])}",
        f"{category_changes}{more} {'test' if category_changes == 1 and not more else 'tests'} changed category",
    ]
    return f"{head}: " + separator.join(parts)


def compare_lines(body: dict, *, arrow: str = "->", separator: str = " · ", clean: Clean = _identity) -> list[str]:
    """The whole ``compare-ai`` table view: the headline, the narrative label
    (``deterministic summary`` / ``AI narrative``) and the ``ai_changes``
    bullets."""
    lines = [compare_headline(body, arrow=arrow, separator=separator, clean=clean)]
    baseline = body.get("baseline") if isinstance(body.get("baseline"), dict) else None
    if baseline is not None and baseline.get("branch_mismatch"):
        lines.append(f"Note: the baseline you chose is on another branch ({one_line(baseline.get('branch'), clean)}).")
    narrative = body.get("narrative") if isinstance(body.get("narrative"), dict) else None
    if narrative is None:
        return lines
    label = one_line(narrative.get("label") or narrative.get("source"), clean)
    summary = narrative.get("executive_summary")
    lines.append(f"{label}: {one_line(summary, clean)}" if summary else label)
    for change in narrative.get("ai_changes") or []:
        lines.append(f"  - {one_line(change, clean, limit=160)}")
    return lines


def verdict_direction(body: Any) -> str | None:
    """``worse`` / ``better`` / ``same``, or ``None`` when no verdict could be
    compared (no baseline, a side not analysed, a verdict missing)."""
    diff = body.get("diff") if isinstance(body, dict) else None
    verdict = diff.get("verdict") if isinstance(diff, dict) else None
    direction = verdict.get("direction") if isinstance(verdict, dict) else None
    return direction if direction in ("worse", "better", "same") else None


__all__ = [
    "compare_headline",
    "compare_lines",
    "diff_lines",
    "diff_view_lines",
    "history_lines",
    "verdict_direction",
    "version_rows",
]
