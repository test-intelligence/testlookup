#!/usr/bin/env python3
"""TestLookup quality gates — single entry point.

Each guard codifies one cross-cutting invariant from CLAUDE.md so future
changes cannot silently degrade code quality. Run locally with::

    make quality-gate

or directly::

    python scripts/quality_gate.py             # run every guard
    python scripts/quality_gate.py --list      # show guard names
    python scripts/quality_gate.py --only backend.no-print frontend.clipboard-util

Guards are grouped by surface (``backend``, ``frontend``, ``database``,
``agents``). Each guard:

* Walks the relevant files.
* Reports violations as ``file:line: message``.
* Subtracts the baseline at ``scripts/quality-gate-baselines/<name>.txt``
  (one **content fingerprint** per line, ``#`` comments allowed) so
  existing, intentional exceptions stay tolerated. **New** violations
  fail the guard (exit code 1). This is the "ratchet" the team agreed
  on — gates only get stricter from here.

Baseline entries are keyed by *what* is tolerated, not by *where* it sits:
``sha256(relpath | enclosing function | normalized source line | nth-dup)``.
Line numbers are not part of the key, so inserting lines above a tolerated
violation is a no-op. Editing that line, or moving/renaming the function
around it, does re-report it — the exemption was granted for specific code.
See the "Baseline fingerprints" block below for the full rationale.

To accept a new baseline entry (rare — usually you fix the code instead):

    python scripts/quality_gate.py --only <guard> --update-baseline

Hand-editing a baseline file is not a workflow: the keys are hashes, so the
only way to add one is to regenerate it and explain the entry in review.

The CI workflow runs this script as a required job. ``--update-baseline``
is *not* run in CI, so a baseline change must be reviewed in a PR.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_DIR = REPO_ROOT / "scripts" / "quality-gate-baselines"

# Colour helpers — disabled when the terminal isn't a TTY (CI logs).
_USE_COLOR = sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False
RED = "\033[31m" if _USE_COLOR else ""
GREEN = "\033[32m" if _USE_COLOR else ""
YELLOW = "\033[33m" if _USE_COLOR else ""
BLUE = "\033[34m" if _USE_COLOR else ""
DIM = "\033[2m" if _USE_COLOR else ""
RESET = "\033[0m" if _USE_COLOR else ""


# ── Baseline fingerprints ────────────────────────────────────────────────────
#
# A baseline entry used to be ``<relpath>:<lineno>``. That made every guard
# hostage to line drift: inserting a line ANYWHERE above a tolerated match
# re-reported it as a brand-new violation. On 2026-08-21 that fired twice in a
# single session against the same untouched ``run_triage_agent(`` call in
# ``analysis_agent.py`` (631 -> 642 -> 684), each time because unrelated code
# was added above it. The cost is not the two-second edit; it is that bumping a
# baseline number becomes reflex, and a reflex is exactly how a genuinely new
# violation gets waved through.
#
# So an entry now fingerprints WHAT is tolerated, not WHERE it sits:
#
#     sha256(relpath | enclosing function | normalized matched line | nth-dup)
#
# * Line numbers are absent from the key, so code moving up or down is a no-op.
# * The matched line's own text IS in the key, so editing the tolerated line
#   re-reports it. Deliberate: the exemption was granted for specific code.
# * The enclosing function (Python only, via AST) is in the key, so moving the
#   call into another function — or renaming that function — re-reports it too.
#   Same reasoning: the reviewer approved a call in a named place.
# * ``nth-dup`` separates two byte-identical matches inside one function, so
#   baselining one does not silently tolerate its twin.
#
# File-level violations (``line=0``, e.g. "this module never calls X") key on
# the path alone, which was already drift-proof. Files with no AST we can parse
# (``.ts``, ``.tsx``, ``.yaml``, unparseable Python) contribute an empty scope
# component; path plus line text still keys them stably.
#
# Known, accepted blind spots: two identical lines in one function that swap
# places keep each other's keys (they are interchangeable by definition), and a
# function moved verbatim to another module re-reports, because the path
# changed — which is worth a second look anyway.

_WHITESPACE_RE = re.compile(r"\s+")
_FILE_LEVEL_TEXT = "<file>"

# The AST parse is the only expensive step, and its cache is keyed on a digest
# of the source it parsed — NOT on (path, mtime, size). A size-preserving
# rewrite inside one mtime tick collides under that stamp, and this cache
# feeds baseline keys: a stale hit would silently fingerprint the wrong line.
# Content addressing cannot go stale by construction. Lines themselves are
# always read fresh; it is a read plus splitlines.
_SCOPE_CACHE: dict[tuple[str, str], list[tuple[int, int, str]]] = {}


def _source_lines(path: Path) -> list[str]:
    """Lines of ``path``, or ``[]`` when it cannot be read. Guards do report
    violations against files that do not exist ("expected owner missing"), so
    this must never raise."""
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        return []


def _python_scopes(path: Path) -> list[tuple[int, int, str]]:
    """``(start_line, end_line, dotted_qualname)`` for every class/def in a
    Python file. Best-effort: a file that will not parse yields no scopes
    rather than blowing up the gate (ruff and pytest own syntax errors)."""
    if path.suffix != ".py":
        return []
    source = "\n".join(_source_lines(path))
    stamp = (path.as_posix(), hashlib.sha256(source.encode("utf-8")).hexdigest())
    cached = _SCOPE_CACHE.get(stamp)
    if cached is not None:
        return cached

    spans: list[tuple[int, int, str]] = []
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        _SCOPE_CACHE[stamp] = spans
        return spans

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                # Decorators sit above the ``def``, and a match on one belongs
                # to the function it decorates.
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                end = getattr(child, "end_lineno", None) or child.lineno
                spans.append((start, end, qual))
                walk(child, qual)
            else:
                walk(child, prefix)

    walk(tree, "")
    _SCOPE_CACHE[stamp] = spans
    return spans


def _enclosing_scope(path: Path, line: int) -> str:
    """Dotted name of the innermost class/def containing ``line``, else ""."""
    if line <= 0:
        return ""
    best = ""
    best_span: Optional[int] = None
    for start, end, qual in _python_scopes(path):
        if start <= line <= end:
            span = end - start
            if best_span is None or span < best_span:
                best_span, best = span, qual
    return best


def _normalize_line(text: str) -> str:
    """Collapse runs of whitespace so re-indenting a tolerated line (a wrapping
    ``if``, a reformat) does not invalidate its baseline entry."""
    return _WHITESPACE_RE.sub(" ", text.strip())


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class Violation:
    """One occurrence of a forbidden pattern.

    ``key`` is what we match against the baseline file: a content fingerprint
    that survives line drift (see "Baseline fingerprints" above).
    ``occurrence`` is filled in by :func:`assign_occurrences` for the rare case
    of two identical matches in one scope; leave it 0 when hand-constructing."""

    file: Path
    line: int
    message: str
    occurrence: int = 0

    @property
    def relpath(self) -> str:
        return self.file.relative_to(REPO_ROOT).as_posix()

    @property
    def source_line(self) -> str:
        """The matched line, normalized. ``<file>`` for file-level violations."""
        if self.line <= 0:
            return _FILE_LEVEL_TEXT
        lines = _source_lines(self.file)
        if 1 <= self.line <= len(lines):
            return _normalize_line(lines[self.line - 1])
        return ""

    @property
    def scope(self) -> str:
        return _enclosing_scope(self.file, self.line)

    def identity_parts(self) -> tuple[str, str, str]:
        """Everything but the duplicate counter — the grouping key used to
        assign ``occurrence``."""
        return (self.relpath, self.scope, self.source_line)

    @property
    def key(self) -> str:
        rel, scope, text = self.identity_parts()
        raw = chr(0).join([rel, scope, text, str(self.occurrence)])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @property
    def annotation(self) -> str:
        """Human-readable pointer written beside the key in the baseline file.
        Regenerated on every ``--update-baseline`` and never matched against —
        it exists so a reviewer can tell what a hash is tolerating."""
        rel, scope, text = self.identity_parts()
        bits = [f"{rel}:{self.line}" if self.line > 0 else rel]
        if scope:
            bits.append(f"in {scope}")
        if text and text != _FILE_LEVEL_TEXT:
            bits.append(_truncate(text, 80))
        if self.occurrence:
            bits.append(f"(match #{self.occurrence + 1})")
        return "  ".join(bits)

    def format(self) -> str:
        return f"{self.relpath}:{self.line}: {self.message}"


def assign_occurrences(violations: list[Violation]) -> list[Violation]:
    """Number violations that fingerprint identically (same file, same
    enclosing function, byte-identical line) so each keeps its own baseline
    entry. Guards emit in file order, so the numbering is stable unless the
    identical siblings are themselves reordered."""
    counts: dict[tuple[str, str, str], int] = {}
    for v in violations:
        parts = v.identity_parts()
        v.occurrence = counts.get(parts, 0)
        counts[parts] = v.occurrence + 1
    return violations


# A fingerprint: 16 lowercase hex chars. Anything else in a baseline file is a
# leftover from the old <relpath>:<lineno> scheme.
_BASELINE_KEY_RE = re.compile(r"^[0-9a-f]{16}$")

_BASELINE_NOTES_MARKER = (
    "# ---- notes below this line are preserved across --update-baseline ----"
)

_BASELINE_HEADER = (
    "# Auto-generated by scripts/quality_gate.py --update-baseline. Do not hand-edit.\n"
    "#\n"
    "# Each entry is a content fingerprint of one tolerated violation:\n"
    "#   sha256(relpath | enclosing function | normalized matched line | nth-dup)\n"
    "# Line numbers are NOT part of the key, so inserting lines above a tolerated\n"
    "# violation does not re-report it. Editing that line, or moving/renaming the\n"
    "# function around it, DOES — on purpose: the exemption was granted for\n"
    "# specific code, so changed code deserves a fresh look.\n"
    "#\n"
    "# The text after `#` on an entry line is a pointer for humans. It is\n"
    "# regenerated on every run and is never matched against.\n"
    "#\n"
    "# Reviewed in PR; remove entries as the code is cleaned up.\n"
)

# Header lines written by the pre-fingerprint format. Dropped on rewrite so the
# migration does not stack two headers; every other comment is a human note and
# survives.
_LEGACY_HEADER_LINES = {
    "# Auto-generated by scripts/quality_gate.py --update-baseline.",
    "# Each line is a tolerated <relpath>:<lineno> match for this guard.",
    "# Reviewed in PR; remove entries as the code is cleaned up.",
}


@dataclass
class Guard:
    name: str
    description: str
    check: Callable[[], list[Violation]]
    fix_hint: str = ""

    @property
    def baseline_path(self) -> Path:
        return BASELINE_DIR / f"{self.name.replace('.', '__')}.txt"

    def load_baseline(self) -> dict[str, str]:
        """Map every baseline entry to its human-readable annotation.

        A dict rather than a set so the stale-entry warning can say *what* a
        hash was tolerating. Membership tests (``key in baseline``) read the
        same as they did against the old set."""
        if not self.baseline_path.exists():
            return {}
        entries: dict[str, str] = {}
        for raw in self.baseline_path.read_text(encoding="utf-8").splitlines():
            entry, _, note = raw.partition("#")
            entry = entry.strip()
            if entry:
                entries[entry] = note.strip()
        return entries

    def load_notes(self) -> list[str]:
        """Hand-written commentary in the baseline file, which explains *why*
        entries are tolerated and must outlive a regeneration.

        Two baselines carry paragraphs of it — ``repo.no-gitignored-source``
        documents GIT-001, ``backend.structlog-positional-args`` documents why
        it is empty on purpose — and losing that to an ``--update-baseline``
        would be a real loss."""
        if not self.baseline_path.exists():
            return []
        lines = self.baseline_path.read_text(encoding="utf-8").splitlines()
        if _BASELINE_NOTES_MARKER in lines:
            lines = lines[lines.index(_BASELINE_NOTES_MARKER) + 1:]
            return [ln for ln in lines if ln.lstrip().startswith("#")]
        # Pre-fingerprint file: every comment that is not old boilerplate.
        return [
            ln for ln in lines
            if ln.lstrip().startswith("#") and ln.strip() not in _LEGACY_HEADER_LINES
        ]

    def save_baseline(self, entries: Iterable["Violation | str"]) -> None:
        """Rewrite the baseline file. Accepts Violations (which carry their own
        annotation) or bare key strings. Entries sort by the location they came
        from, not by hash, so the file stays readable and diffs stay small."""
        rows: dict[str, tuple[tuple[str, int, str], str]] = {}
        for entry in entries:
            if isinstance(entry, Violation):
                rows.setdefault(
                    entry.key,
                    ((entry.relpath, entry.line, entry.key), entry.annotation),
                )
            else:
                key = str(entry)
                rows.setdefault(key, (("", 0, key), ""))

        if not rows and not self.baseline_path.exists():
            # Guards that ship at zero with no baseline file are absolute
            # rules, not ratchets. A repo-wide --update-baseline must not
            # hand them an empty file and turn them into ratchets.
            return

        notes = self.load_notes()
        BASELINE_DIR.mkdir(parents=True, exist_ok=True)
        out = [_BASELINE_HEADER.rstrip("\n"), _BASELINE_NOTES_MARKER]
        out.extend(notes)
        for key, (_sort, annotation) in sorted(rows.items(), key=lambda kv: kv[1][0]):
            out.append(f"{key}  # {annotation}" if annotation else key)
        # newline="" suppresses the platform translation write_text would
        # do: the file must come out byte-identical whether it was
        # regenerated on a developer's Windows box or by CI on Linux
        # (.gitattributes pins these to eol=lf).
        with self.baseline_path.open("w", encoding="utf-8", newline="") as fh:
            fh.write(chr(10).join(out) + chr(10))


# ── File walking helpers ─────────────────────────────────────────────────────

_EXCLUDE_DIR_NAMES = {
    "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "htmlcov",
    "playwright-report", "test-results", "target", ".git",
}


def iter_files(root: Path, suffixes: tuple[str, ...]) -> Iterable[Path]:
    """Yield files under ``root`` matching any of ``suffixes``,
    skipping vendored / generated directories."""
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if any(part in _EXCLUDE_DIR_NAMES for part in path.parts):
            continue
        if path.suffix in suffixes:
            yield path


def grep_lines(path: Path, pattern: re.Pattern[str]) -> list[tuple[int, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    out: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            out.append((i, line))
    return out


def python_string_lines(path: Path) -> set[int]:
    """Return the set of 1-indexed line numbers that fall entirely
    inside a string literal (including docstrings + triple-quoted
    blocks). Used to skip pattern matches that occur in prose."""
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return set()
    in_string: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.STRING:
                (start_line, _), (end_line, _) = tok.start, tok.end
                for ln in range(start_line, end_line + 1):
                    in_string.add(ln)
    except (tokenize.TokenizeError, IndentationError, SyntaxError):
        # Tokenizer is best-effort — broken files surface in pytest/ruff,
        # not here. Falling back to "no string lines" means we may
        # double-count one or two false positives in pathological files.
        return set()
    return in_string


# ── Backend guards ───────────────────────────────────────────────────────────

_PRINT_RE = re.compile(r"^\s*print\s*\(")


def _backend_no_print() -> list[Violation]:
    """``print()`` in app code leaks to stdout instead of structlog;
    structlog is the only allowed log path so trace correlation +
    PII redaction stay intact."""
    root = REPO_ROOT / "backend" / "app"
    violations: list[Violation] = []
    for path in iter_files(root, (".py",)):
        for ln, _ in grep_lines(path, _PRINT_RE):
            violations.append(Violation(
                path, ln,
                "print() in backend/app/ — use structlog (logger.info / .warning / .error)",
            ))
    return violations


_DIRECT_TRIAGE_RE = re.compile(
    r"\brun_triage_agent\s*\(|"
    r"\bRulesEngine\s*\(\s*\)\s*\.classify_test\s*\(|"
    r"\bMLClassifier\s*\(\s*\)\s*\.classify\s*\("
)
# These two files own the dispatch and are allowed to call engines directly.
_ANALYSIS_ROUTER_ALLOWLIST = {
    "backend/app/services/analysis_router.py",
    "backend/app/services/agent.py",   # defines run_triage_agent itself
}


def _backend_analysis_router_bypass() -> list[Violation]:
    """``analysis_router.classify_test()`` is the single dispatcher for
    the four analysis modes. Direct calls to ``run_triage_agent()`` or
    the engines bypass mode resolution, fallback, and routing metadata,
    so reports lose decision traceability."""
    violations: list[Violation] = []
    for path in iter_files(REPO_ROOT / "backend" / "app", (".py",)):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in _ANALYSIS_ROUTER_ALLOWLIST:
            continue
        string_lines = python_string_lines(path)
        for ln, line in grep_lines(path, _DIRECT_TRIAGE_RE):
            if ln in string_lines:
                continue
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            violations.append(Violation(
                path, ln,
                "direct analysis-engine call — route through "
                "services.analysis_router.classify_test()",
            ))
    return violations


_FINALIZE_PATTERN = re.compile(r"\bfinalize_run\s*\(")
# Files that legitimately commit TestCase rows AND must call finalize_run
# afterwards. Adding a new such file requires adding it here (forcing review).
_INGESTION_OWNERS = (
    "backend/app/services/ingestion_pipeline.py",
    "backend/app/worker/tasks.py",
)


def _backend_finalize_run_wired() -> list[Violation]:
    """Any code path that commits TestCase rows must call
    ``ingestion_pipeline.finalize_run`` to drive suite membership +
    auto-tag + AI-pipeline. Otherwise ``/suites`` ends up empty while
    ``/runs`` is populated (the bug we hit on 2026-05-16)."""
    violations: list[Violation] = []
    for rel in _INGESTION_OWNERS:
        path = REPO_ROOT / rel
        if not path.exists():
            violations.append(Violation(
                path, 0,
                f"expected ingestion owner missing: {rel}",
            ))
            continue
        text = path.read_text(encoding="utf-8")
        if not _FINALIZE_PATTERN.search(text):
            violations.append(Violation(
                path, 0,
                "ingestion owner does not call finalize_run() — "
                "/suites will be empty for runs persisted here",
            ))
    return violations


_PII_RAW_LOG_RE = re.compile(
    # Looks for logger.* calls whose first kwarg references a likely
    # PII column without going through privacy_service. Coarse — we
    # baseline existing matches and refuse new ones.
    r"logger\.\w+\([^)]*\b(email|password|user_email|api_key|raw_key)\b\s*=",
)


def _backend_pii_log_redaction() -> list[Violation]:
    """Structlog calls that pass an obvious PII column verbatim. The
    rule is: route through ``privacy_service.sanitize_for_logging``
    so PII never reaches logs unredacted."""
    violations: list[Violation] = []
    for path in iter_files(REPO_ROOT / "backend" / "app", (".py",)):
        if "privacy_service.py" in path.as_posix():
            continue
        for ln, _ in grep_lines(path, _PII_RAW_LOG_RE):
            violations.append(Violation(
                path, ln,
                "logger call references PII verbatim — sanitize via "
                "services.privacy_service.sanitize_for_logging()",
            ))
    return violations


# Modules using structlog (``logger = structlog.get_logger(...)``).
# structlog's BoundLogger.warning/info/error has signature
# ``(event, **kwargs)`` — it does NOT accept stdlib-style positional
# args after the event string. Calls like
# ``logger.warning("foo: %s", exc)`` blow up with ``TypeError:
# BoundLoggerBase._proxy_to_logger() takes from 2 to 3 positional
# arguments but 4 were given`` the moment they fire — and when they
# fire from inside an ``except`` block on a request path, the
# TypeError escapes the handler and 500s the endpoint.
# Reference incident: 2026-05-18 ``/runs/compare/latest`` was 500-ing
# every call because ``run_compare_agent.py:82`` had this pattern and
# Ollama was unreachable (so the except handler fired). See
# ``memory/feedback_structlog_positional_args.md``.
# Cheap pre-filter: does this module bind a structlog logger under ANY name?
# Previously this required the name to be literally ``logger``, which skipped
# every module binding e.g. ``_slog = structlog.get_logger(...)`` — including
# worker/tasks.py, so those call sites were never checked at all. The AST pass
# below resolves the actual binding names; this is only here to skip files
# cheaply.
_STRUCTLOG_LOGGER_RE = re.compile(
    r"structlog(?:\.stdlib)?\.get_logger",
)
# Format specifiers that mean "this string expects positional interpolation".
_STRUCTLOG_FORMAT_SPEC_RE = re.compile(r"%[-#0-9.]*[sdrfi!]")

_STRUCTLOG_POSITIONAL_RE = re.compile(
    # ``logger.<level>("…%X…", arg)`` — format-string + positional arg.
    # Allows ``%s``, ``%d``, ``%r``, ``%f``, ``%!s``.
    r'logger\.(warning|info|error|debug|exception|critical)\(\s*'
    r'["\'][^"\']*%[sdrf!][^"\']*["\']\s*,\s*[^)=]',
)


def _backend_structlog_positional_args() -> list[Violation]:
    """structlog BoundLogger doesn't accept stdlib-style positional args.

    ``BoundLogger.<level>`` is ``(event, **kw)``, so a stdlib-style
    ``logger.info("x %s", val)`` raises ``TypeError`` at call time. Inside a
    ``try``/``except`` that silently disables the feature around it — this is
    exactly how checkpoint restore died (PR #573): the log call raised, the
    blanket except swallowed it, and every restore returned None.

    **AST-based, not line-based.** The previous regex only matched calls
    written on a single line, so a call split across lines was invisible to it.
    That blind spot hid the checkpoint bug. Walking the AST catches a call
    however it is formatted, and also catches the
    ``logger.error("x: %s", exc, exc_info=True)`` shape, where positional args
    sit alongside a legitimate keyword.

    **Binding-aware, not file-aware.** A module may bind BOTH loggers —
    ``worker/tasks.py`` has ``logger = logging.getLogger(__name__)`` next to
    ``_slog = structlog.get_logger(...)``. Treating every ``logger.*`` call in
    any file that merely mentions structlog would flag ~140 stdlib calls that
    are perfectly correct, and "fixing" those to kwargs breaks them with the
    mirror-image ``TypeError`` (stdlib ``Logger._log()`` rejects arbitrary
    keywords). So resolve, per module, which NAMES are bound to
    ``structlog.get_logger`` and only judge calls on those.

    Fix at the call site: replace ``logger.warning("X failed: %s", exc)`` with
    ``logger.warning("x_failed", error=str(exc))`` — but only if that name is a
    structlog logger. On a stdlib logger, positional args are correct.
    """
    violations: list[Violation] = []
    levels = {"warning", "info", "error", "debug", "exception", "critical"}
    for path in iter_files(REPO_ROOT / "backend" / "app", (".py",)):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if not _STRUCTLOG_LOGGER_RE.search(text):
            # Module uses stdlib logging (or no logger at all) — the
            # positional-arg pattern is fine there. Skip.
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue

        # Names assigned from structlog.get_logger(...) anywhere in the module.
        structlog_names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            call = node.value
            func = call.func
            dotted = ""
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                dotted = f"{func.value.id}.{func.attr}"
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute):
                inner = func.value
                if isinstance(inner.value, ast.Name):
                    dotted = f"{inner.value.id}.{inner.attr}.{func.attr}"
            if dotted not in {"structlog.get_logger", "structlog.stdlib.get_logger"}:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    structlog_names.add(target.id)
        if not structlog_names:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in levels:
                continue
            # Only names actually bound to a structlog logger in this module.
            if not (isinstance(func.value, ast.Name) and func.value.id in structlog_names):
                continue
            # A single string argument is the correct kwargs-style call.
            if len(node.args) < 2:
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            if not _STRUCTLOG_FORMAT_SPEC_RE.search(first.value):
                continue
            violations.append(Violation(
                path, node.lineno,
                "structlog logger called with positional %s args — use "
                "kwargs (logger.warning(\"event_name\", error=str(exc)))",
            ))
    return violations


# ── Audit-table write discipline ─────────────────────────────────────────────
#
# The four models below document themselves as *append-only*. Nothing in the
# database enforces that: the entire migration set contains exactly one
# trigger (the search-vector trigger in ``0001``), no grants are restricted,
# and no store is WORM. **This guard is the enforcement** — it is what makes
# those docstrings true rather than aspirational, in the house style (a guard,
# not a promise).
#
# Rule: application code under ``backend/app/`` may INSERT audit rows and read
# them. It may never UPDATE one, and may never DELETE one outside the single
# allowlisted deleter below.
_AUDIT_MODELS = frozenset({
    "AccessAuditLog",      # access_audit_logs
    "IdentityEvent",       # identity_events
    "SettingsAuditLog",    # settings_audit_log
    "TestCaseAuditLog",    # test_case_audit_logs
})
_AUDIT_TABLES = (
    "access_audit_logs",
    "identity_events",
    "settings_audit_log",
    "test_case_audit_logs",
)

# ``services/retention_service.py`` is the ONE legitimate deleter. US-11.4
# gives every project an ``audit_days`` clock (floor 365 d, default 2555 ≈ 7 y,
# validated >= ``runs_days``) and the purge deletes ``access_audit_logs`` +
# ``test_case_audit_logs`` past it. That is deliberate, opt-in (policy
# ``enabled``), preview-able, and self-audited — every execute-mode purge
# writes its own ``settings_audit_log`` row. It is precisely why the models say
# "append-only", not "immutable". The allowlist covers DELETE only: even the
# retention service may not UPDATE an audit row.
_AUDIT_DELETE_ALLOWLIST = {
    "backend/app/services/retention_service.py",
}

_AUDIT_TABLE_ALT = "|".join(_AUDIT_TABLES)
_AUDIT_RAW_SQL_UPDATE_RE = re.compile(
    rf"\bupdate\s+(?:only\s+)?(?:public\.)?[\"`]?(?:{_AUDIT_TABLE_ALT})\b",
    re.IGNORECASE,
)
_AUDIT_RAW_SQL_DELETE_RE = re.compile(
    rf"\b(?:delete\s+from|truncate(?:\s+table)?)\s+(?:only\s+)?(?:public\.)?"
    rf"[\"`]?(?:{_AUDIT_TABLE_ALT})\b",
    re.IGNORECASE,
)



# Keywords ``logging.Logger.<level>`` actually accepts. Everything else lands in
# ``Logger._log()`` as an unexpected keyword and raises TypeError.
_STDLIB_LOG_KWARGS = {"exc_info", "stack_info", "stacklevel", "extra"}


def _stdlib_logger_names(tree: ast.Module) -> set[str]:
    """Module-level names bound to ``logging.getLogger(...)``.

    The mirror of the structlog resolution in
    ``_backend_structlog_positional_args``: a module may bind BOTH (
    ``worker/tasks.py`` has ``logger`` stdlib next to ``_slog`` structlog), so
    the judgement has to be per NAME, never per file.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        dotted = ""
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            dotted = f"{func.value.id}.{func.attr}"
        elif isinstance(func, ast.Name):
            dotted = func.id
        if dotted not in {"logging.getLogger", "getLogger"}:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _backend_stdlib_logger_kwargs() -> list[Violation]:
    """stdlib ``logging.Logger`` called with structlog-style keyword fields.

    The mirror image of ``backend.structlog-positional-args``, and the half
    that was left unguarded. ``Logger.error(msg, *args, exc_info=...,
    stack_info=..., stacklevel=..., extra=...)`` accepts no other keyword, so
    ``logger.error("x_failed", error_type=...)`` raises ``TypeError`` inside
    ``Logger._log`` — **at the moment of logging, not at import**.

    Why that is worse than it sounds: every instance found in the wild sat in
    an ``except`` block. The handler meant to record the real failure and
    re-raise a scrubbed ``RuntimeError(...) from None``; instead the log call
    threw, so the diagnostic was never written, the scrubbed re-raise never
    ran, and the original exception's full chained traceback escaped — which
    is precisely what ``from None`` was there to prevent. Four such handlers
    in ``worker/tasks.py`` were failing on the live homelab.

    A previous fix had already hit this trap, left a comment naming it, and
    still missed four sibling call sites in the same file. That is what this
    guard is for.
    """
    violations: list[Violation] = []
    levels = {
        "debug", "info", "warning", "warn",
        "error", "exception", "critical", "fatal", "log",
    }
    for path in iter_files(REPO_ROOT / "backend" / "app", (".py",)):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "getLogger" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue

        stdlib_names = _stdlib_logger_names(tree)
        if not stdlib_names:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr in levels):
                continue
            if not (isinstance(func.value, ast.Name) and func.value.id in stdlib_names):
                continue
            bad = sorted(
                k.arg for k in node.keywords
                if k.arg is not None and k.arg not in _STDLIB_LOG_KWARGS
            )
            if not bad:
                continue
            fields = ", ".join(f"{name}=" for name in bad)
            violations.append(Violation(
                path,
                node.lineno,
                f"{func.value.id}.{func.attr}(...) is a stdlib logger but is "
                f"passed {fields} — Logger._log() rejects it with TypeError "
                "when the line runs",
            ))
    return violations


