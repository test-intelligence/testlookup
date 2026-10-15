"""Markdown for the AI-history tools (AIH-07): ``get_ai_history``,
``diff_ai_versions`` and ``compare_ai_with_previous``.

Pure functions over the REST bodies (W03 contract section 6):
``GET /api/v1/runs/{run_id}/ai-history`` (``AIHistoryListResponse``),
``GET /api/v1/runs/{run_id}/ai-history/diff`` (``AIDigestDiffResponse``) and
``GET /api/v1/runs/{run_id}/ai-compare`` (``AICompareResponse``). The tools
return the summary built here beside the body itself.

Honest by construction:

* ``null`` means "not known" -- written ``—``, never 0 and never "no change".
  A compare without a baseline says why; a side that was never analysed is
  "not compared", not "nothing changed".
* Provenance comes first: a version analysed by rules and one analysed by an LLM
  differ because the ENGINE differs; the engine change is printed before any
  category change so it is not read as a product change.
* The narrative is labelled for what it is: ``deterministic summary`` when no
  LLM wrote it (offline, cost cap), ``AI narrative`` when one did.
* Names (tests, suites, branches, cluster labels, models) and root-cause text
  come from CI reports and LLM output: they are flattened to one line, ``|``
  and backticks neutralised and cut to 80 characters, so a hostile name cannot
  break a table or open a code span (the ``execution_format.py`` rules).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

MISSING = "—"
MAX_NAME = 80
#: Rows per table in a summary (the structured body keeps every row).
MAX_ROWS = 15
ARROW = "→"

KIND_LABELS = {"analysis_digest": "analysis digest", "run_summary": "run summary"}
NARRATIVE_LABELS = {"deterministic": "deterministic summary", "llm": "AI narrative"}


def name(value: Any, limit: int = MAX_NAME) -> str:
    """An untrusted string, safe inside a markdown table cell or a bullet."""
    if value is None or value == "":
        return MISSING
    text = " ".join(str(value).split()).replace("|", "¦").replace("`", "'")
    if not text:
        return MISSING
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _cell(value: Any) -> str:
    number = _int(value)
    return MISSING if number is None else str(number)


def _signed(value: Any) -> str:
    number = _int(value)
    return MISSING if number is None else f"{number:+d}"


def when(value: Any) -> str:
    """An ISO instant as ``2026-10-14 10:00`` (UTC)."""
    if not value:
        return MISSING
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return name(value, 20)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.strftime("%Y-%m-%d %H:%M")


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _dicts(value: Any) -> list[dict]:
    return [item for item in value or [] if isinstance(item, dict)] if isinstance(value, list) else []


# ── get_ai_history ──────────────────────────────────────────────────────────


def _notes(version: dict) -> str:
    notes = []
    if version.get("fallback_used"):
        notes.append("fallback")
    elif version.get("deterministic"):
        notes.append("deterministic")
    if version.get("trigger") == "backfill":
        notes.append("backfilled")
    if version.get("is_latest"):
        notes.append("latest")
    ref = _dict(version.get("decision_report_ref"))
    if _int(ref.get("report_version")) is not None:
        notes.append(f"decision report v{ref['report_version']}")
    return ", ".join(notes) or MISSING


def history_markdown(body: dict) -> str:
    versions = _dicts(body.get("versions"))
    kind = KIND_LABELS.get(str(body.get("kind")), name(body.get("kind")))
    total = _int(body.get("total"))
    total = len(versions) if total is None else total
    lines = [f"## AI history — run `{str(body.get('run_id') or '')[:8]}` · {kind}", ""]
    if not versions:
        lines.append("No AI versions are stored for this run yet.")
        if _dict(body.get("live")).get("available"):
            lines.append(
                "The run has a current AI analysis; it is stored as a version when the pipeline next completes "
                "(compare with it using `to_version=\"live\"` once a version exists)."
            )
        return "\n".join(lines)
    latest = _int(body.get("latest_version"))
    lines.append(f"{total} stored version(s)" + (f", latest v{latest}" if latest is not None else "") + "; newest first.")
    lines += [
        "",
        "| Version | Generated (UTC) | Engine | Model | Verdict | Risk | Failing tests | Notes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for version in versions:
        lines.append(
            f"| v{_cell(version.get('version'))} | {when(version.get('generated_at') or version.get('created_at'))} "
            f"| {name(version.get('analysis_mode'))} | {name(version.get('llm_model'))} "
            f"| {name(version.get('verdict'))} | {_cell(version.get('risk_score'))} "
            f"| {_cell(version.get('failing_tests'))} | {_notes(version)} |"
        )
        if version.get("headline"):
            lines.append(f"| | | | | _{name(version['headline'], 120)}_ | | | |")
    if body.get("truncated") or total > len(versions):
        lines.append(f"_{len(versions)} of {total} versions shown (raise `limit`, up to 100)._")
    lines += [
        "",
        f"_{MISSING} = not known (never 0). The engine (rules / ML / LLM) is how the analysis was made: two versions "
        "made by different engines can differ because of the engine, not the product._",
    ]
    return "\n".join(lines)


# ── the diff (diff_ai_versions, and the details of compare_ai_with_previous) ──


def _pair(block: Any) -> str | None:
    if not isinstance(block, dict):
        return None

    def side(value: Any) -> str:
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, dict):
            return ", ".join(f"{name(k)} {v}" for k, v in sorted(value.items())) or MISSING
        return name(value)

    return f"{side(block.get('from'))} {ARROW} {side(block.get('to'))}"


def ref_label(ref: Any) -> str:
    ref = _dict(ref)
    if ref.get("source") == "live" or ref.get("ref") == "live":
        return "current data"
    version = _int(ref.get("version"))
    return f"v{version}" if version is not None else MISSING


def _provenance_lines(provenance: dict) -> list[str]:
    if not provenance.get("changed"):
        return ["### Provenance", "Same engine and model on both sides."]
    lines = ["### Provenance (read first)"]
    for key, label in (("engine", "engine"), ("model", "model"), ("fallback_used", "fallback"),
                       ("deterministic", "deterministic"), ("mode_counts", "tests per engine")):
        pair = _pair(provenance.get(key))
        if pair:
            lines.append(f"- {label}: {pair}")
    if provenance.get("note"):
        lines.append(f"- **{name(provenance['note'], 240)}**")
    if provenance.get("engine_only"):
        lines.append("- Only the analysis engine changed: nothing else material differs.")
    return lines


def _verdict_line(verdict: dict) -> str:
    frm, to = verdict.get("from"), verdict.get("to")
    if frm is None and to is None:
        text = "No verdict on either side."
    elif frm == to:
        text = f"{name(to)} (unchanged)"
    else:
        direction = verdict.get("direction")
        text = f"{name(frm)} {ARROW} {name(to)}" + (f" ({direction})" if direction else "")
    if verdict.get("from_risk") is not None or verdict.get("to_risk") is not None:
        text += f"; risk {_cell(verdict.get('from_risk'))} {ARROW} {_cell(verdict.get('to_risk'))}"
        if _int(verdict.get("risk_delta")) is not None:
            text += f" ({_signed(verdict.get('risk_delta'))})"
    return text


def _test_change(item: dict) -> str:
    parts = []
    category = _dict(item.get("category"))
    if category:
        parts.append(f"category {name(category.get('from'))} {ARROW} {name(category.get('to'))}")
    confidence = _dict(item.get("confidence"))
    if confidence:
        parts.append(f"confidence {_cell(confidence.get('from'))} {ARROW} {_cell(confidence.get('to'))}")
    flaky = _dict(item.get("flaky"))
    if flaky:
        parts.append(f"flaky {_pair(flaky)}")
    if not parts and item.get("root_cause_changed"):
        parts.append("root cause reworded (not material)")
    return "; ".join(parts) or MISSING


def diff_sections(diff: Any) -> list[str]:
    """The sections of an ``AIDigestDiff``, provenance first."""
    diff = _dict(diff)
    if not diff:
        return ["No diff available."]
    if diff.get("identical"):
        return ["**Identical:** both sides have the same AI content."]
    if diff.get("has_changes") is False:
        return ["**No differences.**"]
    cross = diff.get("mode") == "cross_run"
    lines = _provenance_lines(_dict(diff.get("provenance")))
    lines += ["", "### Verdict", _verdict_line(_dict(diff.get("verdict")))]

    changed = _dicts(_dict(diff.get("categories")).get("changed"))
    if changed:
        lines += ["", "### Categories", "| Category | From | To | Change |", "|---|---|---|---|"]
        for item in changed[:MAX_ROWS]:
            lines.append(f"| {name(item.get('category'))} | {_cell(item.get('from'))} | {_cell(item.get('to'))} "
                         f"| {_signed(item.get('delta'))} |")

    clusters = _dict(diff.get("clusters"))
    added, removed = _dicts(clusters.get("added")), _dicts(clusters.get("removed"))
    matched = _dicts(clusters.get("matched"))
    resized = [item for item in matched if item.get("resized")]
    if added or removed or resized:
        lines += ["", "### Root causes" if cross else "### Clusters"]
        for title, items in (("New root cause" if cross else "Added", added),
                             ("Resolved" if cross else "Removed", removed)):
            for item in items[:MAX_ROWS]:
                lines.append(f"- {title}: {name(item.get('label') or item.get('signature'))} "
                             f"({_cell(item.get('size'))} tests)")
        for item in resized[:MAX_ROWS]:
            lines.append(
                f"- {'Persisting, resized' if cross else 'Resized'}: "
                f"{name(item.get('label_to') or item.get('label_from') or item.get('signature_to'))} "
                f"({_cell(item.get('size_from'))} {ARROW} {_cell(item.get('size_to'))} tests"
                + (f", matched by member overlap {item.get('jaccard')}" if item.get("matched_by") == "overlap" else "")
                + ")"
            )
        if cross and matched:
            lines.append(f"- {len(matched)} root cause(s) persist ({len(resized)} resized).")

    tests = _dict(diff.get("tests"))
    counts = _dict(tests.get("counts"))
    t_added, t_removed, t_changed = _dicts(tests.get("added")), _dicts(tests.get("removed")), _dicts(tests.get("changed"))
    if t_added or t_removed or t_changed:
        lines += ["", "### Tests", (
            f"{_cell(counts.get('added', len(t_added)))} {'new failing' if cross else 'added'} · "
            f"{_cell(counts.get('removed', len(t_removed)))} {'resolved' if cross else 'removed'} · "
            f"{_cell(counts.get('changed', len(t_changed)))} changed "
            f"({_cell(counts.get('material_changed'))} material) · "
            f"{_cell(tests.get('persisting_unchanged'))} unchanged"
        )]
        rows = [(item, _test_change(item)) for item in t_changed[:MAX_ROWS]]
        rows += [(item, f"{'new failing' if cross else 'added'}: {name(item.get('category'))} "
                        f"{_cell(item.get('confidence'))}") for item in t_added[:MAX_ROWS]]
        rows += [(item, "resolved" if cross else "removed") for item in t_removed[:MAX_ROWS]]
        lines += ["", "| Test | Suite | Change |", "|---|---|---|"]
        for item, change in rows:
            lines.append(f"| {name(item.get('test_name') or item.get('test_fingerprint'))} "
                         f"| {name(item.get('suite_name'))} | {change} |")
        if tests.get("truncated") or len(t_changed) > MAX_ROWS:
            lines.append(f"_At most {MAX_ROWS} rows per list here; the counts are complete._")

    actions = _dict(diff.get("actions"))
    if actions.get("added") or actions.get("removed"):
        lines += ["", "### Actions"]
        lines += [f"- Added: {name(a, 160)}" for a in (actions.get("added") or [])[:MAX_ROWS]]
        lines += [f"- Removed: {name(a, 160)}" for a in (actions.get("removed") or [])[:MAX_ROWS]]
    return lines


def diff_markdown(body: dict) -> str:
    frm, to = _dict(body.get("from")), _dict(body.get("to"))
    build = to.get("build_number")
    run = f"run #{name(build)}" if build else f"run `{str(body.get('run_id') or '')[:8]}`"
    engines = [
        " ".join(name(ref.get(key)) for key in ("analysis_mode", "llm_model") if ref.get(key)) or MISSING
        for ref in (frm, to)
    ]
    lines = [
        f"## AI diff — {run} · {ref_label(frm)} {ARROW} {ref_label(to)}",
        f"**Engines:** {engines[0]} {ARROW} {engines[1]}",
        "",
    ]
    return "\n".join(lines + diff_sections(body.get("diff")))


# ── compare_ai_with_previous ────────────────────────────────────────────────


def baseline_label(baseline: dict) -> str:
    seq = _int(baseline.get("run_seq"))
    if seq is not None:
        return f"#{seq}"
    if baseline.get("build_number"):
        return f"build {name(baseline['build_number'])}"
    return f"`{str(baseline.get('run_id') or '')[:8]}`"


def compare_headline(body: dict) -> str:
    """``vs #1233 (checkout · main): verdict GO → NO_GO · new root causes 1 ·
    resolved 1 · 3 tests changed category`` (the CLI's first line)."""
    baseline = body.get("baseline") if isinstance(body.get("baseline"), dict) else None
    message = name(body.get("message") or body.get("reason") or "no baseline", 240)
    if baseline is None:
        return f"No previous run: {message}"
    head = (f"vs {baseline_label(baseline)} ({name(baseline.get('primary_suite_name') or 'no suite')} · "
            f"{name(baseline.get('branch') or 'no branch')})")
    diff = body.get("diff") if isinstance(body.get("diff"), dict) else None
    if diff is None:
        return f"{head}: not compared: {message}"
    verdict = _dict(diff.get("verdict"))
    frm, to = verdict.get("from"), verdict.get("to")
    if frm is None and to is None:
        verdict_part = "verdict unknown"
    elif frm == to:
        verdict_part = f"verdict {name(to)} (unchanged)"
    else:
        verdict_part = f"verdict {name(frm)} {ARROW} {name(to)}"
    counts = _dict(_dict(diff.get("clusters")).get("counts"))
    tests = _dict(diff.get("tests"))
    category_changes = sum(1 for item in _dicts(tests.get("changed")) if "category" in (item.get("kinds") or []))
    more = "+" if tests.get("truncated") else ""
    return f"{head}: " + " · ".join((
        verdict_part,
        f"new root causes {_cell(counts.get('added'))}",
        f"resolved {_cell(counts.get('removed'))}",
        f"{category_changes}{more} {'test' if category_changes == 1 and not more else 'tests'} changed category",
    ))


def narrative_label(narrative: dict) -> str:
    """``deterministic summary`` whenever no LLM wrote it -- never passed off as AI."""
    source = str(narrative.get("source") or "")
    return NARRATIVE_LABELS.get(source) or name(narrative.get("label") or "summary")


def verdict_regressed(body: Any) -> bool | None:
    """``True`` when the AI verdict got worse than the baseline's, ``False`` when
    it did not, ``None`` when it cannot be judged (no baseline, a side not
    analysed, a verdict missing on a side)."""
    diff = _dict(_dict(body).get("diff"))
    direction = _dict(diff.get("verdict")).get("direction")
    if direction == "worse":
        return True
    if direction in ("better", "same"):
        return False
    return None


def compare_markdown(body: dict) -> str:
    lines = ["## AI analysis vs the previous run", "", f"**{compare_headline(body)}**"]
    baseline = body.get("baseline") if isinstance(body.get("baseline"), dict) else None
    if baseline is not None:
        if baseline.get("selection_reason"):
            lines.append(f"Baseline: {name(baseline['selection_reason'], 240)}")
        if baseline.get("branch_mismatch"):
            lines.append("_The chosen baseline is on another branch._")
    narrative = body.get("narrative") if isinstance(body.get("narrative"), dict) else None
    if narrative is not None:
        lines += ["", f"### {narrative_label(narrative)}"]
        if narrative.get("executive_summary"):
            lines.append(name(narrative["executive_summary"], 600))
        lines += [f"- {name(change, 240)}" for change in narrative.get("ai_changes") or []]
    if isinstance(body.get("diff"), dict):
        lines += [""] + diff_sections(body["diff"])
    elif baseline is not None:
        lines += ["", "_Not compared: this is not the same as \"no change\"._"]
    return "\n".join(lines)
