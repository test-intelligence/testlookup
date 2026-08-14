"""Duplicate authored-test-case detection (Phase 4 of granular test detail).

Tiered, **offline-first**, **per-project** duplicate detection over the AUTHORED
``managed_test_cases`` table (NOT the execution ``test_cases``). Finds pairs of
authored cases that say the same thing so a QA lead can review / merge them.

Transaction model (item #2): every function here STAGES ONLY
(``db.add`` / mutate / ``db.flush``) and returns. The **caller owns the commit**
— routers via ``await db.commit()``, and the Celery beat task
(``run_duplicate_detection`` in ``app.worker.tasks``) which is an allowlisted
worker-owned commit. This module must keep **zero** ``db.commit()`` calls so the
``test_architectural_transaction_boundaries`` stage-only ratchet stays green.

Tiers
-----
- **Tier 0 — fingerprint (always, pure).** ``dup_fingerprint`` = a sha256 over
  the *normalised* ``title|objective|steps_text|expected_result``, truncated to
  32 hex chars. Computed lazily here (no migration backfill). Two cases in the
  same project with an equal non-empty fingerprint are an *exact* duplicate:
  band ``exact``, score ``1.0``, method ``fingerprint``.
- **Tier 1 — structural (always, offline, STDLIB).** Blocking (see below) keeps
  this off the O(n²) all-pairs path; each surviving candidate pair is scored
  with ``difflib.SequenceMatcher`` (title + combined text), a pure-Python
  token-set / Jaccard ratio, and a step-sequence ratio — weighted and
  renormalised when a case has no steps. Bands ``strong`` / ``possible`` by
  threshold. method ``structural``. ``reason`` names the components that drove
  the score (e.g. ``"title 0.92, steps 0.88"``).
- **Tier 2 — semantic (optional, gated).** Per-project ChromaDB nearest-neighbour
  ONLY when a LOCAL embedder is available (bundled ONNX MiniLM) and the cloud
  path is not in play. Under ``AI_OFFLINE_MODE`` we still use the LOCAL embedder
  (it never reaches out), but the whole tier is *best-effort*: any import / client
  error skips it silently and the run is labelled structural-only. method
  ``semantic``. NEVER a cloud embedding function.

Blocking strategy (avoids O(n²))
--------------------------------
We never compare all pairs. Candidate pairs are generated only when two cases
share a **blocking key**:
  * same effective suite (``suite_name``), OR
  * at least one overlapping tag, OR
  * a shared *rare* title token (a token appearing in only a handful of cases —
    common stop-ish tokens that bucket too many cases are dropped).
Each block is itself capped (``_MAX_BLOCK_SIZE``); blocks larger than the cap are
truncated with a logged WARNING. A global pair budget (``_MAX_PAIR_BUDGET``)
bounds total comparisons; once exhausted the run is flagged ``sampled`` with a
human-readable ``note`` so the API/UI can surface that detection was capped.

Idempotency / suppression
--------------------------
Candidate rows are canonical-ordered (``case_a_id < case_b_id``) and upserted on
``(project_id, case_a_id, case_b_id)``. A pair present in
``dismissed_duplicate_pairs`` is SKIPPED (stays dismissed across re-runs).
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.postgres import (
    DismissedDuplicatePair,
    DuplicateTestCaseCandidate,
    ManagedTestCase,
)

logger = structlog.get_logger("services.duplicate_detection")


# ── Tuning knobs ─────────────────────────────────────────────────────────────

# Structural-tier thresholds (Tier 1). Bands are assigned on the aggregate score.
# Raised from the original round 0.85/0.70 after the scoring metric was made
# token-aware (char-ratio dominance produced systematic false positives on
# near-miss titles like "valid"/"invalid", "200"/"404"). A pair must ALSO clear
# ``STRONG_TITLE_JACCARD_FLOOR`` on the title token-set before it can reach the
# ``strong`` band, which alone drops the valid/invalid + 200/404 false positives.
STRONG_THRESHOLD = 0.90
POSSIBLE_THRESHOLD = 0.78
# Minimum title token-set Jaccard required for a pair to be banded ``strong``.
STRONG_TITLE_JACCARD_FLOOR = 0.5

# A title token appearing in more than this fraction of the project's cases is
# too common to be a useful blocking key (it would bucket everything together).
_RARE_TOKEN_MAX_DF = 0.30
# ...but never treat a token as "rare" once it appears in this many cases —
# guards a tiny project where every token is technically < 30% but a block of
# (n) cases is still fine, and a large project where 30% is enormous.
_RARE_TOKEN_ABS_CAP = 50

# Per-block and global comparison budgets. Blocking already prunes hard; these
# are the last line of defence against a pathological project.
_MAX_BLOCK_SIZE = 200          # cases per block before truncation (logged)
_MAX_PAIR_BUDGET = 50_000      # total candidate-pair comparisons per project
_MAX_CASES_PER_PROJECT = 20_000  # above this we sample (oldest-first) + warn

# Tokenisation: words of >= 3 chars, lowercased.
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_MIN_TOKEN_LEN = 3

# Tier-2 semantic acceptance: only surface a neighbour as ``possible`` when the
# cosine distance is small enough AND the structural score didn't already claim
# it. Kept conservative because the local MiniLM embeddings are coarse.
_SEMANTIC_MAX_DISTANCE = 0.25
_SEMANTIC_TOP_K = 5
# Hard upper bound on how many cases the semantic tier will embed/index/query.
# Unlike pair COMPARISONS (blocked + budgeted), the embed+index+self-query cost
# is O(n) over the cases handed to it, so it must be capped independently or it
# becomes the dominant unbounded cost on a large project. Above this the tier is
# skipped with a logged note folded into ``sampled``/``note``.
_MAX_SEMANTIC_CASES = 3_000


# ── Tier 0 — fingerprint ─────────────────────────────────────────────────────

def _normalise_text(value: Optional[str]) -> str:
    """Lowercase, collapse whitespace, strip punctuation to spaces.

    Pure + deterministic so the same logical content always hashes the same.
    """
    if not value:
        return ""
    lowered = value.lower()
    # Punctuation → space, then collapse runs of whitespace.
    cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
    return " ".join(cleaned.split())


def _steps_text(steps: Any) -> str:
    """Flatten a ManagedTestCase.steps JSON list into one normalised string.

    ``steps`` is ``[{step_number, action, expected_result}, ...]``. We keep the
    action + expected_result of each step in order (order matters for the
    sequence ratio in Tier 1, and for a stable fingerprint).
    """
    if not steps or not isinstance(steps, list):
        return ""
    parts: list[str] = []
    for step in steps:
        if isinstance(step, dict):
            action = step.get("action") or step.get("description") or ""
            expected = step.get("expected_result") or step.get("expected") or ""
            parts.append(f"{action} {expected}".strip())
        elif isinstance(step, str):
            parts.append(step)
    return _normalise_text(" || ".join(p for p in parts if p))


def compute_dup_fingerprint(case: ManagedTestCase) -> str:
    """Tier-0 content hash: sha256(normalised(title|objective|steps|expected))[:32].

    Deterministic and project-agnostic (equality is only ever evaluated WITHIN a
    project by the detector). Returns a 32-char hex string.
    """
    payload = "|".join(
        (
            _normalise_text(case.title),
            _normalise_text(case.objective),
            _steps_text(case.steps),
            _normalise_text(case.expected_result),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# ── Tier 1 — structural scoring (pure, STDLIB) ───────────────────────────────

def _tokens(text: str) -> set[str]:
    """Token SET of a normalised string (>= _MIN_TOKEN_LEN chars)."""
    return {t for t in _TOKEN_RE.findall(text) if len(t) >= _MIN_TOKEN_LEN}


def _seq_ratio(a: str, b: str) -> float:
    """difflib.SequenceMatcher ratio (order-sensitive) over two strings."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _jaccard(a: set[str], b: set[str]) -> float:
    """Pure-Python token-set / Jaccard overlap ratio."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _token_list(text: str) -> list[str]:
    """Ordered token LIST of a normalised string (>= _MIN_TOKEN_LEN chars)."""
    return [t for t in _TOKEN_RE.findall(text) if len(t) >= _MIN_TOKEN_LEN]


def _token_seq_ratio(a: str, b: str) -> float:
    """Order-sensitive SequenceMatcher over the TOKEN lists (not raw chars).

    Running the matcher over whole tokens treats ``valid``/``invalid`` and
    ``200``/``404`` as distinct atomic units instead of letting their spurious
    character overlap inflate the ratio.
    """
    ta, tb = _token_list(a), _token_list(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return SequenceMatcher(None, ta, tb).ratio()


def _title_component(a: "_CaseView", b: "_CaseView") -> float:
    """Word-aware title similarity.

    A blend of token-set overlap (order-insensitive), an order-sensitive ratio
    over the token LISTS, and a char-level ratio. The token signals dominate so
    one discriminating WORD (valid/invalid, login/logout, 200/404) materially
    lowers the score; the char ratio is kept at a low weight purely to recover
    morphological paraphrases (``login``/``log in``, ``credential``/
    ``credentials``) that token equality would otherwise miss. The
    discriminating-token band cap (``_discriminating_tokens`` →
    ``_band_for_score``) — not this metric alone — is what blocks the
    opposite-sense false positives.
    """
    jacc = _jaccard(a.title_tokens, b.title_tokens)
    ordered = _token_seq_ratio(a.norm_title, b.norm_title)
    char = _seq_ratio(a.norm_title, b.norm_title)
    return 0.30 * jacc + 0.25 * ordered + 0.45 * char


_NEGATION_PREFIXES = ("in", "un", "im", "ir", "il", "non", "dis", "de")
_NEGATION_WORDS = {"not", "no", "never", "without", "cannot", "fail", "fails", "failure"}


def _discriminating_tokens(a: "_CaseView", b: "_CaseView") -> set[str]:
    """Salient tokens that distinguish the two cases and likely flip meaning.

    Returns the subset of the symmetric token difference that carries an
    antonym/negation/numeric signal — e.g. ``invalid`` vs ``valid`` (prefix
    flip), ``404`` vs ``200`` (differing numerics), ``logout`` vs ``login``,
    explicit negation words. Used both to CAP a pair's band at ``possible`` and
    to surface the key difference in the human-readable reason.
    """
    sym = a.title_tokens ^ b.title_tokens
    if not sym:
        return set()
    a_only = a.title_tokens - b.title_tokens
    b_only = b.title_tokens - a.title_tokens
    marked: set[str] = set()

    # Purely-numeric tokens that differ (200 vs 404) are discriminating.
    for tok in sym:
        if tok.isdigit():
            marked.add(tok)

    # Explicit negation/antonym words.
    for tok in sym:
        if tok in _NEGATION_WORDS:
            marked.add(tok)

    # Prefix-flip antonyms: a token in one side is another side's token with a
    # leading negation prefix (valid/invalid, login/logout via "out"/"in" stem,
    # locked/unlocked). Check both directions.
    def _stem_flip(longer: str, shorter: str) -> bool:
        return any(longer == p + shorter for p in _NEGATION_PREFIXES)

    for x in a_only:
        for y in b_only:
            if _stem_flip(x, y) or _stem_flip(y, x):
                marked.add(x)
                marked.add(y)
            # login/logout style: shared stem, differing suffix in/out.
            elif (
                len(x) >= 4 and len(y) >= 4
                and x[:3] == y[:3]
                and {x[3:5], y[3:5]} & {"in", "ou"}
                and x[3:] != y[3:]
            ):
                marked.add(x)
                marked.add(y)
    return marked


@dataclass
class _CaseView:
    """Pre-computed view of a ManagedTestCase used during scoring/blocking."""
    id: uuid.UUID
    title: str
    suite_name: Optional[str]
    tags: list[str]
    status: Optional[str]
    fingerprint: str
    norm_title: str
    norm_text: str            # BODY only (objective + expected + description)
    steps_text: str           # ordered step text
    step_set: frozenset[str]   # order-insensitive set of normalised steps
    title_tokens: set[str]
    text_tokens: set[str]
    has_body: bool


def _step_set(steps: Any) -> frozenset[str]:
    """Order-insensitive SET of normalised per-step strings.

    Lets reordered-but-identical step lists be recognised (the order-sensitive
    ``steps_text`` ratio alone false-negatives on them).
    """
    if not steps or not isinstance(steps, list):
        return frozenset()
    out: set[str] = set()
    for step in steps:
        if isinstance(step, dict):
            action = step.get("action") or step.get("description") or ""
            expected = step.get("expected_result") or step.get("expected") or ""
            norm = _normalise_text(f"{action} {expected}")
        elif isinstance(step, str):
            norm = _normalise_text(step)
        else:
            norm = ""
        if norm:
            out.add(norm)
    return frozenset(out)


def _make_view(case: ManagedTestCase) -> _CaseView:
    norm_title = _normalise_text(case.title)
    # BODY only — exclude the title so the ``title`` and ``text`` components
    # measure INDEPENDENT signals (otherwise a misleading title similarity is
    # double-counted; finding: text double-counts the title).
    norm_text = _normalise_text(
        " ".join(
            x for x in (case.objective, case.expected_result, case.description) if x
        )
    )
    steps_text = _steps_text(case.steps)
    tags = [str(t) for t in (case.tags or []) if t]
    return _CaseView(
        id=case.id,
        title=case.title or "",
        suite_name=case.suite_name,
        tags=tags,
        status=case.status,
        fingerprint=case.dup_fingerprint or compute_dup_fingerprint(case),
        norm_title=norm_title,
        norm_text=norm_text,
        steps_text=steps_text,
        step_set=_step_set(case.steps),
        title_tokens=_tokens(norm_title),
        text_tokens=_tokens(norm_text),
        has_body=bool(norm_text),
    )


def _structural_score(a: _CaseView, b: _CaseView) -> tuple[float, dict[str, Any], str]:
    """Weighted structural similarity of two cases.

    Components (all WORD-aware where short text makes char ratios misleading):
      * ``title``  — blend of title token-set Jaccard + order-sensitive token
        ratio (``_title_component``). ``valid`` and ``invalid`` are distinct
        tokens, so opposite-sense titles score apart instead of near-1.0.
      * ``text``   — BODY-only (objective/expected/description, title excluded
        to avoid double-counting). A balanced blend of token-set overlap,
        Jaccard, and a char ratio so a LOW token overlap pulls the score DOWN
        (the old ``max(seq, jaccard)`` could only ever raise it → FP amplifier).
      * ``steps``  — MAX of an order-sensitive ratio and an order-INSENSITIVE
        per-step set overlap, so identical-but-reordered steps score ~1.0 while
        genuinely different sequences still score low.

    Weights: 0.4*title + 0.2*text + 0.4*steps, renormalised onto title/text when
    neither case has steps. Returns ``(score, component_scores, reason)``.
    """
    title = _title_component(a, b)

    # BODY-only text: balanced blend (token_set_ratio + jaccard + char ratio).
    text_seq = _seq_ratio(a.norm_text, b.norm_text)
    jacc = _jaccard(a.text_tokens, b.text_tokens)
    token_set_ratio = _token_seq_ratio(
        " ".join(sorted(a.text_tokens)), " ".join(sorted(b.text_tokens))
    )
    if a.has_body or b.has_body:
        text = 0.5 * token_set_ratio + 0.3 * jacc + 0.2 * text_seq
    else:
        # No body on either side → the text signal is absent. Mirror the title
        # so the no-steps blend collapses to title-only evidence (weak, banded
        # accordingly) rather than injecting a spurious 1.0 "both empty" match.
        text = title

    has_steps = bool(a.steps_text) or bool(b.steps_text)
    if has_steps:
        steps_seq = _seq_ratio(a.steps_text, b.steps_text)
        steps_set = _jaccard(set(a.step_set), set(b.step_set))
        # max(): recover the true positive the order-sensitive metric missed on
        # reordered-but-identical steps. Correct combiner here (unlike text).
        steps = max(steps_seq, steps_set)
    else:
        steps = 0.0

    if has_steps:
        score = 0.4 * title + 0.2 * text + 0.4 * steps
    else:
        # Renormalise the 0.4 step weight away → 0.667*title + 0.333*text.
        score = (0.4 * title + 0.2 * text) / 0.6

    disc = _discriminating_tokens(a, b)

    components: dict[str, Any] = {
        "title": round(title, 4),
        "text": round(text, 4),
        "jaccard": round(jacc, 4),
        "steps": round(steps, 4) if has_steps else None,
    }
    if disc:
        components["discriminating_tokens"] = sorted(disc)

    reason = _build_reason(a, b, title, text, steps, has_steps, disc)
    return score, components, reason


def _build_reason(
    a: _CaseView,
    b: _CaseView,
    title: float,
    text: float,
    steps: float,
    has_steps: bool,
    disc: set[str],
) -> str:
    """Human-readable, actionable reason: what the pair SHARES and how it DIFFERS.

    Keeps the numeric component scores (for the chips) but leads with the salient
    overlapping/differing tokens so a reviewer can make the keep/merge/dismiss
    call without re-reading both cases.
    """
    bits: list[str] = []
    shared = a.title_tokens & b.title_tokens
    if shared:
        bits.append("titles share {" + ", ".join(sorted(shared)[:5]) + "}")
    if disc:
        bits.append("differ on {" + ", ".join(sorted(disc)[:4]) + "}")
    if has_steps and a.step_set and b.step_set:
        common = len(a.step_set & b.step_set)
        total = max(len(a.step_set), len(b.step_set))
        if total:
            bits.append(f"{common}/{total} steps overlap")

    # Always append the 1-2 strongest numeric drivers for transparency.
    drivers: list[tuple[str, float]] = [("title", title), ("text", text)]
    if has_steps:
        drivers.append(("steps", steps))
    drivers.sort(key=lambda kv: kv[1], reverse=True)
    nums = ", ".join(f"{name} {val:.2f}" for name, val in drivers[:2])
    bits.append(nums)
    return "; ".join(bits)


def _band_for_score(
    score: float, title_jaccard: float = 1.0, discriminating: bool = False
) -> Optional[str]:
    """Assign a band, with two guards beyond the raw score threshold:

    * a pair with a discriminating token (antonym/negation/differing number) is
      capped at ``possible`` — opposite-sense tests are never ``strong`` dups;
    * a pair below ``STRONG_TITLE_JACCARD_FLOOR`` on the title token-set is
      capped at ``possible`` even if the aggregate clears ``STRONG_THRESHOLD``.
    """
    if score >= STRONG_THRESHOLD:
        if discriminating or title_jaccard < STRONG_TITLE_JACCARD_FLOOR:
            return "possible" if score >= POSSIBLE_THRESHOLD else None
        return "strong"
    if score >= POSSIBLE_THRESHOLD:
        return "possible"
    return None


# ── Blocking ─────────────────────────────────────────────────────────────────

@dataclass
class _BlockingResult:
    pairs: set[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=set)
    truncated_blocks: int = 0
    budget_exhausted: bool = False


def _rare_title_tokens(views: list[_CaseView]) -> dict[uuid.UUID, set[str]]:
    """For each case, the subset of its title tokens that are *rare* across the
    project — common tokens would bucket everything into one giant block.
    """
    n = len(views)
    df: dict[str, int] = defaultdict(int)
    for v in views:
        for tok in v.title_tokens:
            df[tok] += 1
    max_df = max(1, min(_RARE_TOKEN_ABS_CAP, int(n * _RARE_TOKEN_MAX_DF)))
    rare: dict[uuid.UUID, set[str]] = {}
    for v in views:
        rare[v.id] = {tok for tok in v.title_tokens if df[tok] <= max_df}
    return rare


def _generate_candidate_pairs(views: list[_CaseView]) -> _BlockingResult:
    """Blocking: emit only pairs that share a suite, a tag, or a rare title token.

    Each block (bucket) is capped at ``_MAX_BLOCK_SIZE`` and the total number of
    emitted pairs is capped at ``_MAX_PAIR_BUDGET``. Both caps log a WARNING and
    flag the result so the caller can mark the run ``sampled``.
    """
    result = _BlockingResult()
    by_id = {v.id: v for v in views}

    buckets: dict[str, list[uuid.UUID]] = defaultdict(list)

    # Suite blocks
    for v in views:
        if v.suite_name:
            buckets[f"suite::{_normalise_text(v.suite_name)}"].append(v.id)
    # Tag blocks
    for v in views:
        for tag in v.tags:
            buckets[f"tag::{_normalise_text(tag)}"].append(v.id)
    # Rare-title-token blocks
    rare = _rare_title_tokens(views)
    for v in views:
        for tok in rare[v.id]:
            buckets[f"tok::{tok}"].append(v.id)

    for key, ids in buckets.items():
        if len(ids) < 2:
            continue
        if len(ids) > _MAX_BLOCK_SIZE:
            result.truncated_blocks += 1
            logger.warning(
                "duplicate_detection_block_truncated",
                block_key=key,
                block_size=len(ids),
                cap=_MAX_BLOCK_SIZE,
            )
            ids = ids[:_MAX_BLOCK_SIZE]
        # All unordered pairs within the bucket, canonically ordered.
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                if a == b:
                    continue
                pair = (a, b) if a < b else (b, a)
                if pair in result.pairs:
                    continue
                if len(result.pairs) >= _MAX_PAIR_BUDGET:
                    result.budget_exhausted = True
                    logger.warning(
                        "duplicate_detection_pair_budget_exhausted",
                        budget=_MAX_PAIR_BUDGET,
                    )
                    return result
                # Guard against ids that vanished (shouldn't happen — defensive).
                if pair[0] in by_id and pair[1] in by_id:
                    result.pairs.add(pair)

    return result


# ── Tier 2 — semantic (optional, gated, LOCAL embedder only) ─────────────────

async def _semantic_neighbours(
    views: list[_CaseView],
) -> dict[tuple[uuid.UUID, uuid.UUID], float]:
    """Best-effort per-project ChromaDB nearest-neighbour over case text.

    Returns ``{(a_id, b_id): similarity}`` for pairs whose embedding distance is
    under ``_SEMANTIC_MAX_DISTANCE`` (canonical order). ANY failure (Chroma not
    installed / unreachable, embedder missing) returns ``{}`` so the run is
    structural-only — never raises, never reaches a cloud API.

    Gate: uses ChromaDB's default local ONNX MiniLM (no ``embedding_function``
    passed), so there is no cloud *inference* here. That is NOT the same as no
    egress: the model is not bundled, and ChromaDB fetches 79 MB from AWS S3 on
    first use — which was observed happening with ``AI_OFFLINE_MODE=true``.
    ``AI_OFFLINE_MODE`` now covers weight acquisition too, enforced at
    ChromaDB's download chokepoint (``services/local_embedder_guard.py``); when
    it blocks, the ``except`` below returns ``{}`` and the run is
    structural-only, which is the intended degradation.
    """
    if not views:
        return {}
    try:
        # Per-project, ephemeral in-memory collection so we never pollute the
        # shared search index and tenant isolation is total by construction.
        def _build_and_query() -> dict[tuple[uuid.UUID, uuid.UUID], float]:
            import chromadb

            client = chromadb.EphemeralClient()
            coll = client.create_collection(
                name=f"dupdetect_{uuid.uuid4().hex}",
            )
            docs = [v.norm_text or v.norm_title or str(v.id) for v in views]
            ids = [str(v.id) for v in views]
            coll.add(documents=docs, ids=ids)

            found: dict[tuple[uuid.UUID, uuid.UUID], float] = {}
            k = min(_SEMANTIC_TOP_K + 1, len(views))
            res = coll.query(query_texts=docs, n_results=k)
            res_ids = res.get("ids") or []
            res_dist = res.get("distances") or []
            for src_idx, (nbr_ids, nbr_dists) in enumerate(zip(res_ids, res_dist)):
                src = views[src_idx].id
                for nbr_id_str, dist in zip(nbr_ids, nbr_dists):
                    if nbr_id_str == str(src):
                        continue
                    if dist > _SEMANTIC_MAX_DISTANCE:
                        continue
                    nbr = uuid.UUID(nbr_id_str)
                    pair = (src, nbr) if src < nbr else (nbr, src)
                    sim = max(0.0, 1.0 - float(dist))
                    # Keep the strongest similarity seen for the pair.
                    if pair not in found or sim > found[pair]:
                        found[pair] = sim
            return found

        return await asyncio.to_thread(_build_and_query)
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.info(
            "duplicate_detection_semantic_skipped",
            reason=str(exc),
            offline_mode=settings.AI_OFFLINE_MODE,
        )
        return {}


# ── Loading + suppression ────────────────────────────────────────────────────

async def _load_cases(db: AsyncSession, project_id: uuid.UUID) -> tuple[list[ManagedTestCase], bool]:
    """Load authored cases for a project (excluding deprecated).

    Returns ``(cases, sampled)``. Above ``_MAX_CASES_PER_PROJECT`` we load the
    oldest N (deterministic) and flag ``sampled``.
    """
    q = (
        select(ManagedTestCase)
        .where(
            ManagedTestCase.project_id == project_id,
            ManagedTestCase.status != "deprecated",
        )
        .order_by(ManagedTestCase.created_at.asc())
        .limit(_MAX_CASES_PER_PROJECT + 1)
    )
    rows = (await db.execute(q)).scalars().all()
    sampled = len(rows) > _MAX_CASES_PER_PROJECT
    if sampled:
        logger.warning(
            "duplicate_detection_project_sampled",
            project_id=str(project_id),
            cap=_MAX_CASES_PER_PROJECT,
        )
        rows = rows[:_MAX_CASES_PER_PROJECT]
    return list(rows), sampled


async def _load_dismissed(
    db: AsyncSession, project_id: uuid.UUID
) -> set[tuple[uuid.UUID, uuid.UUID]]:
    """Canonical-ordered set of dismissed pairs for the project (suppression)."""
    rows = (
        await db.execute(
            select(
                DismissedDuplicatePair.case_a_id,
                DismissedDuplicatePair.case_b_id,
            ).where(DismissedDuplicatePair.project_id == project_id)
        )
    ).all()
    return {(a, b) if a < b else (b, a) for a, b in rows}


async def _load_existing_candidates(
    db: AsyncSession, project_id: uuid.UUID
) -> dict[tuple[uuid.UUID, uuid.UUID], DuplicateTestCaseCandidate]:
    rows = (
        await db.execute(
            select(DuplicateTestCaseCandidate).where(
                DuplicateTestCaseCandidate.project_id == project_id
            )
        )
    ).scalars().all()
    return {(c.case_a_id, c.case_b_id): c for c in rows}


# ── Public API ───────────────────────────────────────────────────────────────

async def detect_duplicates_for_project(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    enable_semantic: bool = True,
) -> dict[str, Any]:
    """Run tiered duplicate detection for ONE project. STAGE-ONLY (no commit).

    Loads authored cases, lazily backfills ``dup_fingerprint``, blocks to
    candidate pairs, scores them across Tiers 0-2, and upserts
    ``DuplicateTestCaseCandidate`` rows (canonical order, idempotent on
    ``(project_id, case_a_id, case_b_id)``, skipping dismissed pairs). The caller
    owns the commit.

    Returns a summary dict matching ``DuplicateDetectionRunResponse``::

        {project_id, candidates_created, candidates_total, cases_scanned,
         sampled, note}
    """
    cases, sampled = await _load_cases(db, project_id)
    cases_scanned = len(cases)

    if cases_scanned < 2:
        return {
            "project_id": project_id,
            "candidates_created": 0,
            "candidates_total": 0,
            "cases_scanned": cases_scanned,
            "sampled": sampled,
            "note": "fewer than 2 authored cases — nothing to compare" if cases_scanned < 2 else None,
        }

    # Tier 0 — lazily compute + persist (stage) the fingerprint for any case
    # that doesn't have one yet. This is the only mutation to ManagedTestCase
    # and is idempotent.
    for case in cases:
        fp = compute_dup_fingerprint(case)
        if case.dup_fingerprint != fp:
            case.dup_fingerprint = fp  # staged; caller commits

    views = [_make_view(c) for c in cases]
    by_id = {v.id: v for v in views}

    dismissed = await _load_dismissed(db, project_id)
    existing = await _load_existing_candidates(db, project_id)

    # Accumulate the best finding per canonical pair.
    # findings[pair] = (band, score, reason, method, component_scores)
    findings: dict[tuple[uuid.UUID, uuid.UUID], tuple[str, float, str, str, dict]] = {}

    # ── Tier 0: exact fingerprint matches (within project) ──
    # Skip the DEGENERATE empty/whitespace-only payload: every case whose
    # title+objective+steps+expected all normalise to nothing hashes to the
    # SAME constant fingerprint, so a bad bulk-import of N empty drafts would
    # otherwise collapse into one bucket and stage N*(N-1)/2 candidate rows with
    # no budget cap (the one O(n^2) path that escaped blocking). Two truly-empty
    # cases are not meaningfully duplicates. We also bound the Tier-0 loop with
    # the same global pair budget as Tier-1.
    fp_buckets: dict[str, list[uuid.UUID]] = defaultdict(list)
    for v in views:
        # ``has_body``/title/steps all empty ⇒ degenerate fingerprint — skip.
        if v.fingerprint and (v.norm_title or v.norm_text or v.steps_text):
            fp_buckets[v.fingerprint].append(v.id)
    fp_budget_exhausted = False
    for fp, ids in fp_buckets.items():
        if fp_budget_exhausted:
            break
        if len(ids) < 2:
            continue
        for i in range(len(ids)):
            if fp_budget_exhausted:
                break
            for j in range(i + 1, len(ids)):
                if len(findings) >= _MAX_PAIR_BUDGET:
                    fp_budget_exhausted = True
                    logger.warning(
                        "duplicate_detection_fingerprint_budget_exhausted",
                        budget=_MAX_PAIR_BUDGET,
                        project_id=str(project_id),
                    )
                    break
                a, b = ids[i], ids[j]
                pair = (a, b) if a < b else (b, a)
                findings[pair] = (
                    "exact",
                    1.0,
                    "identical normalized content",
                    "fingerprint",
                    {"fingerprint": fp},
                )

    # ── Tier 1: structural over blocked pairs ──
    blocking = _generate_candidate_pairs(views)
    for pair in blocking.pairs:
        if pair in findings:  # already exact
            continue
        a, b = by_id[pair[0]], by_id[pair[1]]
        score, components, reason = _structural_score(a, b)
        title_jacc = _jaccard(a.title_tokens, b.title_tokens)
        has_disc = bool(components.get("discriminating_tokens"))
        band = _band_for_score(score, title_jaccard=title_jacc, discriminating=has_disc)
        if band is None:
            continue
        findings[pair] = (
            band,
            round(score, 4),
            reason,
            "structural",
            components,
        )

    # ── Tier 2: semantic (optional, gated, best-effort) ──
    # Bound the semantic tier the same way as Tier-1: it only embeds the cases
    # that already participate in a block (appear in ``blocking.pairs``), and is
    # skipped entirely above ``_MAX_SEMANTIC_CASES`` (the embed/index/query cost
    # is O(n) and otherwise unbounded by the pair budget). This also stops
    # semantic from minting brand-new pairs that never shared a blocking key,
    # keeping the "never compare all pairs" guarantee intact.
    semantic_used = False
    semantic_skipped = False
    if enable_semantic:
        blocked_ids: set[uuid.UUID] = set()
        for pa, pb in blocking.pairs:
            blocked_ids.add(pa)
            blocked_ids.add(pb)
        semantic_views = [v for v in views if v.id in blocked_ids]
        if len(semantic_views) > _MAX_SEMANTIC_CASES:
            semantic_skipped = True
            logger.warning(
                "duplicate_detection_semantic_skipped_cap",
                project_id=str(project_id),
                blocked_cases=len(semantic_views),
                cap=_MAX_SEMANTIC_CASES,
            )
            semantic_views = []
        neighbours = await _semantic_neighbours(semantic_views) if semantic_views else {}
        for pair, sim in neighbours.items():
            semantic_used = semantic_used or True
            if pair in findings:
                # Fold the semantic signal into an existing structural finding's
                # explainability without downgrading its band/method.
                band, score, reason, method, comp = findings[pair]
                comp = {**(comp or {}), "semantic": round(sim, 4)}
                findings[pair] = (band, score, reason, method, comp)
                continue
            # Semantic-only match — conservative ``possible`` band.
            findings[pair] = (
                "possible",
                round(sim, 4),
                f"semantic similarity {sim:.2f}",
                "semantic",
                {"semantic": round(sim, 4)},
            )

    # ── Upsert candidate rows (skip dismissed) ──
    created = 0
    for pair, (band, score, reason, method, components) in findings.items():
        if pair in dismissed:
            continue
        case_a_id, case_b_id = pair  # already canonical (a < b)
        row = existing.get(pair)
        if row is None:
            row = DuplicateTestCaseCandidate(
                project_id=project_id,
                case_a_id=case_a_id,
                case_b_id=case_b_id,
                band=band,
                score=score,
                reason=reason,
                method=method,
                component_scores=components,
                status="open",
            )
            db.add(row)
            existing[pair] = row
            created += 1
        else:
            # Re-detection refreshes the score/explainability but never
            # resurrects a resolved (merged/dismissed) pair.
            if row.status == "open":
                row.band = band
                row.score = score
                row.reason = reason
                row.method = method
                row.component_scores = components

    await db.flush()

    note: Optional[str] = None
    if (
        sampled
        or blocking.budget_exhausted
        or blocking.truncated_blocks
        or fp_budget_exhausted
        or semantic_skipped
    ):
        bits = []
        if sampled:
            bits.append(f"project capped at {_MAX_CASES_PER_PROJECT} cases")
        if fp_budget_exhausted:
            bits.append(f"fingerprint budget {_MAX_PAIR_BUDGET} exhausted")
        if blocking.budget_exhausted:
            bits.append(f"comparison budget {_MAX_PAIR_BUDGET} exhausted")
        if blocking.truncated_blocks:
            bits.append(f"{blocking.truncated_blocks} oversized block(s) truncated")
        if semantic_skipped:
            bits.append(
                f"semantic tier skipped (>{_MAX_SEMANTIC_CASES} cases)"
            )
        note = "; ".join(bits)

    return {
        "project_id": project_id,
        "candidates_created": created,
        "candidates_total": len(existing),
        "cases_scanned": cases_scanned,
        "sampled": bool(
            sampled or blocking.budget_exhausted or fp_budget_exhausted
        ),
        "note": note,
        "semantic_used": semantic_used,
    }


async def dismiss_candidate(
    db: AsyncSession,
    project_id: uuid.UUID,
    candidate_id: uuid.UUID,
    dismissed_by_user_id: Optional[uuid.UUID],
) -> DuplicateTestCaseCandidate:
    """Dismiss a candidate: flip status to ``dismissed`` + record the suppression.

    STAGE-ONLY. Idempotent — re-dismissing is a no-op on the suppression row.
    Raises ``ValueError`` if the candidate is missing or belongs to another
    project (the router maps this to 404 after its own IDOR check).
    """
    cand = await db.get(DuplicateTestCaseCandidate, candidate_id)
    if cand is None or cand.project_id != project_id:
        raise ValueError("duplicate candidate not found in project")

    cand.status = "dismissed"

    # Record (idempotently) so re-detection never resurfaces this pair.
    exists = (
        await db.execute(
            select(DismissedDuplicatePair.id).where(
                DismissedDuplicatePair.project_id == project_id,
                DismissedDuplicatePair.case_a_id == cand.case_a_id,
                DismissedDuplicatePair.case_b_id == cand.case_b_id,
            )
        )
    ).scalar_one_or_none()
    if exists is None:
        db.add(
            DismissedDuplicatePair(
                project_id=project_id,
                case_a_id=cand.case_a_id,
                case_b_id=cand.case_b_id,
                dismissed_by_user_id=dismissed_by_user_id,
            )
        )
    await db.flush()
    return cand


async def merge_candidate(
    db: AsyncSession,
    project_id: uuid.UUID,
    candidate_id: uuid.UUID,
    keep_case_id: uuid.UUID,
    deprecate_loser: bool = True,
) -> tuple[DuplicateTestCaseCandidate, Optional[uuid.UUID]]:
    """NON-DESTRUCTIVE merge: flip status to ``merged``; optionally soft-deprecate
    the losing case. NEVER deletes a case or redirects a fingerprint this phase.

    STAGE-ONLY. Returns ``(candidate, deprecated_case_id_or_None)``.
    Raises ``ValueError`` (router → 404/400) on a missing candidate, a
    cross-project candidate, or a ``keep_case_id`` not part of the pair.
    """
    cand = await db.get(DuplicateTestCaseCandidate, candidate_id)
    if cand is None or cand.project_id != project_id:
        raise ValueError("duplicate candidate not found in project")
    if keep_case_id not in (cand.case_a_id, cand.case_b_id):
        raise ValueError("keep_case_id must be one of the candidate's two cases")

    cand.status = "merged"

    deprecated_id: Optional[uuid.UUID] = None
    if deprecate_loser:
        loser_id = cand.case_b_id if keep_case_id == cand.case_a_id else cand.case_a_id
        loser = await db.get(ManagedTestCase, loser_id)
        if loser is not None and loser.status != "deprecated":
            loser.status = "deprecated"
            loser.is_stale = True
            loser.stale_reason = (
                f"soft-deprecated as a duplicate of {keep_case_id} "
                f"(candidate {candidate_id})"
            )
            deprecated_id = loser_id
    await db.flush()
    return cand, deprecated_id