_AT_MOST_ONCE_MARKER = "at-most-once:"


def _backend_dedup_lock_allows_retry() -> list[Violation]:
    """A dedup lock must not silence the task's own Celery retry.

    The trap: a task takes a Redis ``SET NX`` lock to suppress *concurrent*
    duplicates, then fails and calls ``self.retry()``. Celery keeps the task
    id stable across retries, but the lock outlives the failed attempt — so
    the retry meets its own lock, concludes it is a duplicate, logs "skipping
    duplicate" and returns **success** having done nothing. The retry policy
    is inert and the work is dropped silently, which is the worst available
    shape: every counter says the task succeeded.

    ``_is_duplicate(key, ttl, owner=...)`` exists precisely so the same task
    id can reacquire. This guard requires it wherever a dedup call shares a
    task with ``self.retry(``.

    Why a guard and not a test: this was fixed once, in ``run_agent_pipeline``
    (Phase J), and pinned by a source-text assertion naming that one function.
    Three sibling call sites in the same file kept the bug for months while
    that assertion stayed green — ``ingest_test_run`` silently dropped an
    entire uploaded run on any transient MinIO or DB failure. Guard the class,
    not the instance.

    Escape hatch: a call marked ``at-most-once:`` in a comment on or just
    above it is exempt. Suppressing a retry is correct when the work is an
    irreversible external side effect (an email already delivered), and that
    intent should be stated rather than inferred.
    """
    violations: list[Violation] = []
    for path in iter_files(REPO_ROOT / "backend" / "app", (".py",)):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "_is_duplicate" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        lines = text.splitlines()

        for scope in ast.walk(tree):
            if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = list(ast.walk(scope))
            retries = any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "retry"
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "self"
                for n in body
            )
            if not retries:
                continue
            for node in body:
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_is_duplicate"
                ):
                    continue
                if any(k.arg == "owner" for k in node.keywords):
                    continue
                window = lines[max(0, node.lineno - 6):node.lineno]
                if any(_AT_MOST_ONCE_MARKER in ln for ln in window):
                    continue
                violations.append(Violation(
                    path,
                    node.lineno,
                    f"_is_duplicate(...) without owner= inside {scope.name}(), "
                    "which calls self.retry() — the retry will meet this "
                    "lock, report success and do nothing",
                ))
    return violations


def _audit_call_name(func: ast.AST) -> str:
    """Terminal callable name — ``update`` for both ``update(X)`` and
    ``sa.update(X)`` / ``db.query(X).update(...)``."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _audit_referenced_names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _audit_scope_nodes(scope: ast.AST) -> list[ast.AST]:
    """Every node belonging to one lexical scope.

    Nested ``def`` / ``class`` / ``lambda`` bodies are excluded — they are
    analysed as scopes of their own, so a local called ``row`` in one function
    can never be confused with a ``row`` in another (the bug that made a
    module-wide pass flag ``retention_service.upsert_policy``).
    """
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        out.append(node)
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
        ):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return out


def _audit_scopes(tree: ast.AST) -> list[list[ast.AST]]:
    scopes = [_audit_scope_nodes(tree)]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            scopes.append(_audit_scope_nodes(node))
    return scopes


def _audit_row_names(nodes: list[ast.AST]) -> set[str]:
    """Names in one scope bound to a **fetched** audit row (or a container).

    A freshly constructed row (``row = SettingsAuditLog(...)``) is excluded on
    purpose: mutating its attributes before ``flush`` is still one INSERT, and
    that is the normal write path. Anything else that mentions an audit model
    (a ``select(...)`` result, a ``scalars().all()`` list, a loop variable over
    either) is treated as an already-persisted row, so attribute stores on it
    are UPDATEs.
    """
    names: set[str] = set()
    # Re-run until stable so ``rows = select(...)`` → ``for row in rows:``
    # propagates through intermediate bindings.
    for _ in range(4):
        before = len(names)
        for node in nodes:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if value is None:
                    continue
                referenced = _audit_referenced_names(value)
                if not (referenced & _AUDIT_MODELS or referenced & names):
                    continue
                if (
                    isinstance(value, ast.Call)
                    and _audit_call_name(value.func) in _AUDIT_MODELS
                ):
                    continue  # construction — an INSERT being staged
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                referenced = _audit_referenced_names(node.iter)
                if (referenced & _AUDIT_MODELS or referenced & names) and isinstance(
                    node.target, ast.Name
                ):
                    names.add(node.target.id)
        if len(names) == before:
            break
    return names


def _backend_audit_write_discipline(root: Optional[Path] = None) -> list[Violation]:
    """Audit tables are append-only — enforce it, don't just document it.

    ``settings_audit_log``, ``access_audit_logs``, ``test_case_audit_logs`` and
    ``identity_events`` have no triggers, no restricted grants and no WORM
    storage behind them. This guard fails CI when application code:

    * builds an UPDATE against an audit model (``update(AccessAuditLog)``,
      ``sa.update(...)``, ``db.query(...).update(...)``),
    * assigns to an attribute of a **fetched** audit row (or calls
      ``setattr`` / ``db.delete`` on one),
    * builds a DELETE against an audit model outside
      ``services/retention_service.py``,
    * embeds raw SQL that UPDATEs / DELETEs FROM / TRUNCATEs an audit table.

    Deliberate blind spots (written down so nobody trusts the guard past its
    edge): raw SQL whose table name is assembled at runtime (f-strings,
    concatenation); a mutation performed inside a generic helper that receives
    an audit row as a *parameter*, or inside a nested closure over an outer
    row — name binding within one scope is what we track; Alembic migrations,
    which are reviewed schema evolution and out of scope; and anything done
    outside the application (psql, a DBA, a restore, a backup rollback).
    """
    root = root or (REPO_ROOT / "backend" / "app")
    violations: list[Violation] = []
    for path in iter_files(root, (".py",)):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if not any(model in text for model in _AUDIT_MODELS) and not any(
            table in text for table in _AUDIT_TABLES
        ):
            continue
        try:
            rel = path.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            rel = path.as_posix()
        delete_allowed = rel in _AUDIT_DELETE_ALLOWLIST

        seen: set[tuple[int, str]] = set()

        def _flag(line: int, kind: str, message: str) -> None:
            if (line, kind) in seen:
                return
            seen.add((line, kind))
            violations.append(Violation(path, line, message))

        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            tree = None

        for scope_nodes in _audit_scopes(tree) if tree is not None else []:
            fetched = _audit_row_names(scope_nodes)
            for node in scope_nodes:
                # (a) Core / ORM bulk constructs against an audit model.
                if isinstance(node, ast.Call):
                    name = _audit_call_name(node.func)
                    arg_hits = (
                        _audit_referenced_names(node.args[0]) & _AUDIT_MODELS
                        if node.args
                        else set()
                    )
                    chain_hits: set[str] = set()
                    if isinstance(node.func, ast.Attribute):
                        chain_hits = (
                            _audit_referenced_names(node.func.value) & _AUDIT_MODELS
                        )
                    if name == "update" and (arg_hits or chain_hits):
                        _flag(
                            node.lineno, "update",
                            "UPDATE against an audit table "
                            f"({', '.join(sorted(arg_hits or chain_hits))}) — audit "
                            "rows are append-only; write a new row instead",
                        )
                    elif name == "delete" and (arg_hits or chain_hits):
                        if not delete_allowed:
                            _flag(
                                node.lineno, "delete",
                                "DELETE against an audit table "
                                f"({', '.join(sorted(arg_hits or chain_hits))}) — the "
                                "retention purge (services/retention_service.py) is "
                                "the only allowed deleter",
                            )
                    elif (
                        name == "setattr"
                        and node.args
                        and isinstance(node.args[0], ast.Name)
                        and node.args[0].id in fetched
                    ):
                        _flag(
                            node.lineno, "update",
                            f"setattr on fetched audit row {node.args[0].id!r} — "
                            "audit rows are append-only",
                        )
                    elif (
                        name == "delete"
                        and len(node.args) == 1
                        and isinstance(node.args[0], ast.Name)
                        and node.args[0].id in fetched
                        and not delete_allowed
                    ):
                        _flag(
                            node.lineno, "delete",
                            f"session delete of fetched audit row "
                            f"{node.args[0].id!r} — only the retention purge may "
                            "delete audit rows",
                        )
                # (b) Attribute store on a fetched audit row.
                targets: list[ast.AST] = []
                if isinstance(node, ast.Assign):
                    targets = list(node.targets)
                elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                    targets = [node.target]
                for target in targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id in fetched
                    ):
                        _flag(
                            node.lineno, "update",
                            f"attribute assignment on fetched audit row "
                            f"{target.value.id}.{target.attr} — audit rows are "
                            "append-only; write a new row instead",
                        )

        # (c) Raw SQL embedded as a literal.
        for ln, line in grep_lines(path, _AUDIT_RAW_SQL_UPDATE_RE):
            if line.strip().startswith("#"):
                continue
            _flag(ln, "update", "raw SQL UPDATE of an audit table — append-only")
        for ln, line in grep_lines(path, _AUDIT_RAW_SQL_DELETE_RE):
            if line.strip().startswith("#") or delete_allowed:
                continue
            _flag(
                ln, "delete",
                "raw SQL DELETE/TRUNCATE of an audit table — only the retention "
                "purge (services/retention_service.py) may delete audit rows",
            )
    return violations


# ── Frontend guards ──────────────────────────────────────────────────────────

_CLIPBOARD_RE = re.compile(r"navigator\.clipboard\.writeText\b")
_CLIPBOARD_OWNER = "frontend/src/utils/clipboard.ts"


def _frontend_clipboard_util() -> list[Violation]:
    """``navigator.clipboard.writeText`` is gated to secure contexts and
    silently throws on HTTP origins like the homelab. Every copy must
    route through ``utils/clipboard.copyTextToClipboard`` which has a
    ``document.execCommand`` fallback."""
    violations: list[Violation] = []
    root = REPO_ROOT / "frontend" / "src"
    for path in iter_files(root, (".ts", ".tsx")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == _CLIPBOARD_OWNER:
            continue
        for ln, _ in grep_lines(path, _CLIPBOARD_RE):
            violations.append(Violation(
                path, ln,
                "navigator.clipboard.writeText — use "
                "copyTextToClipboard from @/utils/clipboard "
                "(fails silently on HTTP origins)",
            ))
    return violations


_MODAL_OVERLAY_MARK = "fixed inset-0"
_MODAL_OPT_OUT = "not-a-dialog"
_MODAL_OWNER_TAGS = ("<div", "<form", "<aside", "<section")
_MODAL_LABELLEDBY_RE = re.compile(r'aria-labelledby="([^"]+)"')


def _frontend_modal_dialog_role() -> list[Violation]:
    """A full-screen ``fixed inset-0`` overlay is a modal. Without
    ``role="dialog"`` it is invisible to ``getByRole('dialog')`` and to a
    screen reader's dialog navigation, so tests fall back to anchoring on
    heading text — which is exactly how 14 modals drifted without one. The
    backdrop may instead carry ``role="presentation"`` when the dialog role
    sits on the panel inside it; both shapes are in use here."""
    violations: list[Violation] = []
    root = REPO_ROOT / "frontend" / "src"
    for path in iter_files(root, (".tsx",)):
        if path.name.endswith(".test.tsx"):
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines):
            if _MODAL_OVERLAY_MARK not in line:
                continue
            # The owning tag may sit several lines up when its attributes
            # are formatted one per line.
            start = idx if any(t in line for t in _MODAL_OWNER_TAGS) else None
            if start is None:
                for back in range(idx - 1, max(idx - 10, -1), -1):
                    if any(t in lines[back] for t in _MODAL_OWNER_TAGS):
                        start = back
                        break
            if start is None:
                continue
            window = chr(10).join(lines[start:idx + 7])
            if _MODAL_OPT_OUT in window:
                continue
            if 'role="dialog"' in window or 'role="presentation"' in window:
                continue
            violations.append(Violation(
                path, idx + 1,
                'fixed inset-0 overlay without role="dialog" — '
                "getByRole('dialog') cannot see it",
            ))

        # A dangling aria-labelledby is worse than none: the dialog role is
        # present, the gate above is satisfied, and the dialog still has NO
        # accessible name because the id it points at does not exist.
        text = path.read_text(encoding="utf-8")
        for ref in _MODAL_LABELLEDBY_RE.findall(text):
            if ('id="' + ref + '"') in text:
                continue
            line_no = text[:text.index('aria-labelledby="' + ref + '"')].count(chr(10)) + 1
            violations.append(Violation(
                path, line_no,
                'aria-labelledby="' + ref + '" matches no id in this file — '
                "the dialog ends up with no accessible name",
            ))
    return violations


_HEDGING_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_HEDGING_LINE_COMMENT_RE = re.compile(r"//.*$", re.MULTILINE)
_HEDGING_JSX_COMMENT_RE = re.compile(r"\{\s*/\*.*?\*/\s*\}", re.DOTALL)

# Verdict phrasings. Each entry is (pattern, why). They are deliberately
# narrow: they fire on copy that asserts the cause is KNOWN, and stay silent
# on hedged forms ("likely caused by", "suggested root cause", "suspects, not
# culprits") and on pipeline STAGE names ("Root Cause Analysis"), which name a
# stage rather than a conclusion.
_AI_HEDGING_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\broot cause\s*:", re.IGNORECASE),
        "a bare \"Root cause:\" label presents an AI suggestion as a verdict "
        "— use \"Suggested root cause\"",
    ),
    (
        re.compile(r"\broot cause summary\b", re.IGNORECASE),
        "\"Root Cause Summary\" asserts the cause is known — say "
        "\"Suggested root cause\"",
    ),
    (
        re.compile(r"\bthe (?:root )?cause (?:is|was)\b", re.IGNORECASE),
        "\"the cause is …\" states an AI inference as fact — hedge it "
        "(\"the suggested cause\", \"likely\")",
    ),
    (
        re.compile(r"\b(?:is|was|are|were)\s+caused by\b", re.IGNORECASE),
        "\"is caused by\" asserts causation the pipeline cannot establish — "
        "use \"likely caused by\" or \"consistent with\"",
    ),
)


def _strip_ts_comments(text: str) -> str:
    """Blank out comments while preserving line numbering.

    Copy lives in JSX text and string literals; comments legitimately quote
    the very phrasings this guard bans (this file's own docstring does).
    Newlines are preserved so reported line numbers stay accurate.
    """
    def _blank(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    text = _HEDGING_JSX_COMMENT_RE.sub(_blank, text)
    text = _HEDGING_BLOCK_COMMENT_RE.sub(_blank, text)
    return _HEDGING_LINE_COMMENT_RE.sub("", text)


def _frontend_ai_output_hedging() -> list[Violation]:
    """AI output is a suggestion for a human to confirm — never a verdict.

    US-15.1 put every AI-produced conclusion behind the shared
    ``components/ai/AISuggestion`` trust chrome ("AI-suggested" badge,
    confidence + calibration basis, routing provenance, evidence,
    confirm/correct). Chrome is worthless if the copy inside it still reads
    as fact, so this guard fails CI on the verdict phrasings that keep
    creeping back into rendered strings: a bare ``Root cause:`` label,
    ``Root Cause Summary``, ``the cause is …``, and ``is caused by``.

    Hedged phrasings pass on purpose — "Suggested root cause", "likely caused
    by", "Suspects, not culprits" — as do pipeline STAGE names such as "Root
    Cause Analysis", which name a stage of the pipeline rather than a
    conclusion about a failure.

    Deliberate blind spots (written down so nobody trusts the guard past its
    edge):

    * **Runtime-assembled copy.** Template literals interpolating variables,
      strings built by concatenation, and anything looked up from a map at
      render time are matched only if the banned words survive verbatim on
      one source line.
    * **Backend- and model-authored text.** The single largest source of
      un-hedged assertions is the LLM's own ``root_cause_summary`` prose,
      which is data, not source. API field names (``root_cause``,
      ``root_cause_summary``) are out of scope by design — renaming the wire
      contract is not a copy fix.
    * **Naive comment stripping.** ``//`` inside a string literal (a URL, a
      regex) truncates the rest of that line, so a banned phrase after it on
      the same line goes unseen.
    * **Semantics, not spelling.** A sentence that asserts causation without
      these exact words — "the culprit is", "blame:", "this broke because" —
      sails straight through. The guard catches the four phrasings we have
      actually shipped, not the concept.
    * **Tests and e2e specs** are exempt: they assert on copy, including copy
      they are proving is absent.
    """
    violations: list[Violation] = []
    root = REPO_ROOT / "frontend" / "src"
    for path in iter_files(root, (".ts", ".tsx")):
        name = path.name
        if ".test." in name or ".spec." in name:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        stripped = _strip_ts_comments(text)
        for lineno, line in enumerate(stripped.splitlines(), start=1):
            for pattern, why in _AI_HEDGING_PATTERNS:
                if pattern.search(line):
                    violations.append(Violation(path, lineno, why))
    return violations


_ALL_PROJECTS_LITERAL_RE = re.compile(
    # Look for "'all'" / '"all"' being assigned to a project_id-shaped
    # variable / param. Coarse but matches the failure mode from
    # CLAUDE.md pitfall #1.
    r"project_?[Ii]d\s*[:=]\s*['\"]all['\"]"
)


def _frontend_all_projects_literal() -> list[Violation]:
    """``ALL_PROJECTS_ID = 'all'`` is a frontend-only sentinel. Sending
    the literal string ``'all'`` to the backend as a UUID returns 422
    (no project filter is applied). Convert to ``null`` before the
    API call."""
    violations: list[Violation] = []
    root = REPO_ROOT / "frontend" / "src"
    for path in iter_files(root, (".ts", ".tsx")):
        for ln, _ in grep_lines(path, _ALL_PROJECTS_LITERAL_RE):
            violations.append(Violation(
                path, ln,
                "literal 'all' assigned to project_id — backend wants null",
            ))
    return violations


_AXIOS_CREATE_RE = re.compile(r"\baxios\.create\s*\(")
_AXIOS_OWNER = "frontend/src/services/api.ts"


_REFRESH_LITERAL_RE = re.compile(r"refreshInterval:\s*([0-9_]+)")


def _frontend_refresh_intervals_from_config() -> list[Violation]:
    """SWR poll cadences must come from config/refreshIntervals.ts.

    That file states "All hooks should import from here — never hardcode
    intervals" and defines four tiers. Nothing enforced it, and the drift ran
    2:1 against the rule: 10 hooks hardcoded 30 literals while 5 imported the
    tiers. Two hooks polled at 2s and 3s — faster than REALTIME (5s), which the
    config assigns to "live execution dashboards, agent pipelines", i.e. exactly
    those hooks.

    A scattered literal is not just style: poll cadence is server load, and
    nobody can see the total request rate when it is spread across ten files.

    ``refreshInterval: 0`` is allowed — that is SWR's "polling disabled", not a
    cadence. The SWR 2 function form is allowed too (see useFixer), since a
    dynamic interval cannot be a constant.
    """
    violations: list[Violation] = []
    root = REPO_ROOT / "frontend" / "src" / "hooks"
    if not root.exists():
        return violations
    for path in iter_files(root, (".ts", ".tsx")):
        if path.name.endswith((".test.ts", ".test.tsx")):
            continue
        for ln, line in grep_lines(path, _REFRESH_LITERAL_RE):
            m = _REFRESH_LITERAL_RE.search(line)
            if not m:
                continue
            if int(m.group(1).replace("_", "")) == 0:
                continue
            violations.append(Violation(
                path, ln,
                f"hardcoded refreshInterval {m.group(1)} — import a tier from "
                f"@/config/refreshIntervals so the app's total poll rate is "
                f"visible in one place",
            ))
    return violations


# Settings consumed by a property/validator *inside* config.py itself, so they
# legitimately have no reference elsewhere.
_SETTINGS_INTERNAL_ONLY = {"CORS_ORIGINS_RAW"}
_SETTINGS_FIELD_RE = re.compile(r"(?m)^\s{4}([A-Z][A-Z0-9_]{2,}):\s")


def _backend_project_scope_guard_placement() -> list[Violation]:
    """An access check must not sit inside an ``if not project_id`` branch.

    This exact shape has now leaked tenant data three times — digests (F-033),
    chat (F-040), and six more handlers found by the sweep after it (F-042)::

        if not project_id:
            accessible = await get_accessible_project_ids(db, current_user)
            if accessible is not None:
                return []
        return await service(db, project_id, ...)     # <- unguarded

    The guard fires only when there is nothing to guard. Supplying a
    ``project_id`` skips it entirely.

    Grepping for the guard cannot catch this, which is why it kept recurring:
    ``get_accessible_project_ids`` **is** imported and **is** called. The
    architectural authorization ratchet cannot catch it either — that one
    matches routers whose *path* declares ``{project_id}``, and here the id
    arrives as a query parameter. So the placement needs its own guard.

    Fix: call ``resolve_project_scope`` unconditionally. It 403s a non-admin
    naming a project they do not belong to and leaves ADMIN unrestricted.
    """
    violations: list[Violation] = []
    routers = REPO_ROOT / "backend" / "app" / "routers"
    if not routers.is_dir():
        return violations

    guards = ("get_accessible_project_ids", "resolve_project_scope")
    for path in sorted(routers.glob("*.py")):
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not re.match(r"if (not project_id|project_id is None)\s*:", stripped):
                continue
            indent = len(line) - len(line.lstrip())
            # Body of the branch: lines indented deeper, until it closes.
            body: list[str] = []
            for nxt in lines[i + 1 :]:
                if not nxt.strip():
                    body.append(nxt)
                    continue
                if (len(nxt) - len(nxt.lstrip())) <= indent:
                    break
                body.append(nxt)
            blob = chr(10).join(body)
            if not any(g in blob for g in guards):
                continue

            # A compensating check on the other path is fine: runs.py guards
            # the `if` branch by membership set and the `else` branch with
            # _require_accessible_project, so the leaking case *is* covered.
            # Look at the rest of the enclosing handler, excluding this branch.
            rest: list[str] = []
            for nxt in lines[i + 1 + len(body) :]:
                if nxt.startswith("@router.") or re.match(r"^(async )?def ", nxt):
                    break
                rest.append(nxt)
            if any(g in chr(10).join(rest) for g in (*guards, "_require_accessible_project")):
                continue

            violations.append(
                    Violation(
                        file=path,
                        line=i + 1,
                        message=(
                            "access check sits inside an `if not project_id` branch — "
                            "it cannot fire for the case that leaks. Call "
                            "resolve_project_scope unconditionally instead"
                        ),
                    )
                )
    return violations


def _backend_cloud_providers_are_priced() -> list[Violation]:
    """Every cloud LLM provider must have entries in the price table.

    A provider with no price meters $0.00 per call. That is not a missing
    number in a report — it is an invisible understatement of the bill, and it
    is exactly the state the whole backend was in before ``llm_pricing.py``
    existed: ``llm_cost_budget`` had a per-project USD cap that could never
    trip because every call cost nothing.

    Self-hosted providers are exempt: $0.00 is the correct answer for them.
    """
    violations: list[Violation] = []
    config_path = REPO_ROOT / "backend" / "app" / "core" / "config.py"
    pricing_path = REPO_ROOT / "backend" / "app" / "services" / "llm_pricing.py"
    if not config_path.exists() or not pricing_path.exists():
        return violations

    config_src = config_path.read_text(encoding="utf-8", errors="ignore")
    pricing_src = pricing_path.read_text(encoding="utf-8", errors="ignore")

    match = re.search(r"LLM_PROVIDER:\s*Literal\[([^\]]+)\]", config_src)
    if not match:
        return violations
    declared = set(re.findall(r'"([^"]+)"', match.group(1)))

    self_hosted: set[str] = set()
    sh_match = re.search(
        r"SELF_HOSTED_PROVIDERS\s*=\s*frozenset\(\{([^}]*)\}\)", pricing_src
    )
    if sh_match:
        self_hosted = set(re.findall(r'"([^"]+)"', sh_match.group(1)))

    priced = set(re.findall(r'^\s*\("([a-z_]+)",\s*r"', pricing_src, flags=re.MULTILINE))

    line_no = config_src[: match.start()].count(chr(10)) + 1
    for provider in sorted(declared - self_hosted - priced):
        violations.append(
            Violation(
                file=config_path,
                line=line_no,
                message=(
                    f"LLM provider '{provider}' has no entry in llm_pricing.PRICE_TABLE — "
                    f"every call through it meters $0.00 and under-states the bill silently"
                ),
            )
        )
    return violations


_INGEST_ROUTER_PATH = Path("backend") / "app" / "routers" / "ingest.py"
_UPLOAD_SERVICE_PATH = Path("frontend") / "src" / "services" / "reportUploadService.ts"
_BACKEND_INGEST_FORMATS_RE = re.compile(
    r"_SUPPORTED_FORMATS\s*=\s*\{(?P<body>[^}]*)\}", re.DOTALL
)
_FRONTEND_INGEST_FORMATS_RE = re.compile(
    r"SUPPORTED_FORMATS\s*:[^=]*=\s*\[(?P<body>.*?)\]", re.DOTALL
)


def _backend_ingest_formats() -> set[str] | None:
    path = REPO_ROOT / _INGEST_ROUTER_PATH
    if not path.exists():
        return None
    m = _BACKEND_INGEST_FORMATS_RE.search(path.read_text(encoding="utf-8", errors="ignore"))
    if not m:
        return None
    formats = set(re.findall(r'"([a-z0-9_]+)"', m.group("body")))
    return formats or None


def _frontend_ingest_formats() -> set[str] | None:
    path = REPO_ROOT / _UPLOAD_SERVICE_PATH
    if not path.exists():
        return None
    m = _FRONTEND_INGEST_FORMATS_RE.search(path.read_text(encoding="utf-8", errors="ignore"))
    if not m:
        return None
    formats = set(re.findall(r"value:\s*'([a-z0-9_]+)'", m.group("body")))
    return formats or None


def _ingest_formats_match_ui() -> list[Violation]:
    """The formats the upload UI advertises must equal what ``/ingest/file``
    accepts.

    ``routers/ingest.py::_SUPPORTED_FORMATS`` is the authority on what the
    endpoint will 202 rather than 400; ``reportUploadService.ts::SUPPORTED_FORMATS``
    is the dropdown a self-hoster picks from (and what the first-run guide
    derives its advertised-format list from). The two are hand-maintained on
    opposite sides of a language boundary, so nothing but this check couples
    them.

    A drift is a silent adoption defect in either direction:

    * a value the UI offers but the backend rejects → the user picks it, uploads,
      and gets a 400 for a format we told them we support;
    * a parser the backend gains but the UI never lists → a real capability that
      is invisible in the product and under-advertised in onboarding.

    Both files must parse to a non-empty set; if either registry cannot be read
    (a structural refactor moved it) the check no-ops rather than firing a false
    positive — the paired unit tests pin the current shape.
    """
    violations: list[Violation] = []
    backend = _backend_ingest_formats()
    frontend = _frontend_ingest_formats()
    if backend is None or frontend is None:
        return violations

    ui_path = REPO_ROOT / _UPLOAD_SERVICE_PATH
    router_path = REPO_ROOT / _INGEST_ROUTER_PATH
    ui_line = next(
        (ln for ln, _ in grep_lines(ui_path, re.compile(r"SUPPORTED_FORMATS\s*:"))),
        1,
    )
    router_line = next(
        (ln for ln, _ in grep_lines(router_path, re.compile(r"_SUPPORTED_FORMATS\s*="))),
        1,
    )

    for fmt in sorted(frontend - backend):
        violations.append(Violation(
            ui_path, ui_line,
            f"upload UI advertises format '{fmt}', but /ingest/file rejects it "
            f"(not in routers/ingest.py::_SUPPORTED_FORMATS) — the user gets a 400 "
            f"for a format we told them we support",
        ))
    for fmt in sorted(backend - frontend):
        violations.append(Violation(
            router_path, router_line,
            f"/ingest/file accepts format '{fmt}', but the upload UI never offers "
            f"it (not in reportUploadService.ts::SUPPORTED_FORMATS) — a real "
            f"capability invisible in the product and under-advertised in onboarding",
        ))
    return violations


def _backend_settings_are_consumed() -> list[Violation]:
    """Every Settings field must be read somewhere.

    A config field nobody reads is a knob that silently does nothing. An
    operator sets it, restarts, and the behaviour is unchanged — the worst kind
    of configuration bug, because it looks like it worked.

    Found three: DEEP_CLUSTER_THRESHOLD and DEEP_MAX_CLUSTERS_PER_RUN described a
    similarity-clustering design that was never built, and
    KNOWLEDGE_SYNC_TIMEOUT_SECONDS sat unread while the connectors hardcoded
    10s/15s/20s.

    Searches Python plus the deployment surfaces (compose/k8s/env/shell), since
    a setting may legitimately be consumed only as an env var.
    """
    violations: list[Violation] = []
    cfg_path = REPO_ROOT / "backend" / "app" / "core" / "config.py"
    if not cfg_path.exists():
        return violations
    raw = cfg_path.read_text(encoding="utf-8", errors="ignore")
    body = re.sub(r'""".*?"""', "", raw, flags=re.S)
    body = re.sub(r"(?m)^\s*#.*$", "", body)
    names = sorted(set(_SETTINGS_FIELD_RE.findall(body)))

    blobs: list[str] = []
    for path in iter_files(REPO_ROOT / "backend", (".py",)):
        sp = path.as_posix()
        if "/tests/" in sp or sp.endswith("app/core/config.py") or "migrations/versions" in sp:
            continue
        blobs.append(path.read_text(encoding="utf-8", errors="ignore"))
    for sub in ("k8s", "docker", "scripts", "cli", "mcp"):
        d = REPO_ROOT / sub
        if d.exists():
            for path in iter_files(d, (".yaml", ".yml", ".py", ".sh", ".env")):
                blobs.append(path.read_text(encoding="utf-8", errors="ignore"))
    for name in ("docker-compose.yml", "docker-compose.airgap.yml", ".env.example", "Makefile"):
        f = REPO_ROOT / name
        if f.exists():
            blobs.append(f.read_text(encoding="utf-8", errors="ignore"))
    hay = chr(10).join(blobs)

    for ln, line in grep_lines(cfg_path, _SETTINGS_FIELD_RE):
        m = _SETTINGS_FIELD_RE.search(line)
        if not m:
            continue
        name = m.group(1)
        if name not in names or name in _SETTINGS_INTERNAL_ONLY or name in hay:
            continue
        violations.append(Violation(
            cfg_path, ln,
            f"{name} is never read — a knob that does nothing. Wire it up, or "
            f"delete it so operators are not misled into tuning it.",
        ))
    return violations


def _frontend_single_axios() -> list[Violation]:
    """One Axios instance owns auth refresh + 401 queue. A second
    instance silently bypasses the refresh interceptor, so 401s
    surface to the UI instead of getting transparently retried."""
    violations: list[Violation] = []
    root = REPO_ROOT / "frontend" / "src"
    for path in iter_files(root, (".ts", ".tsx")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == _AXIOS_OWNER:
            continue
        for ln, _ in grep_lines(path, _AXIOS_CREATE_RE):
            violations.append(Violation(
                path, ln,
                "second axios.create() — import the shared instance "
                "from @/services/api (or use getData/postData helpers)",
            ))
    return violations


_FETCH_IN_PAGE_RE = re.compile(r"\b(fetch|axios)\s*\(")
_PAGE_FILE_RE = re.compile(r"frontend[\\/]src[\\/]pages[\\/]")


def _frontend_swr_only_fetching() -> list[Violation]:
    """Pages must read through an SWR hook so refresh / dedupe /
    refetch-on-project-switch all work consistently. Raw ``fetch`` or
    ``axios`` inside a page is a regression — the page won't refresh
    when the user changes project."""
    violations: list[Violation] = []
    root = REPO_ROOT / "frontend" / "src" / "pages"
    for path in iter_files(root, (".tsx",)):
        if path.name.endswith(".test.tsx"):
            continue
        for ln, line in grep_lines(path, _FETCH_IN_PAGE_RE):
            # Skip obvious benign cases (createObjectURL fetcher refs, etc.).
            if "URL.createObjectURL" in line or "window.fetch" in line:
                continue
            violations.append(Violation(
                path, ln,
                "raw fetch/axios in a page — use a hook in src/hooks/ "
                "wrapping SWR or useProjectScopedSWR",
            ))
    return violations


# ── Database guards ──────────────────────────────────────────────────────────

_DOWN_REV_RE = re.compile(r"^down_revision\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
_REV_RE = re.compile(r"^revision\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)


def _database_single_alembic_head() -> list[Violation]:
    """Multiple Alembic heads block ``alembic upgrade head`` and break
    container startup. The fix is to pick a fresh ``down_revision``
    when generating a migration — see the ``feedback_alembic_head_conflicts``
    memory."""
    versions = REPO_ROOT / "backend" / "migrations" / "versions"
    if not versions.exists():
        return []
    revisions: dict[str, Path] = {}
    children: dict[str, list[str]] = {}
    for path in iter_files(versions, (".py",)):
        text = path.read_text(encoding="utf-8")
        rev_m = _REV_RE.search(text)
        down_m = _DOWN_REV_RE.search(text)
        if not rev_m:
            continue
        rev = rev_m.group(1)
        revisions[rev] = path
        if down_m:
            children.setdefault(down_m.group(1), []).append(rev)
    # A head is a revision with no children.
    heads = [r for r in revisions if r not in children]
    if len(heads) <= 1:
        return []
    return [
        Violation(
            revisions[h], 0,
            f"multiple Alembic heads detected: {sorted(heads)} — "
            "re-base your migration's down_revision onto the current head",
        )
        for h in heads
    ]


_MIGRATION_DOWN_RE = re.compile(r"^def\s+downgrade\s*\(", re.MULTILINE)


_MODEL_MODULE = "app.models.postgres"


def _model_module_exported_names() -> set[str]:
    """Top-level names ``app.models.postgres`` provides, parsed not imported.

    Importing it would build the SQLAlchemy engine (``app/db/postgres.py`` does
    that at import time), which needs ``DATABASE_URL`` — absent when the gate
    runs. An import-based check therefore **fails open**, reporting OK because
    it could not look rather than because nothing was wrong. That is worse than
    no guard, so this reads the file instead.
    """
    import ast

    path = REPO_ROOT / "backend" / "app" / "models" / "postgres.py"
    if not path.exists():
        return set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            # Re-exports are legitimately importable from this module.
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _backend_model_imports_resolve() -> list[Violation]:
    """Every ``from app.models.postgres import X`` names something real.

    This exists because of a defect that reached a live deployment and stayed
    invisible there. ``flaky_score_service.score_project`` imported
    ``PerformanceBaseline``; the class is called ``PerfBaseline``. Three things
    conspired to hide it:

    1. The import sat **inside the function**, so nothing raised at module
       import and no linter or type-check pass flagged the module.
    2. Its only caller wrapped each project in ``except Exception`` — by design,
       so one bad project cannot stop a nightly sweep — which turned a hard
       ``ImportError`` into a single warning line.
    3. The unit tests exercised the pure scoring functions, not the query path,
       so the import statement never executed in CI.

    The result was a headline feature computing nothing, on every project, for
    as long as it had been deployed, while reporting success.
    """
    import ast

    exported = _model_module_exported_names()
    if not exported:
        # Could not read the model module at all. Say nothing rather than
        # accuse every import in the tree of being wrong.
        return []

    violations: list[Violation] = []
    root = REPO_ROOT / "backend" / "app"
    for path in iter_files(root, (".py",)):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            # Absolute (``app.models.postgres``) or relative
            # (``from ..models.postgres import``). The codebase uses absolute
            # today; matching both means a later style change cannot silently
            # open a hole in this guard.
            module = node.module or ""
            if not (module == _MODEL_MODULE
                    or (node.level and module.endswith("models.postgres"))):
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                if alias.name not in exported:
                    violations.append(Violation(
                        path,
                        node.lineno,
                        f"imports {alias.name!r} from {_MODEL_MODULE}, "
                        "which does not define it",
                    ))
    return violations


# ── Status-literal vocabulary (FIX-002) ──────────────────────────────────────
#
# ``FlakyQuarantineRequest.status`` stores UPPERCASE ``FlakyQuarantineStatus``
# values; the Fixer's candidate selector filtered it with lowercase literals.
# ``status.in_(("quarantined", ...))`` matched nothing, ever — so the whole
# feature was inert while every run logged ``status=completed error=0``. Two
# of the three literals were not stored values in ANY casing, so the drift was
# a vocabulary error, not only a casing one.
#
# The guard only inspects models that TIE their status column to an enum
# (``default=SomeStatus.X.value``). Models whose status is a free string (most
# of them) have no declared vocabulary to check against, and inventing one for
# them would be guesswork that fails noisily on correct code.


def _status_enum_vocabularies() -> tuple[dict[str, str], dict[str, set[str]]]:
    """``(model name -> status-enum name, enum name -> its string values)``.

    Parsed, never imported — importing ``app.models.postgres`` builds the
    SQLAlchemy engine and needs ``DATABASE_URL``, so an import-based check
    would fail open (see ``_model_module_exported_names``).
    """
    path = REPO_ROOT / "backend" / "app" / "models" / "postgres.py"
    if not path.exists():
        return {}, {}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return {}, {}

    enums: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {getattr(b, "id", getattr(b, "attr", "")) for b in node.bases}
        if not ({"Enum", "PyEnum"} & bases):
            continue
        values = {
            stmt.value.value
            for stmt in node.body
            if isinstance(stmt, ast.Assign)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        }
        if values:
            enums[node.name] = values

    model_enum: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if not (isinstance(stmt, ast.AnnAssign)
                    and getattr(stmt.target, "id", "") == "status"
                    and stmt.value is not None):
                continue
            # ``default=FlakyQuarantineStatus.PROPOSED.value`` — walk to the
            # root Name of any attribute chain and keep the one that names an
            # enum we parsed.
            for sub in ast.walk(stmt.value):
                if not isinstance(sub, ast.Attribute):
                    continue
                root = sub
                while isinstance(root, ast.Attribute):
                    root = root.value
                if isinstance(root, ast.Name) and root.id in enums:
                    model_enum[node.name] = root.id
                    break
    return model_enum, enums


def _module_level_string_sequences(tree: ast.Module) -> dict[str, list[tuple[str, int]]]:
    """Module-level ``NAME = ("a", "b")`` constants, as ``(value, lineno)``.

    Needed because the FIX-002 literals lived in a module constant, not inline
    in the ``.in_()`` call — a guard that only read call arguments would have
    walked straight past the defect it exists to catch.
    """
    consts: dict[str, list[tuple[str, int]]] = {}
    for stmt in tree.body:
        target = value = None
        if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)):
            target, value = stmt.targets[0].id, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            target, value = stmt.target.id, stmt.value
        if target is None or not isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            continue
        strings = [
            (elt.value, elt.lineno)
            for elt in value.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        ]
        if strings:
            consts[target] = strings
    return consts


def _backend_status_enum_vocab() -> list[Violation]:
    """Status literals compared against an enum-backed ``status`` column must
    be values of that enum.

    Catches the producer/consumer vocabulary drift that made the Fixer inert:
    a filter whose terms the column can never hold returns nothing forever,
    and an empty result set is indistinguishable from "no matching rows".
    Nothing errors, so nothing surfaces.
    """
    models_path = REPO_ROOT / "backend" / "app" / "models" / "postgres.py"
    model_enum, enums = _status_enum_vocabularies()
    if not model_enum:
        # Fail LOUD rather than open. A guard that reports OK because it could
        # not read its own reference data is the same shape as the defect it
        # exists to catch: silence that reads as success.
        if not models_path.exists():
            return []
        return [Violation(
            models_path,
            1,
            "no status column could be tied to a status enum — this guard "
            "checked nothing. Either the model module moved, or a status "
            "column stopped declaring its enum default.",
        )]

    violations: list[Violation] = []
    root = REPO_ROOT / "backend" / "app"
    for path in iter_files(root, (".py",)):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        consts = _module_level_string_sequences(tree)

        for node in ast.walk(tree):
            owner: Optional[ast.AST] = None
            literals: list[tuple[str, int]] = []

            # ``Model.status.in_([...])`` / ``.in_(_SOME_CONSTANT)``
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "in_"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "status"):
                owner = node.func.value.value
                for arg in node.args:
                    if isinstance(arg, (ast.List, ast.Tuple, ast.Set)):
                        literals += [
                            (e.value, e.lineno) for e in arg.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)
                        ]
                    elif isinstance(arg, ast.Name) and arg.id in consts:
                        literals += consts[arg.id]

            # ``Model.status == "..."`` / ``!=``
            elif (isinstance(node, ast.Compare)
                    and isinstance(node.left, ast.Attribute)
                    and node.left.attr == "status"
                    and len(node.ops) == 1
                    and isinstance(node.ops[0], (ast.Eq, ast.NotEq))
                    and isinstance(node.comparators[0], ast.Constant)
                    and isinstance(node.comparators[0].value, str)):
                owner = node.left.value
                literals = [(node.comparators[0].value, node.lineno)]

            model = getattr(owner, "id", None)
            if model not in model_enum or not literals:
                continue
            enum_name = model_enum[model]
            vocabulary = enums[enum_name]
            for value, lineno in literals:
                if value in vocabulary:
                    continue
                hint = ""
                for known in vocabulary:
                    if known.lower() == value.lower():
                        hint = f" (did you mean {known!r}?)"
                        break
                violations.append(Violation(
                    path,
                    lineno,
                    f"{model}.status compared to {value!r}, which is not a "
                    f"{enum_name} value{hint} — this filter matches nothing",
                ))
    return violations


def _backend_streaming_body_not_rebound() -> list[Violation]:
    """``async with response["Body"] as x`` throws the usable object away.

    ``response["Body"]`` is aiobotocore's ``StreamingBody``, a wrapt proxy
    that supports ``read(amt)`` and ``iter_chunks(n)``. But::

        StreamingBody.__aenter__  -> return await self.__wrapped__.__aenter__()
        ClientResponse.__aenter__ -> return self

    so the ``as`` rebinds the target to the bare ``aiohttp.ClientResponse``,
    which has neither method -- its signature is ``read(self)``.

    ``S3StorageProvider.stream_object`` shipped in that shape and raised
    ``TypeError: ClientResponse.read() takes 1 positional argument but 2 were
    given`` on every call it ever made, from the day it was written (#824).

    Nothing else can catch this. The sibling ``get_object_content`` used the
    same shape and worked, because a *no-argument* ``read()`` is valid on
    ClientResponse -- verified byte-identical against live MinIO. So the
    hazard is invisible to any behavioural test until someone passes a size,
    at which point it fails 100% of the time. A static guard is the only
    thing that can hold it.

    Entering the context is still required (it releases the connection);
    only the ``as`` is forbidden. Bind first::

        body = response["Body"]
        async with body:
            ...
    """
    import ast

    violations: list[Violation] = []
    root = REPO_ROOT / "backend" / "app"
    for path in iter_files(root, (".py",)):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            # Sync ``with`` too: the same trap exists for botocore's
            # StreamingBody, and a later port must not get a free pass.
            if not isinstance(node, (ast.AsyncWith, ast.With)):
                continue
            for item in node.items:
                if item.optional_vars is None:
                    continue  # `async with body:` -- the correct shape
                expr = item.context_expr
                if not isinstance(expr, ast.Subscript):
                    continue
                key = expr.slice
                if not (isinstance(key, ast.Constant) and key.value == "Body"):
                    continue
                violations.append(Violation(
                    path,
                    node.lineno,
                    "`with ...[\"Body\"] as X` rebinds X to the bare "
                    "ClientResponse, dropping the StreamingBody proxy "
                    "(no read(size), no iter_chunks)",
                ))
    return violations


def _database_downgrade_implemented() -> list[Violation]:
    """Every migration must implement ``downgrade()``. Empty stubs
    block rollback in incident response."""
    versions = REPO_ROOT / "backend" / "migrations" / "versions"
    if not versions.exists():
        return []
    violations: list[Violation] = []
    for path in iter_files(versions, (".py",)):
        text = path.read_text(encoding="utf-8")
        if not _MIGRATION_DOWN_RE.search(text):
            violations.append(Violation(
                path, 0, "missing downgrade() function",
            ))
            continue
        # Detect ``def downgrade(): pass`` / ``def downgrade(): ...``
        # bodies — same effect as missing. Cheap heuristic that skips
        # docstrings + comments so a downgrade that only contains a
        # ``"""no-op: index drop is implicit"""`` docstring still counts
        # as deliberate (the author has explained it).
        after = text.split("def downgrade", 1)[1].split("\ndef ", 1)[0]
        body_lines = after.splitlines()[1:]
        in_docstring = False
        has_explanation = False  # docstring OR inline comment
        meaningful: list[str] = []
        for raw in body_lines:
            s = raw.strip()
            if not s:
                continue
            if in_docstring:
                has_explanation = True
                if s.endswith('"""') or s.endswith("'''"):
                    in_docstring = False
                continue
            if s.startswith('"""') or s.startswith("'''"):
                has_explanation = True
                # Single-line docstring closes on the same line; multi-
                # line stays open until we hit a closing triple-quote.
                if not (len(s) >= 6 and (s.endswith('"""') or s.endswith("'''"))):
                    in_docstring = True
                continue
            if s.startswith("#"):
                # Accept comments as no-op explanations too — many
                # migrations explain "intentional no-op" inline rather
                # than via docstring (e.g. 0045_clean_user_role_strings).
                has_explanation = True
                continue
            meaningful.append(s)
        # Empty body = no docstring, no comments, no statements (or
        # only ``pass`` / ``...``). A docstring or inline comment is
        # treated as a deliberate no-op explanation and passes.
        is_empty = not meaningful or all(s in {"pass", "..."} for s in meaningful)
        if is_empty and not has_explanation:
            violations.append(Violation(
                path, 0,
                "downgrade() is empty — implement reverse of upgrade() "
                "or add a docstring explaining why rollback is unsupported",
            ))
    return violations


# ── Agentic AI guards ────────────────────────────────────────────────────────

_BASE_AGENT_RE = re.compile(r"class\s+\w+\s*\(\s*BaseAgent\s*[,)]")
_SUPPORT_AGENT_FILES = {
    # Support modules in app/agents/ that are NOT agent implementations.
    "__init__.py", "state.py", "workflow.py", "conversation.py", "base.py",
    # consistency.py holds the AIQ-P2 self-critique Pydantic models + pure-local
    # checker functions; it has no agent class and no observability contract.
    "consistency.py",
    # evidence.py holds the AIQ-P3 EvidenceRef model + aggregate_confidence
    # helper; it is a pure-local scoring utility with no agent class and no
    # observability contract.
    "evidence.py",
    # Fixer (AI-2) support modules — NOT LangGraph pipeline agents. runners.py
    # holds the sandbox ValidationRunner executors (subprocess/HTTP), pipeline.py
    # holds pure stage helpers (candidate selection, glob rejection, PR opener,
    # outcome poller). Their audit surface is the fix_attempts rows + the
    # agent_runs ledger, not the BaseAgent stage/decision contract.
    "runners.py", "pipeline.py", "persistence.py",
    # RunCompareAgent is not a pipeline stage. It is driven from
    # run_compare_ai_service.generate_and_save_report on a REST path -- there is
    # no AgentPipelineRun and no AgentStageResult row for it to write, so
    # BaseAgent's lifecycle would find nothing to update and no-op while
    # *looking* compliant, which is worse than the honest plain class. Its audit
    # surface is the run_compare_reports row (status / ai_report /
    # fallback_used), same reasoning as the Fixer modules above.
    "run_compare_agent.py",
}


def _agents_base_agent_subclass() -> list[Violation]:
    """Every agent implementation in ``backend/app/agents/`` must
    subclass ``BaseAgent`` so its stages, decision log, and OTEL
    spans land in the standard tables. Standalone classes silently
    skip the observability contract."""
    root = REPO_ROOT / "backend" / "app" / "agents"
    violations: list[Violation] = []
    for path in iter_files(root, (".py",)):
        if path.name in _SUPPORT_AGENT_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        if "class " not in text:
            continue
        if not _BASE_AGENT_RE.search(text):
            violations.append(Violation(
                path, 0,
                "agent class does not subclass BaseAgent — "
                "stage tracking + decision_log + OTEL spans will be missed",
            ))
    return violations


def _agent_class_graph() -> list[tuple[Path, ast.ClassDef]]:
    """Every class under ``app/agents/`` that reaches ``BaseAgent`` by any path.

    Resolved across files by class name, because the inheritance that matters
    here spans modules (``InfraHypothesisAgent`` -> ``HypothesisAgent`` ->
    ``BaseAgent``). Name-based resolution cannot tell two same-named classes in
    different modules apart; that is acceptable in this tree and fails toward
    *including* a class rather than skipping it.
    """
    bases: dict[str, list[str]] = {}
    located: list[tuple[Path, ast.ClassDef]] = []
    for path in iter_files(REPO_ROOT / "backend" / "app" / "agents", (".py",)):
        if path.name in _SUPPORT_AGENT_FILES:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # a broken file fails louder elsewhere
            continue
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            bases[cls.name] = [
                getattr(b, "id", getattr(b, "attr", "")) for b in cls.bases
            ]
            located.append((path, cls))

    def derives(name: str, seen: frozenset = frozenset()) -> bool:
        if name in seen:  # defensive; a cycle cannot occur in valid Python
            return False
        for base in bases.get(name, []):
            if base == "BaseAgent" or derives(base, seen | {name}):
                return True
        return False

    return [(path, cls) for path, cls in located if derives(cls.name)]


def _defines_run(cls: ast.ClassDef) -> bool:
    return any(
        isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name == "run"
        for m in cls.body
    )


def _delegates_to_super_run(cls: ast.ClassDef) -> bool:
    """``super().run(...)`` — the parent's decisions still fire, so this is fine."""
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Call)
        and getattr(node.func.value.func, "id", "") == "super"
        for node in ast.walk(cls)
    )


def _class_calls(cls: ast.ClassDef, attr: str) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
        for node in ast.walk(cls)
    )


def _agents_log_decision_present() -> list[Violation]:
    """Every agent that implements ``run`` records at least one decision.

    ``AgentStageResult.decision_log`` is the "why did the agent do that"
    surface; an agent that never calls ``log_decision`` leaves it empty.

    Two widenings, each from a real miss:

    * **By AST, not substring.** ``"log_decision" in text`` was satisfied by a
      docstring, a comment, or a ``# TODO``, so a module could describe the
      trail it never wrote and pass.
    * **Following inheritance, not just direct bases.** The check matched
      ``class X(BaseAgent)`` only. Probing the deployment with ``issubclass``
      surfaced seven agents the guard could not see — the five hypothesis
      agents plus the two ``_Standalone*`` variants — all of which were fine,
      because they inherit ``run``. A subclass that *overrode* ``run`` without
      logging would have been missed entirely.

    The rule is therefore anchored on ``run``: the class that implements the
    stage owes the decision. A class that inherits ``run``, or delegates with
    ``super().run(...)``, is covered wherever that ``run`` is defined.
    """
    violations: list[Violation] = []
    for path, cls in _agent_class_graph():
        if not _defines_run(cls):
            continue  # inherits a run(), checked where that run() is defined
        if _class_calls(cls, "log_decision") or _delegates_to_super_run(cls):
            continue
        violations.append(Violation(
            path, cls.lineno,
            f"{cls.name} implements run() but never calls self.log_decision(...) — "
            "every non-trivial routing/fallback/skip branch should be logged",
        ))
    return violations


# ── Homelab guards ───────────────────────────────────────────────────────────

_HOMELAB_OVERLAY_REL = "k8s/overlays/homelab/kustomization.yaml"
# Images whose ``newTag:`` must remain the placeholder at rest. Mirrors
# the ``images:`` block at the bottom of the homelab overlay; if a new
# locally-built image is added there, append it here too.
_HOMELAB_PINNED_IMAGES = (
    "testlookup/backend",
    "testlookup/frontend",
    "testlookup/mcp",
)
_HOMELAB_PLACEHOLDER = "BUILD_TAG_PLACEHOLDER"

# Match a kustomize ``images:`` entry block:
#   - name: testlookup/backend
#     newName: registry.local:30500/testlookup/backend
#     newTag: BUILD_TAG_PLACEHOLDER
# Captures the image name and the newTag value on the line that follows
# (one or more lines down). Tolerant of intervening ``newName:`` /
# blank / comment lines so the layout in the overlay can evolve without
# breaking the guard. The non-greedy ``.*?`` + DOTALL is bounded by the
# next ``- name:`` or end-of-string via a positive lookahead — we want
# only the ``newTag:`` belonging to *this* image, not the one for the
# next image in the list.
_HOMELAB_IMAGE_BLOCK_RE = re.compile(
    r"-\s*name:\s*(?P<name>\S+)"
    r"(?P<body>.*?)"
    r"(?=^\s*-\s*name:|\Z)",
    re.DOTALL | re.MULTILINE,
)
_HOMELAB_NEW_TAG_RE = re.compile(
    r"^\s*newTag:\s*(?P<tag>\S+)\s*$",
    re.MULTILINE,
)


def _homelab_build_tag_placeholder() -> list[Violation]:
    """The homelab overlay's ``newTag:`` for every locally-built image
    must equal ``BUILD_TAG_PLACEHOLDER`` at rest. ``deploy-homelab.sh``
    substitutes it in-place per-deploy and ``trap``s a restore on exit,
    but a hard kill (SIGKILL, runner OOM, power loss between the sed
    and the restore) can leave a real timestamp committed by mistake.

    The existing ``k8s-image-pin-check`` CI guard already rejects the
    word ``latest``; this guard closes the matching hole for any other
    accidentally-committed substituted tag (e.g. ``build-20260518-013421``).
    """
    path = REPO_ROOT / _HOMELAB_OVERLAY_REL
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")

    # Build a name → (newTag value, line number) map by walking the
    # ``images:`` block entries. Line numbers are best-effort: we record
    # the line of the ``newTag:`` match so the failure points at the
    # exact offender, not the top of the file.
    line_offsets: list[int] = [0]
    running = 0
    for ch in text:
        if ch == "\n":
            line_offsets.append(running + 1)
        running += 1

    def char_to_line(offset: int) -> int:
        """1-indexed line number for a character offset."""
        # Linear scan is fine — overlay file is < 500 lines.
        for i, start in enumerate(line_offsets):
            if start > offset:
                return i
        return len(line_offsets)

    violations: list[Violation] = []
    seen: dict[str, tuple[str, int]] = {}
    for block in _HOMELAB_IMAGE_BLOCK_RE.finditer(text):
        name = block.group("name").strip()
        if name not in _HOMELAB_PINNED_IMAGES:
            continue
        tag_match = _HOMELAB_NEW_TAG_RE.search(block.group("body"))
        if not tag_match:
            # Missing newTag is a bug in the overlay shape — flag it so
            # nobody silently regresses to a floating tag.
            seen[name] = ("", char_to_line(block.start()))
            continue
        # Translate the match's offset back to the absolute file offset
        # so the line number points at the real ``newTag:`` line.
        abs_offset = block.start("body") + tag_match.start("tag")
        seen[name] = (tag_match.group("tag").strip(), char_to_line(abs_offset))

    for name in _HOMELAB_PINNED_IMAGES:
        entry = seen.get(name)
        if entry is None:
            violations.append(Violation(
                path, 0,
                f"homelab overlay missing 'images:' entry for {name!r} — "
                "every locally-built image must be pinned to "
                f"{_HOMELAB_PLACEHOLDER}",
            ))
            continue
        tag, line = entry
        if not tag:
            violations.append(Violation(
                path, line,
                f"{name!r} has no newTag — pin it to {_HOMELAB_PLACEHOLDER} "
                "so deploy-homelab.sh substitutes it per-deploy",
            ))
            continue
        if tag != _HOMELAB_PLACEHOLDER:
            violations.append(Violation(
                path, line,
                f"{name!r} newTag is {tag!r} — must be "
                f"{_HOMELAB_PLACEHOLDER}. A previous deploy-homelab.sh "
                "run was likely killed mid-deploy before its trap "
                "could restore the placeholder. Reset with: "
                f"git checkout -- {_HOMELAB_OVERLAY_REL}",
            ))
    return violations


_CAPABILITY_REGISTRY_REL = "backend/app/services/agent_capability_registry.py"
_AGENT_PLANNER_REL = "backend/app/services/agent_planner.py"
# Stage-order tuples in agent_planner.py. A workflow type added without being
# listed here would make the guard read fewer planned stages than exist, so the
# companion check below asserts this set still covers every _*_STAGES tuple.
_PLANNER_STAGE_TUPLES = (
    "_OFFLINE_STAGES", "_LIVE_STAGES", "_INVESTIGATION_STAGES", "_DEEP_STAGES",
)
_NON_PLANNED_EXECUTIONS = {"child_spawned", "on_demand", "runtime"}


def _string_tuple_assignments(tree: ast.Module) -> dict[str, set[str]]:
    """Every ``NAME = ("a", "b", ...)`` in a module, as {NAME: {values}}."""
    found: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if not isinstance(node.value, (ast.Tuple, ast.List, ast.Set)):
            continue
        values = {
            elt.value for elt in node.value.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        }
        if values:
            found[target.id] = values
    return found


def _declared_capabilities(tree: ast.Module) -> dict[str, str]:
    """{stage_name: execution} for every ``_capability("name", ...)`` call."""
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "_capability" or not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        execution = "planned"
        for kw in node.keywords:
            if kw.arg == "execution" and isinstance(kw.value, ast.Constant):
                execution = str(kw.value.value)
        found[first.value] = execution
    return found


def _agents_capability_has_executor() -> list[Violation]:
    """Every declared capability has something that actually runs it.

    ``defect_commander`` sat in the registry with ``permission="mutating"`` and
    ``dependencies=("root_cause_analysis",)`` -- reading exactly like a pipeline
    stage -- while appearing in no workflow's stage order. It had never written
    a single ``agent_stage_results`` row on the deployment. Nothing was wrong
    with the agent; it runs on demand via ``POST /api/v1/agents/defect-command``.
    What was wrong was the record, which described a stage nobody plans.

    The rule has two directions, and both matter:

    * a capability that says ``execution="planned"`` must appear in a stage
      order, or it is describing a stage that never runs;
    * a capability that appears in a stage order must NOT claim to be
      ``on_demand`` / ``child_spawned`` / ``runtime``, or the declaration is
      lying in the other direction.
    """
    registry_path = REPO_ROOT / _CAPABILITY_REGISTRY_REL
    planner_path = REPO_ROOT / _AGENT_PLANNER_REL
    if not registry_path.exists() or not planner_path.exists():
        return []
    try:
        registry_tree = ast.parse(registry_path.read_text(encoding="utf-8"))
        planner_tree = ast.parse(planner_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    capabilities = _declared_capabilities(registry_tree)
    tuples = _string_tuple_assignments(planner_tree)
    planned: set[str] = set()
    for name in _PLANNER_STAGE_TUPLES:
        planned |= tuples.get(name, set())

    # Fail loudly rather than silently reading an empty plan: an empty `planned`
    # would report every capability as an orphan, and a renamed tuple would make
    # this guard quietly stop covering a whole workflow type.
    missing_tuples = [n for n in _PLANNER_STAGE_TUPLES if n not in tuples]
    if missing_tuples:
        return [Violation(
            planner_path, 0,
            f"stage-order tuple(s) {missing_tuples} not found — this guard reads "
            "the planner by name and has lost sight of a workflow type",
        )]

    violations: list[Violation] = []
    for stage, execution in sorted(capabilities.items()):
        if execution == "planned" and stage not in planned:
            violations.append(Violation(
                registry_path, 0,
                f"capability '{stage}' declares execution='planned' but no workflow "
                "stage order contains it — it describes a stage nothing runs; give "
                "it an executor or declare execution='on_demand'/'child_spawned'/'runtime'",
            ))
        elif execution in _NON_PLANNED_EXECUTIONS and stage in planned:
            violations.append(Violation(
                registry_path, 0,
                f"capability '{stage}' declares execution='{execution}' but a workflow "
                "stage order plans it — the declaration contradicts the planner",
            ))
    return violations


def _agents_routing_metadata_populated() -> list[Violation]:
    """``analysis_router.classify_test`` must populate a ``_routing``
    dict on every return so per-test rows carry mode-used /
    fallback-from / fallback-reason. Without it, the UI cannot
    explain why a given test was classified by rules vs. LLM."""
    path = REPO_ROOT / "backend" / "app" / "services" / "analysis_router.py"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if '"_routing"' not in text and "_routing'" not in text:
        return [Violation(
            path, 0,
            "analysis_router.classify_test does not assign _routing — "
            "decision traceability contract is broken",
        )]
    return []


# ── AI prompt-manifest guard (AI-F2) ─────────────────────────────────────────
# Every LLM prompt is registered in backend/app/services/prompt_registry.py
# (MCP templates in mcp/prompts/templates.py::PROMPT_TEMPLATES) and pinned —
# sha256(text)[:12] + version — in prompt_manifest.json. A prompt edit without
# a deliberate manifest bump fails here; a manifest bump without a fresh
# eval-gate attestation (prompt_manifest_eval.json) also fails here. This is
# the CI blocker that makes "no prompt change ships without an eval run" real.
# Stdlib-only mirror of app.services.prompt_registry's checks: sources are
# parsed via ast, never imported.

_PROMPT_REGISTRY_PATH = REPO_ROOT / "backend" / "app" / "services" / "prompt_registry.py"
_PROMPT_MANIFEST_PATH = REPO_ROOT / "backend" / "app" / "services" / "prompt_manifest.json"
_PROMPT_ATTESTATION_PATH = REPO_ROOT / "backend" / "app" / "services" / "prompt_manifest_eval.json"
_MCP_PROMPT_TEMPLATES_PATH = REPO_ROOT / "mcp" / "prompts" / "templates.py"
_PROMPT_HASH_LEN = 12
_PROMPT_ATTEST_HINT = (
    "cd backend && python -m app.services.prompt_registry --write-manifest && "
    "python -m app.services.prompt_registry --attest <change-id> "
    "(add --offline without a live DB)"
)


def _prompt_content_hash(text: str) -> str:
    """MUST mirror app.services.prompt_registry.content_hash."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_PROMPT_HASH_LEN]


def _prompt_manifest_digest(prompts: dict) -> str:
    """MUST mirror app.services.prompt_registry.manifest_digest."""
    canonical = json.dumps(prompts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _extract_registry_prompts(path: Path) -> tuple[dict, list[tuple[int, str]]]:
    """``{id: {"version", "content_hash", "line"}}`` from ``_register(...)`` calls."""
    prompts: dict = {}
    problems: list[tuple[int, str]] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as exc:
        return {}, [(0, f"cannot parse prompt registry: {exc}")]
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_register"
        ):
            continue
        try:
            pid = ast.literal_eval(node.args[0])
            version = int(ast.literal_eval(node.args[1]))
            text = ast.literal_eval(node.args[2])
        except (ValueError, TypeError, IndexError):
            problems.append((
                node.lineno,
                "_register(...) call is not fully literal — the manifest "
                "guard cannot hash it; keep prompt texts as plain string "
                "literals",
            ))
            continue
        prompts[pid] = {
            "version": version,
            "content_hash": _prompt_content_hash(str(text)),
            "line": node.lineno,
        }
    if not prompts and not problems:
        problems.append((0, "no _register(...) calls found in the prompt registry"))
    return prompts, problems


def _extract_mcp_prompts(path: Path) -> tuple[dict, list[tuple[int, str]]]:
    """``{id: {"version", "content_hash", "line"}}`` from ``PROMPT_TEMPLATES``."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as exc:
        return {}, [(0, f"cannot parse MCP prompt templates: {exc}")]
    templates: dict = {}
    versions: dict = {}
    line = 0
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
            value = node.value
        else:
            continue
        if value is None:
            continue
        try:
            if "PROMPT_TEMPLATES" in names:
                templates = dict(ast.literal_eval(value))
                line = node.lineno
            elif "PROMPT_TEMPLATE_VERSIONS" in names:
                versions = dict(ast.literal_eval(value))
        except (ValueError, TypeError):
            return {}, [(node.lineno, "PROMPT_TEMPLATES is not a literal dict")]
    if not templates:
        return {}, [(0, "no PROMPT_TEMPLATES dict found in MCP templates module")]
    return {
        pid: {
            "version": int(versions.get(pid, 1)),
            "content_hash": _prompt_content_hash(str(text)),
            "line": line,
        }
        for pid, text in templates.items()
    }, []


def _ai_prompt_manifest_sync(
    registry_path: Optional[Path] = None,
    manifest_path: Optional[Path] = None,
    attestation_path: Optional[Path] = None,
    mcp_path: Optional[Path] = None,
) -> list[Violation]:
    registry_path = registry_path or _PROMPT_REGISTRY_PATH
    manifest_path = manifest_path or _PROMPT_MANIFEST_PATH
    attestation_path = attestation_path or _PROMPT_ATTESTATION_PATH
    mcp_path = mcp_path or _MCP_PROMPT_TEMPLATES_PATH

    violations: list[Violation] = []

    current: dict = {}
    reg_prompts, reg_problems = _extract_registry_prompts(registry_path)
    for line, msg in reg_problems:
        violations.append(Violation(registry_path, line, msg))
    current.update({pid: (entry, registry_path) for pid, entry in reg_prompts.items()})

    if mcp_path.exists():
        mcp_prompts, mcp_problems = _extract_mcp_prompts(mcp_path)
        for line, msg in mcp_problems:
            violations.append(Violation(mcp_path, line, msg))
        current.update({pid: (entry, mcp_path) for pid, entry in mcp_prompts.items()})

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        pinned = manifest["prompts"]
        assert isinstance(pinned, dict)
    except (OSError, ValueError, KeyError, AssertionError) as exc:
        violations.append(Violation(
            manifest_path, 0,
            f"prompt manifest missing/unreadable ({exc}) — {_PROMPT_ATTEST_HINT}",
        ))
        return violations

    for pid, (entry, src_path) in sorted(current.items()):
        pin = pinned.get(pid)
        if pin is None:
            violations.append(Violation(
                src_path, entry["line"],
                f"prompt {pid!r} is not pinned in the manifest — {_PROMPT_ATTEST_HINT}",
            ))
            continue
        if pin.get("content_hash") != entry["content_hash"]:
            violations.append(Violation(
                src_path, entry["line"],
                f"prompt {pid!r} text drifted from the manifest (hash "
                f"{entry['content_hash']} != pinned {pin.get('content_hash')}). "
                f"Bump its version, then: {_PROMPT_ATTEST_HINT}",
            ))
        elif pin.get("version") != entry["version"]:
            violations.append(Violation(
                src_path, entry["line"],
                f"prompt {pid!r} version {entry['version']} != pinned "
                f"{pin.get('version')} — {_PROMPT_ATTEST_HINT}",
            ))
    for pid in sorted(pinned):
        if pid not in current:
            violations.append(Violation(
                manifest_path, 0,
                f"manifest pins {pid!r} but no such prompt is registered "
                f"(stale entry) — {_PROMPT_ATTEST_HINT}",
            ))

    # Attestation: the manifest digest must carry a green eval-gate run.
    digest = _prompt_manifest_digest(pinned)
    try:
        attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        violations.append(Violation(
            attestation_path, 0,
            f"eval-gate attestation missing/unreadable ({exc}) — a prompt-"
            f"manifest change requires an eval run: {_PROMPT_ATTEST_HINT}",
        ))
        return violations
    if attestation.get("manifest_digest") != digest:
        violations.append(Violation(
            attestation_path, 0,
            "prompt manifest changed WITHOUT a fresh eval-gate attestation "
            f"(attested digest {str(attestation.get('manifest_digest'))[:12]}… != "
            f"current {digest[:12]}…) — {_PROMPT_ATTEST_HINT}",
        ))
    if attestation.get("verdict") != "PASS":
        violations.append(Violation(
            attestation_path, 0,
            f"attested eval-gate verdict is {attestation.get('verdict')!r}, not "
            "PASS — the prompt change did not clear the eval gate; fix the "
            "regression (or re-baseline deliberately) and re-attest",
        ))
    return violations


# ── Registry ─────────────────────────────────────────────────────────────────

# ── repo.no-gitignored-source ────────────────────────────────────────────────
#
# `.gitignore` carries deliberately broad security globs — `*credentials*`,
# `*secrets*`, `*api_key*`, `*apikey*` — that match at every depth and do not
# care about file type. They also match ordinary source.
#
# This has cost real incidents twice:
#
#   1. A regression test named ``test_no_shared_default_credentials.py`` was
#      silently excluded. ``git add`` printed a hint and committed everything
#      else, so a security fix landed UNGUARDED.
#   2. A production module named ``project_credentials_service.py`` — the one
#      ``routers/projects.py`` imports — was excluded the same way. Had that
#      commit gone through unchecked, ``main`` would have carried a router
#      importing a file that is not in the repository.
#
# Both were caught by hand, by remembering to run ``git check-ignore``. This
# guard is that habit, automated.
#
# ``git check-ignore`` is the authority — reimplementing gitignore matching is
# how a guard ends up disagreeing with the thing it guards.

# ``backend/tests`` and ``frontend/tests`` are NOT optional here. Incident 1
# above was a TEST file — ``test_no_shared_default_credentials.py`` — and a
# roots tuple covering only application code misses the exact case that
# motivated this guard. Verified: with ``backend/tests`` absent, recreating
# incident 1 did not trip the gate.
_SOURCE_ROOTS = (
    "backend/app", "backend/tests", "backend/scripts", "backend/migrations",
    "frontend/src", "frontend/tests",
    "cli", "mcp", "client", "scripts",
)
_SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".sql"}
_SOURCE_SKIP_DIRS = {
    "__pycache__", "node_modules", ".venv", "venv", "dist", "build", "target",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
}

_GITIGNORED_SOURCE_HINT = (
    "Rename the file so it no longer matches the pattern (that is what "
    "project_credentials_service.py -> project_access_revocation_service.py "
    "was), or narrow the glob in .gitignore if it is genuinely too broad. "
    "Never reach for `git add -f`: the next person to add the file will not "
    "know to."
)


def _source_candidates() -> list[str]:
    """Repo-relative POSIX paths of the source files worth checking."""
    out: list[str] = []
    for root in _SOURCE_ROOTS:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in _SOURCE_SUFFIXES:
                continue
            if _SOURCE_SKIP_DIRS & set(path.parts):
                continue
            out.append(path.relative_to(REPO_ROOT).as_posix())
    return out


def _git_tracked(paths: Iterable[str]) -> set[str]:
    """The subset of ``paths`` git already tracks."""
    proc = subprocess.run(
        ["git", "ls-files", "-z", "--"],
        cwd=REPO_ROOT, capture_output=True,
    )
    if proc.returncode != 0:
        return set()
    tracked = set(proc.stdout.decode("utf-8", "replace").split("\0"))
    return {p for p in paths if p in tracked}


def _repo_no_gitignored_source() -> list[Violation]:
    candidates = _source_candidates()

    # Fail-open check. If the scan finds nothing, "no violations" would mean
    # "the walk broke", not "the tree is clean" — the same shape as a check
    # that reports OK because it could not look.
    if len(candidates) < 100:
        return [Violation(
            REPO_ROOT / ".gitignore", 0,
            f"the source scan found only {len(candidates)} files; the guard "
            "cannot have looked properly, so a pass here would be meaningless",
        )]

    # ``--no-index`` asks the question that matters: does the PATH match an
    # ignore pattern? Without it, git answers "no" for anything already
    # tracked, and CI — where every file is tracked — could never fail.
    #
    # ``-c core.ignorecase=false`` pins the ANSWER to the one CI gets. A Windows
    # checkout sets ``core.ignorecase = true``, so git matched the lowercase
    # glob ``*apikey*`` against camelCase source like ``ApiKeysPage.tsx``;
    # case-sensitive Linux CI did not. The same tree therefore failed the gate
    # locally and passed it in CI — five violations that only existed on one
    # platform. A guard whose verdict depends on the developer's filesystem
    # teaches people to ignore it. CI is the authority, so ask CI's question.
    proc = subprocess.run(
        ["git", "-c", "core.ignorecase=false",
         "check-ignore", "--stdin", "-v", "--no-index"],
        cwd=REPO_ROOT,
        # BYTES, deliberately. On Windows a TEXT stdin translates "\n" into
        # "\r\n", git takes the CR as part of the filename, and every answer
        # comes back wrong. This cost a full round of bogus measurements.
        input=b"\n".join(p.encode() for p in candidates),
        capture_output=True,
    )
    # 0 = something matched, 1 = nothing matched. Anything else is git
    # failing, which must not read as "clean".
    if proc.returncode not in (0, 1):
        return [Violation(
            REPO_ROOT / ".gitignore", 0,
            "git check-ignore failed "
            f"({proc.stderr.decode('utf-8', 'replace').strip()[:200]}) — "
            "the guard could not look, which is not the same as a pass",
        )]

    matches: list[tuple[str, str]] = []
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        rule, sep, path = line.rpartition("\t")
        if sep:
            matches.append((path, rule))

    tracked = _git_tracked(p for p, _ in matches)
    violations: list[Violation] = []
    for path, rule in sorted(matches):
        if path in tracked:
            message = (
                f"source file matches the ignore rule `{rule}` — it survives "
                "only because it is already tracked. Delete-and-recreate, or "
                "any tool that re-adds it, loses it silently"
            )
        else:
            message = (
                f"UNTRACKED source file matches the ignore rule `{rule}` — "
                "`git add` will skip it and your commit will land without it"
            )
        violations.append(Violation(REPO_ROOT / path, 0, message))
    return violations


GUARDS: list[Guard] = [
    Guard(
        name="backend.no-print",
        description="No print() in backend/app/ — use structlog.",
        check=_backend_no_print,
        fix_hint="Replace print(...) with logger.info(...) / .warning(...) / .error(...).",
    ),
    Guard(
        name="backend.analysis-router",
        description="Direct calls to run_triage_agent / RulesEngine / MLClassifier — must go through services.analysis_router.",
        check=_backend_analysis_router_bypass,
        fix_hint="Replace with `await analysis_router.classify_test(...)` so mode + fallback + routing metadata are recorded.",
    ),
    Guard(
        name="backend.finalize-run",
        description="Ingestion owners (ingestion_pipeline, worker.tasks) must call finalize_run().",
        check=_backend_finalize_run_wired,
        fix_hint="After persisting TestCase rows, call services.ingestion_pipeline.finalize_run(...) inside a try/except.",
    ),
    Guard(
        name="backend.pii-log-redaction",
        description="logger.* calls passing PII (email / password / api_key) verbatim.",
        check=_backend_pii_log_redaction,
        fix_hint="Wrap the value with privacy_service.sanitize_for_logging(...) before logging.",
    ),
    Guard(
        name="backend.structlog-positional-args",
        description=(
            "structlog logger called with positional %s args — explodes "
            "with TypeError mid-call (BoundLogger.warning is (event, **kw))."
        ),
        check=_backend_structlog_positional_args,
        fix_hint=(
            "Use kwargs: ``logger.warning(\"event_name\", error=str(exc))``. "
            "See memory/feedback_structlog_positional_args.md."
        ),
    ),
    Guard(
        name="backend.stdlib-logger-kwargs",
        description=(
            "stdlib logging.Logger called with structlog-style keyword "
            "fields — Logger._log() raises TypeError when the line runs, "
            "which in an except block destroys the diagnostic and skips "
            "the scrubbed re-raise."
        ),
        check=_backend_stdlib_logger_kwargs,
        fix_hint=(
            "Use the module's structlog logger for keyword fields "
            "(``_slog.error(\"x_failed\", error_type=...)``), or keep "
            "stdlib and format positionally "
            "(``logger.error(\"x failed: %s\", exc)``). This is the "
            "mirror of backend.structlog-positional-args."
        ),
    ),
    Guard(
        name="backend.dedup-lock-allows-retry",
        description=(
            "A task that calls self.retry() must pass owner= to _is_duplicate "
            "— otherwise the retry meets the failed attempt's own dedup "
            "lock, logs 'skipping duplicate' and returns success having "
            "done nothing, so the work is dropped silently."
        ),
        check=_backend_dedup_lock_allows_retry,
        fix_hint=(
            "Set ``dedup_owner = str(self.request.id)`` and pass "
            "``owner=dedup_owner`` so the same task id can reacquire, and "
            "release the lock on the error path so a redelivery after the "
            "retries are exhausted is not refused for the whole TTL. If "
            "suppressing the retry is deliberate (the side effect is "
            "irreversible, e.g. mail already sent), mark the call "
            "``at-most-once:`` in a comment and say why."
        ),
    ),
    Guard(
        name="backend.audit-write-discipline",
        description=(
            "Audit tables (settings_audit_log / access_audit_logs / "
            "test_case_audit_logs / identity_events) are append-only: no "
            "UPDATE anywhere, no DELETE outside the retention purge."
        ),
        check=_backend_audit_write_discipline,
        fix_hint=(
            "Append a NEW audit row instead of mutating one. If a purge is "
            "genuinely needed, extend services/retention_service.py (the "
            "audit-clock deleter) rather than deleting inline — the model "
            "docstrings promise exactly that boundary."
        ),
    ),
    Guard(
        name="frontend.modal-dialog-role",
        description=(
            "A `fixed inset-0` modal overlay must carry role=\"dialog\" "
            "(or role=\"presentation\" when the dialog role is on the panel "
            "inside it) — otherwise getByRole('dialog') cannot see it."
        ),
        check=_frontend_modal_dialog_role,
        fix_hint=(
            "Add role=\"dialog\" aria-modal=\"true\" and name it with "
            "aria-labelledby pointing at the modal heading's id — a static "
            "aria-label goes stale when the title is dynamic. A full-screen "
            "overlay that genuinely is not a dialog can opt out with a "
            "`not-a-dialog` comment on the element."
        ),
    ),
    Guard(
        name="frontend.clipboard-util",
        description="navigator.clipboard.writeText outside utils/clipboard.ts — breaks on HTTP origins.",
        check=_frontend_clipboard_util,
        fix_hint="`import { copyTextToClipboard } from '@/utils/clipboard'` and call that instead.",
    ),
    Guard(
        name="frontend.ai-output-hedging",
        description=(
            "AI output rendered as a verdict — a bare \"Root cause:\", "
            "\"Root Cause Summary\", \"the cause is …\" or \"is caused by\" "
            "in UI copy. AI conclusions are suggestions to confirm."
        ),
        check=_frontend_ai_output_hedging,
        fix_hint=(
            "Hedge the copy (\"Suggested root cause\", \"likely caused by\") "
            "and render the conclusion inside components/ai/AISuggestion so "
            "it carries the badge, basis and provenance (US-15.1). Pipeline "
            "STAGE names like \"Root Cause Analysis\" are already allowed."
        ),
    ),
    Guard(
        name="frontend.all-projects-literal",
        description="Literal 'all' assigned to project_id — never send to backend.",
        check=_frontend_all_projects_literal,
        fix_hint="Use `activeProjectId === ALL_PROJECTS_ID` guard and pass `null` to the API.",
    ),
    Guard(
        name="backend.project-scope-guard-placement",
        description="Access checks must not sit inside an `if not project_id` branch.",
        check=_backend_project_scope_guard_placement,
        fix_hint="Call resolve_project_scope(db, user, project_id) unconditionally; it 403s a non-admin naming a project they cannot access.",
    ),
    Guard(
        name="backend.cloud-providers-are-priced",
        description="Every non-self-hosted LLM provider has price-table entries.",
        check=_backend_cloud_providers_are_priced,
        fix_hint="Add a (provider, model-regex, ModelPrice) row to services/llm_pricing.py PRICE_TABLE.",
    ),
    Guard(
        name="frontend.ingest-formats-match-backend",
        description="The upload UI's advertised formats equal what /ingest/file accepts.",
        check=_ingest_formats_match_ui,
        fix_hint=(
            "Add or remove the format on BOTH sides so they match: "
            "reportUploadService.ts::SUPPORTED_FORMATS (value + label, and the "
            "ReportFormat union) and routers/ingest.py::_SUPPORTED_FORMATS. A new "
            "backend parser also needs its detection wired in _detect_format."
        ),
    ),
    Guard(
        name="backend.settings-are-consumed",
        description="Every Settings field is read somewhere — no dead config knobs.",
        check=_backend_settings_are_consumed,
        fix_hint="Reference it in code (or a compose/k8s/env surface), or remove the field.",
    ),
    Guard(
        name="frontend.refresh-intervals-from-config",
        description="SWR poll cadences come from config/refreshIntervals.ts tiers.",
        check=_frontend_refresh_intervals_from_config,
        fix_hint="`import { REFRESH_INTERVALS } from '@/config/refreshIntervals'` and use REALTIME / ACTIVE / POLLING / BACKGROUND (0 = disabled is fine).",
    ),
    Guard(
        name="frontend.single-axios",
        description="Only one axios.create() allowed (services/api.ts).",
        check=_frontend_single_axios,
        fix_hint="`import { api } from '@/services/api'` and reuse it (or use getData/postData from services/http.ts).",
    ),
    Guard(
        name="frontend.swr-only-fetching",
        description="Pages must fetch via SWR hooks, not raw fetch/axios.",
        check=_frontend_swr_only_fetching,
        fix_hint="Move the call into a hook under src/hooks/ that wraps useSWR / useProjectScopedSWR.",
    ),
    Guard(
        name="database.single-alembic-head",
        description="Single Alembic head — no merge conflicts in down_revision chain.",
        check=_database_single_alembic_head,
        fix_hint="Re-base your migration's down_revision onto the current head (see feedback_alembic_head_conflicts memory).",
    ),
    Guard(
        name="backend.model-imports-resolve",
        description=(
            "Every 'from app.models.postgres import X' names a real class — a "
            "function-local import of a misspelled model is invisible until it "
            "runs, and a broad except turns it into a warning."
        ),
        check=_backend_model_imports_resolve,
        fix_hint=(
            "Correct the class name (check app/models/postgres.py — e.g. the "
            "perf_baselines table's class is PerfBaseline, not "
            "PerformanceBaseline)."
        ),
    ),
    Guard(
        name="backend.status-enum-vocab",
        description=(
            "Status literals filtered against an enum-backed status "
            "column must be values of that enum — a mismatched "
            "vocabulary makes the query match nothing, silently and "
            "forever."
        ),
        check=_backend_status_enum_vocab,
        fix_hint=(
            "Build the filter from the enum "
            "(``FlakyQuarantineStatus.QUARANTINED.value``), never from a "
            "hand-written string. See FIX-002: the Fixer selected zero "
            "candidates on every run while reporting success."
        ),
    ),
    Guard(
        name="backend.streaming-body-not-rebound",
        description=(
            "`async with response[\"Body\"] as X` rebinds X to the bare "
            "aiohttp ClientResponse, silently dropping aiobotocore's "
            "StreamingBody proxy — the shape that made S3 stream_object raise "
            "TypeError on every call it ever made."
        ),
        check=_backend_streaming_body_not_rebound,
        fix_hint=(
            "Bind the proxy before entering the context: "
            "``body = response[\"Body\"]`` then ``async with body:`` and use "
            "``body``. Entering the context is still required (it releases the "
            "connection); only the ``as`` is wrong. No behavioural test can "
            "catch this — a no-argument read() works on ClientResponse too, so "
            "the bug stays invisible until someone passes a size."
        ),
    ),
    Guard(
        name="repo.no-gitignored-source",
        description=(
            "No source file is matched by .gitignore — the security globs "
            "(*credentials*, *secrets*, *api_key*) match at every depth and "
            "have twice silently excluded real code from a commit."
        ),
        check=_repo_no_gitignored_source,
        fix_hint=_GITIGNORED_SOURCE_HINT,
    ),
    Guard(
        name="database.downgrade-implemented",
        description="Every migration has a real downgrade() body.",
        check=_database_downgrade_implemented,
        fix_hint="Implement the inverse operations, or add a one-line docstring explaining why rollback is unsupported.",
    ),
    Guard(
        name="agents.base-agent-subclass",
        description="Every agent in app/agents/ subclasses BaseAgent.",
        check=_agents_base_agent_subclass,
        fix_hint="`class FooAgent(BaseAgent):` so stage tracking + decision log + OTEL spans work.",
    ),
    Guard(
        name="agents.log-decision-present",
        description=(
            "Every agent that implements run() calls self.log_decision(...) "
            "at least once — following inheritance, so a subclass that "
            "overrides run() owes its own decision trail."
        ),
        check=_agents_log_decision_present,
        fix_hint=(
            "Log every non-trivial routing/fallback/skip branch via "
            "`await self.log_decision(...)`. A subclass that only extends "
            "behaviour can delegate with `await super().run(state)` instead — "
            "the parent's decisions still fire."
        ),
    ),
    Guard(
        name="agents.capability-has-executor",
        description=(
            "Every capability in the registry is actually executed by "
            "something: planned in a workflow stage order, spawned as a "
            "child, exposed on demand, or runtime bookkeeping."
        ),
        check=_agents_capability_has_executor,
        fix_hint=(
            "Either add the stage to the right _*_STAGES tuple in "
            "app/services/agent_planner.py, or declare how it really runs: "
            "_capability(\"name\", execution=\"on_demand\", ...). "
            "defect_commander read as a mutating pipeline stage for months "
            "while having no executor at all."
        ),
    ),
    Guard(
        name="agents.routing-metadata",
        description="analysis_router.classify_test populates the _routing dict on every return.",
        check=_agents_routing_metadata_populated,
        fix_hint="Set `result['_routing'] = {...}` before returning from classify_test (mode_requested / mode_resolved / mode_used / fallback_*).",
    ),
    Guard(
        name="ai.prompt-manifest-sync",
        description=(
            "Every LLM prompt (registry + MCP templates) matches its pinned "
            "hash in prompt_manifest.json, and the manifest digest carries a "
            "green eval-gate attestation (prompt_manifest_eval.json)."
        ),
        check=_ai_prompt_manifest_sync,
        fix_hint=(
            "Bump the prompt's version in app/services/prompt_registry.py "
            "(or PROMPT_TEMPLATE_VERSIONS for mcp.* prompts), then: "
            + _PROMPT_ATTEST_HINT
            + ". See architecture/AI_EVALUATION.md."
        ),
    ),
    Guard(
        name="homelab.build-tag-placeholder",
        description=(
            "k8s/overlays/homelab/kustomization.yaml must keep newTag: "
            "BUILD_TAG_PLACEHOLDER for every locally-built image at rest."
        ),
        check=_homelab_build_tag_placeholder,
        fix_hint=(
            "Run `git checkout -- k8s/overlays/homelab/kustomization.yaml` "
            "to restore the placeholder. A previous deploy-homelab.sh run "
            "was likely killed before its EXIT trap could fire."
        ),
    ),
]

GUARD_BY_NAME = {g.name: g for g in GUARDS}


# ── CLI ──────────────────────────────────────────────────────────────────────


def run_guard(guard: Guard, update_baseline: bool) -> bool:
    baseline = guard.load_baseline()
    raw = assign_occurrences(guard.check())
    keys = {v.key for v in raw}

    # Entries that are not 16-hex fingerprints are leftovers from the old
    # <relpath>:<lineno> scheme. They tolerate nothing, so say so loudly
    # instead of letting the guard report every baselined violation as new
    # with no explanation.
    legacy = sorted(k for k in baseline if not _BASELINE_KEY_RE.match(k))
    fingerprints = {k for k in baseline if _BASELINE_KEY_RE.match(k)}

    new = [v for v in raw if v.key not in fingerprints]
    stale = fingerprints - keys

    if update_baseline:
        guard.save_baseline(raw)
        if not guard.baseline_path.exists():
            print(f"{BLUE}== {guard.name}{RESET}: clean, and has no baseline "
                  f"file — left without one (absolute rule, not a ratchet)")
        else:
            print(f"{BLUE}== {guard.name}{RESET}: wrote {len(keys)} entries to "
                  f"{guard.baseline_path.relative_to(REPO_ROOT)}")
        return True

    if legacy:
        print(f"{YELLOW}!!{RESET}  {guard.name}: {len(legacy)} baseline "
              f"{'entries use' if len(legacy) != 1 else 'entry uses'} the old "
              f"<path>:<lineno> format and now tolerate nothing. Regenerate "
              f"with `python scripts/quality_gate.py --only {guard.name} "
              f"--update-baseline`.")
        for k in legacy:
            print(f"    {DIM}legacy: {k}{RESET}")

    if not new and not stale and not legacy:
        print(f"{GREEN}OK{RESET}  {guard.name} {DIM}({guard.description}){RESET}")
        return True

    if stale:
        # Baseline drift means the code is cleaner than the baseline
        # claims. Not a failure — just nudge the developer to rerun
        # ``--update-baseline``. The key is a hash, so print the annotation
        # recorded beside it or the entry is unactionable.
        print(f"{YELLOW}!!{RESET}  {guard.name}: {len(stale)} baseline "
              f"entries are stale (the code is fixed). Run "
              f"`python scripts/quality_gate.py --only {guard.name} "
              f"--update-baseline` to prune.")
        for k in sorted(stale, key=lambda e: (baseline.get(e, ""), e)):
            note = baseline.get(k) or "(no recorded location)"
            print(f"    {DIM}stale: {k}  {note}{RESET}")

    if new:
        print(f"{RED}FAIL{RESET} {guard.name} -- {len(new)} new violation"
              f"{'s' if len(new) != 1 else ''}:")
        for v in new:
            print(f"    {v.format()}")
        if guard.fix_hint:
            print(f"  {DIM}fix: {guard.fix_hint}{RESET}")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true",
                        help="Print every guard name and exit.")
    parser.add_argument("--only", nargs="+", metavar="GUARD",
                        help="Run a subset of guards (by name).")
    parser.add_argument("--update-baseline", action="store_true",
                        help="Rewrite the baseline file(s) for the selected guard(s). "
                             "Disabled in CI by convention — review baseline changes in PRs.")
    args = parser.parse_args()

    if args.list:
        for g in GUARDS:
            print(f"  {g.name:<40}  {g.description}")
        return 0

    selected: list[Guard]
    if args.only:
        unknown = [n for n in args.only if n not in GUARD_BY_NAME]
        if unknown:
            print(f"{RED}unknown guard(s){RESET}: {unknown}", file=sys.stderr)
            print(f"  valid names: {', '.join(GUARD_BY_NAME)}", file=sys.stderr)
            return 2
        selected = [GUARD_BY_NAME[n] for n in args.only]
    else:
        selected = GUARDS

    print(f"{BLUE}TestLookup quality gate — {len(selected)} guard"
          f"{'s' if len(selected) != 1 else ''}{RESET}")
    failures = 0
    for g in selected:
        ok = run_guard(g, update_baseline=args.update_baseline)
        if not ok:
            failures += 1

    if failures:
        print(f"\n{RED}quality gate FAILED -- {failures} guard"
              f"{'s' if failures != 1 else ''} blocked{RESET}", file=sys.stderr)
        return 1
    print(f"\n{GREEN}quality gate passed{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
