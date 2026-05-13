"""
Conversation Agent — Chat interface for test analysis.

Context engineering improvements over v1:
  1. Intent classification — pattern matching routes queries to targeted data sources
  2. Query-aware retrieval — each intent fetches different data (depth, filters, tables)
  3. Grounded system prompt — includes current datetime, project name, intent label
  4. Conversation memory compression — long sessions summarised to preserve context window
  5. Bug fix — user message saved AFTER history load (eliminates duplicate in LLM context)
  6. Non-blocking ChromaDB — sync client wrapped in asyncio.to_thread
  7. Concurrent context fetch — independent sources fetched with asyncio.gather
  8. Source priority matches intent — relevant sources float to top of the list
"""
import asyncio
import hashlib
import structlog
import re
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy import func, select

from app.core.config import settings
from app.db.mongo import Collections, get_mongo_db
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import (
    AIAnalysis,
    ChatMessage,
    ChatSession,
    Defect,
    TestCase,
    TestCaseHistory,
    TestRun,
)
from app.services.llm_factory import get_llm
from app.services.redaction_service import redact_text

logger = structlog.get_logger("agents.conversation")

# Max content length for stored messages (prevents unbounded growth)
_MAX_MESSAGE_LENGTH = 50_000
# Debounce: skip compression if it ran within this many seconds
_COMPRESS_DEBOUNCE_SECONDS = 300
# P2-8: In-memory TTL cache for _fetch_run_context() — avoids re-fetching
# the same run history multiple times within a chat session.
_RUN_CONTEXT_CACHE: dict[str, tuple[float, str, list[dict]]] = {}
_RUN_CONTEXT_TTL_SECONDS = 60
# P2-8: Hash of last compressed message list per session — skip if unchanged
_LAST_COMPRESSION_HASH: dict[str, str] = {}

# ── Intent classification ─────────────────────────────────────────────────────

class QueryIntent(str, Enum):
    TREND       = "trend"
    FAILURE     = "failure"
    FLAKINESS   = "flakiness"
    COMPARISON  = "comparison"
    SUMMARY     = "summary"
    TRIAGE      = "triage"
    PERFORMANCE = "performance"
    GENERAL     = "general"


_INTENT_PATTERNS: dict[QueryIntent, list[str]] = {
    QueryIntent.TREND: [
        r"trend", r"over time", r"history", r"last \d+ day", r"last \d+ week",
        r"getting (worse|better)", r"improv", r"pattern over", r"across.*run",
        r"week(ly)?", r"daily", r"month",
    ],
    QueryIntent.FAILURE: [
        r"fail", r"broke?n?", r"error", r"exception", r"crash",
        r"why did", r"what (went|is) wrong", r"root cause", r"stack trace",
        r"cause of", r"reason.*fail",
    ],
    QueryIntent.FLAKINESS: [
        r"flak", r"intermittent", r"unstable", r"unreliable", r"inconsistent",
        r"sometimes (pass|fail)", r"non.?determin", r"not reliable",
    ],
    QueryIntent.COMPARISON: [
        r"\bvs\b", r"versus", r"compar", r"difference between",
        r"regression", r"worse than", r"better than", r"between build",
    ],
    QueryIntent.SUMMARY: [
        r"summar", r"overview", r"status", r"how (is|are)", r"current state",
        r"latest", r"recent result", r"what.*happening", r"tell me about",
    ],
    QueryIntent.TRIAGE: [
        r"jira", r"ticket", r"defect", r"bug report", r"assign",
        r"priority", r"created.*ticket", r"open.*issue", r"triage",
    ],
    QueryIntent.PERFORMANCE: [
        r"slow", r"duration", r"timeout", r"performance", r"speed",
        r"how long", r"takes? too long", r"fast",
    ],
}

_INTENT_LABELS: dict[QueryIntent, str] = {
    QueryIntent.TREND:       "Trend Analysis",
    QueryIntent.FAILURE:     "Failure Investigation",
    QueryIntent.FLAKINESS:   "Flakiness Analysis",
    QueryIntent.COMPARISON:  "Build Comparison",
    QueryIntent.SUMMARY:     "Status Summary",
    QueryIntent.TRIAGE:      "Defect Triage",
    QueryIntent.PERFORMANCE: "Performance Analysis",
    QueryIntent.GENERAL:     "General QA Query",
}


def classify_intent(query: str) -> QueryIntent:
    """Classify user query into an intent category using regex pattern matching.
    Fast — no LLM call required. Falls back to GENERAL when ambiguous.
    """
    q = query.lower()
    scores: dict[QueryIntent, int] = {i: 0 for i in QueryIntent}
    for intent, patterns in _INTENT_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, q):
                scores[intent] += 1
    best = max(scores, key=lambda i: scores[i])
    return best if scores[best] > 0 else QueryIntent.GENERAL


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_TEMPLATE = """\
You are TestLookup, an expert assistant for software quality analysis embedded in a CI/CD testing platform.

**Session context:**
- Current date/time (UTC): {now}
- Project scope: {project_scope}
- Query focus: {intent_label}

**Available data:**
You have access to structured test execution data:
- Test run history: build numbers, branches, pass rates, failure counts, timestamps
- AI root-cause analyses: per-test failure categories, confidence scores, recommended actions
- Run summaries: AI-generated executive and detailed markdown reports
- Historical flakiness data: stability rates across recent runs
- Defect triage records: Jira ticket references, resolution status

**Instructions:**
- Ground every answer in the retrieved context below — do not invent metrics or test names
- Quote specific values (build numbers, pass rates, test names) directly from the context
- For trend questions, compute and state actual deltas (e.g., "pass rate dropped from 87% → 71%")
- For comparison questions, use a markdown table
- If the context lacks data for a precise answer, say exactly what is missing
- Keep responses concise and actionable for a QA engineer audience
"""

# ── Memory settings ───────────────────────────────────────────────────────────

_COMPRESS_AFTER = 20   # compress when session exceeds this many user+assistant messages
_HISTORY_TAIL   = 6    # always keep the most recent N messages verbatim


# ── ConversationAgent ─────────────────────────────────────────────────────────

class ConversationAgent:
    """
    Context-engineered conversation agent.
    Sessions are persisted to PostgreSQL (ChatSession + ChatMessage tables).
    """

    async def chat(
        self,
        session_id: str,
        user_message: str,
        user_id: str,
        project_id: Optional[str] = None,
    ) -> dict:
        """Process one user message and return the assistant reply."""

        # 1. Classify intent (fast — no LLM call)
        intent = classify_intent(user_message)
        logger.debug("Session %s intent=%s query=%r", session_id, intent.value, user_message[:80])

        # 2. Load history BEFORE saving user message — prevents duplicate in LLM context
        history, summary_ctx = await self._load_history(session_id)

        # 3. Save user message
        await self._save_message(session_id, "user", user_message, sources=None)

        # 4. Fetch context and project metadata concurrently
        context_coro  = self._retrieve_context(user_message, project_id, intent)
        project_coro  = self._fetch_project_name(project_id)
        (context, sources), project_name = await asyncio.gather(context_coro, project_coro)

        # 5. Build grounded system prompt
        project_scope = (
            project_name if project_name
            else ("all projects" if not project_id else f"project …{project_id[-8:]}")
        )
        system_content = _SYSTEM_TEMPLATE.format(
            now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            project_scope=project_scope,
            intent_label=_INTENT_LABELS.get(intent, "General"),
        )
        if summary_ctx:
            system_content += f"\n\n**Earlier conversation summary:**\n{summary_ctx}"
        system_content += f"\n\n## Retrieved Context\n{context}"

        # 6. Build LangChain message list (history is already deduplicated — no current message)
        messages: list = [SystemMessage(content=system_content)]
        for msg in history:
            cls = HumanMessage if msg["role"] == "user" else AIMessage
            messages.append(cls(content=msg["content"]))
        messages.append(HumanMessage(content=user_message))

        # 7. Invoke LLM with timeout
        try:
            llm = await get_llm()
            response = await asyncio.wait_for(
                llm.ainvoke(messages),
                timeout=settings.AI_TIMEOUT_SECONDS,
            )
            _raw_reply = response.content if hasattr(response, "content") else str(response)
            reply = _raw_reply if isinstance(_raw_reply, str) else str(_raw_reply)
        except asyncio.TimeoutError:
            logger.warning("LLM invocation timed out", timeout=settings.AI_TIMEOUT_SECONDS)
            reply = "The AI provider took too long to respond. Please try again with a simpler query."
            sources = []
        except Exception as exc:
            logger.error("LLM invocation failed", error=str(exc))
            reply = "I'm having trouble connecting to the AI provider. Please try again."
            sources = []

        # 8. Persist assistant reply
        await self._save_message(session_id, "assistant", reply, sources=sources)
        await self._touch_session(session_id)

        # 9. Compress history if session is getting long (fire-and-forget, non-blocking)
        asyncio.create_task(self._maybe_compress_history(session_id))

        return {"reply": reply, "sources": sources}

    # ── Intent-aware context retrieval ───────────────────────────────────────

    async def _retrieve_context(
        self, query: str, project_id: Optional[str], intent: QueryIntent
    ) -> tuple[str, list[dict]]:
        """
        Fetch context targeted to the query intent.
        Each intent path fetches the most relevant data at the right depth.
        """
        parts: list[str] = []
        sources: list[dict] = []

        if intent == QueryIntent.TREND:
            # More runs, tabular format for trend readability
            run_ctx, run_src = await self._fetch_run_context(project_id, limit=15)
            if run_ctx:
                parts.append(f"### Test Run History (last 15 builds)\n{run_ctx}")
                sources.extend(run_src)

        elif intent == QueryIntent.FAILURE:
            # Fewer runs + deep failure analysis, lower confidence threshold to catch more
            run_ctx, run_src = await self._fetch_run_context(project_id, limit=3)
            analysis_ctx, analysis_src = await self._fetch_analysis_context(
                project_id, limit=20, min_confidence=20
            )
            if run_ctx:
                parts.append(f"### Recent Test Runs\n{run_ctx}")
                sources.extend(run_src)
            if analysis_ctx:
                parts.append(f"### Failure Root-Cause Analyses\n{analysis_ctx}")
                sources.extend(analysis_src)

        elif intent == QueryIntent.FLAKINESS:
            # Flakiness-specific data first, supplemented by recent runs
            flaky_ctx, flaky_src = await self._fetch_flaky_context(project_id)
            run_ctx, run_src = await self._fetch_run_context(project_id, limit=5)
            if flaky_ctx:
                parts.append(f"### Flaky Test Analysis\n{flaky_ctx}")
                sources.extend(flaky_src)
            if run_ctx:
                parts.append(f"### Recent Test Runs\n{run_ctx}")

        elif intent == QueryIntent.COMPARISON:
            # More runs for meaningful comparison
            run_ctx, run_src = await self._fetch_run_context(project_id, limit=10)
            if run_ctx:
                parts.append(f"### Test Run History (for comparison)\n{run_ctx}")
                sources.extend(run_src)

        elif intent == QueryIntent.PERFORMANCE:
            # Performance-specific anomalies first
            perf_ctx, perf_src = await self._fetch_perf_context(project_id)
            run_ctx, run_src  = await self._fetch_run_context(project_id, limit=5)
            if perf_ctx:
                parts.append(f"### Performance Anomalies\n{perf_ctx}")
                sources.extend(perf_src)
            if run_ctx:
                parts.append(f"### Recent Test Runs\n{run_ctx}")

        elif intent == QueryIntent.TRIAGE:
            # Open defects + high-confidence analyses
            triage_ctx, triage_src = await self._fetch_triage_context(project_id)
            analysis_ctx, analysis_src = await self._fetch_analysis_context(
                project_id, limit=10, min_confidence=70
            )
            if triage_ctx:
                parts.append(f"### Open Defects / Jira Tickets\n{triage_ctx}")
                sources.extend(triage_src)
            if analysis_ctx:
                parts.append(f"### High-Confidence AI Analyses\n{analysis_ctx}")
                sources.extend(analysis_src)

        else:
            # GENERAL / SUMMARY: balanced fetch, all sources concurrently
            (run_ctx, run_src), (analysis_ctx, analysis_src), summary_str = await asyncio.gather(
                self._fetch_run_context(project_id, limit=5),
                self._fetch_analysis_context(project_id, limit=5, min_confidence=50),
                self._fetch_run_summaries(project_id),
            )
            if run_ctx:
                parts.append(f"### Recent Test Runs\n{run_ctx}")
                sources.extend(run_src)
            if analysis_ctx:
                parts.append(f"### AI Analysis Findings\n{analysis_ctx}")
                sources.extend(analysis_src)
            if summary_str:
                parts.append(f"### Latest Run Summary\n{summary_str}")

        # Semantic search supplements any intent (optional, non-blocking)
        semantic_ctx = await self._semantic_search(query, project_id)
        if semantic_ctx:
            parts.append(f"### Semantically Similar Historical Failures\n{semantic_ctx}")

        if not parts:
            return "No relevant test data found for this project yet.", []

        return "\n\n".join(parts), sources[:8]

    # ── Focused data fetchers ─────────────────────────────────────────────────

    async def _fetch_run_context(
        self, project_id: Optional[str], limit: int = 5
    ) -> tuple[str, list[dict]]:
        """Fetch test run history in a table-friendly format.

        P2-8: Results are cached in-memory with a 60-second TTL to avoid
        redundant DB queries within the same chat session.
        """
        # Check in-memory TTL cache
        cache_key = f"{project_id or 'all'}:{limit}"
        cached = _RUN_CONTEXT_CACHE.get(cache_key)
        if cached:
            ts, cached_text, cached_sources = cached
            if time.monotonic() - ts < _RUN_CONTEXT_TTL_SECONDS:
                return cached_text, cached_sources
            del _RUN_CONTEXT_CACHE[cache_key]

        try:
            async with AsyncSessionLocal() as db:
                q = (
                    select(
                        TestRun.id, TestRun.build_number, TestRun.branch, TestRun.status,
                        TestRun.total_tests, TestRun.failed_tests, TestRun.pass_rate,
                        TestRun.start_time,
                    )
                    .order_by(TestRun.start_time.desc())
                    .limit(limit)
                )
                if project_id:
                    q = q.where(TestRun.project_id == project_id)

                rows = (await db.execute(q)).all()
                if not rows:
                    return "", []

                header = "| Build | Branch | Status | Tests | Failures | Pass Rate | Date |\n|---|---|---|---|---|---|---|"
                lines = []
                src = []
                for r in rows:
                    ts = r.start_time.strftime("%Y-%m-%d %H:%M") if r.start_time else "?"
                    lines.append(
                        f"| {r.build_number} | {r.branch or '?'} | {r.status} "
                        f"| {r.total_tests} | {r.failed_tests} "
                        f"| **{r.pass_rate:.1f}%** | {ts} |"
                    )
                    src.append({"type": "test_run", "id": str(r.id), "build": r.build_number})

                result_text = header + "\n" + "\n".join(lines)
                _RUN_CONTEXT_CACHE[cache_key] = (time.monotonic(), result_text, src)
                return result_text, src
        except Exception as exc:
            logger.debug("Run context fetch error: %s", exc)
            return "", []

    async def _fetch_analysis_context(
        self,
        project_id: Optional[str],
        limit: int = 5,
        min_confidence: int = 50,
    ) -> tuple[str, list[dict]]:
        """Fetch AI analysis results at configurable depth."""
        try:
            async with AsyncSessionLocal() as db:
                q = (
                    select(
                        AIAnalysis.root_cause_summary,
                        AIAnalysis.failure_category,
                        AIAnalysis.confidence_score,
                        AIAnalysis.recommended_actions,
                        AIAnalysis.is_flaky,
                        AIAnalysis.test_case_id,
                        TestCase.test_name,
                        TestCase.suite_name,
                    )
                    .join(TestCase, TestCase.id == AIAnalysis.test_case_id)
                    .where(AIAnalysis.confidence_score >= min_confidence)
                    .order_by(AIAnalysis.confidence_score.desc(), AIAnalysis.created_at.desc())
                    .limit(limit)
                )
                if project_id:
                    q = q.join(TestRun, TestRun.id == TestCase.test_run_id).where(
                        TestRun.project_id == project_id
                    )

                rows = (await db.execute(q)).all()
                if not rows:
                    return "", []

                lines = []
                src = []
                for r in rows:
                    actions = "; ".join((r.recommended_actions or [])[:2])
                    flaky_tag = " *(flaky)*" if r.is_flaky else ""
                    lines.append(
                        f"- **{r.test_name}**{flaky_tag} "
                        f"[{r.failure_category}, {r.confidence_score}% confidence]\n"
                        f"  Root cause: {r.root_cause_summary or 'No summary'}\n"
                        f"  Actions: {actions or 'None suggested'}"
                    )
                    src.append({"type": "ai_analysis", "test_case_id": str(r.test_case_id)})

                return "\n".join(lines), src
        except Exception as exc:
            logger.debug("Analysis context fetch error: %s", exc)
            return "", []

    async def _fetch_flaky_context(
        self, project_id: Optional[str]
    ) -> tuple[str, list[dict]]:
        """Fetch tests identified as flaky by the AI analysis pipeline."""
        try:
            async with AsyncSessionLocal() as db:
                q = (
                    select(TestCase.test_name, TestCase.suite_name, AIAnalysis.root_cause_summary)
                    .join(AIAnalysis, AIAnalysis.test_case_id == TestCase.id)
                    .where(AIAnalysis.is_flaky.is_(True))
                    .order_by(AIAnalysis.created_at.desc())
                    .limit(20)
                )
                if project_id:
                    q = q.join(TestRun, TestRun.id == TestCase.test_run_id).where(
                        TestRun.project_id == project_id
                    )
                rows = (await db.execute(q)).all()

                if not rows:
                    return "No flaky tests identified in recent AI analyses.", []

                lines = [
                    f"- **{r.test_name}** (suite: {r.suite_name or '?'}): "
                    f"{r.root_cause_summary or 'No root cause captured'}"
                    for r in rows
                ]
                src = [{"type": "flaky_test", "test_name": r.test_name} for r in rows]
                return "\n".join(lines), src
        except Exception as exc:
            logger.debug("Flaky context fetch error: %s", exc)
            return "", []

    async def _fetch_perf_context(
        self, project_id: Optional[str]
    ) -> tuple[str, list[dict]]:
        """Fetch tests with duration spikes compared to their 30-day average."""
        try:
            async with AsyncSessionLocal() as db:
                from sqlalchemy.orm import aliased

                current_run = aliased(TestRun)
                history_run = aliased(TestRun)
                q = (
                    select(
                        TestCase.test_name,
                        TestCase.suite_name,
                        TestCase.duration_ms,
                        func.avg(TestCaseHistory.duration_ms).label("avg_duration_ms"),
                    )
                    .join(TestCaseHistory, TestCaseHistory.test_fingerprint == TestCase.test_fingerprint)
                    .join(current_run, current_run.id == TestCase.test_run_id)
                    .join(history_run, history_run.id == TestCaseHistory.test_run_id)
                    .where(TestCase.duration_ms.isnot(None))
                    .group_by(
                        TestCase.id, TestCase.test_name,
                        TestCase.suite_name, TestCase.duration_ms,
                    )
                    .having(TestCase.duration_ms > func.avg(TestCaseHistory.duration_ms) * 1.5)
                    .order_by(
                        (TestCase.duration_ms / func.avg(TestCaseHistory.duration_ms)).desc()
                    )
                    .limit(10)
                )
                if project_id:
                    q = q.where(
                        current_run.project_id == project_id,
                        history_run.project_id == project_id,
                    )

                rows = (await db.execute(q)).all()
                if not rows:
                    return "No significant performance anomalies found.", []

                lines = []
                for r in rows:
                    if r.avg_duration_ms and r.avg_duration_ms > 0:
                        ratio = r.duration_ms / r.avg_duration_ms
                        lines.append(
                            f"- **{r.test_name}**: {r.duration_ms}ms "
                            f"vs avg {r.avg_duration_ms:.0f}ms ({ratio:.1f}× slower)"
                        )
                return "\n".join(lines) if lines else "No anomalies above 1.5× threshold.", []
        except Exception as exc:
            logger.debug("Perf context fetch error: %s", exc)
            return "", []

    async def _fetch_triage_context(
        self, project_id: Optional[str]
    ) -> tuple[str, list[dict]]:
        """Fetch open defects with Jira ticket references."""
        try:
            async with AsyncSessionLocal() as db:
                q = (
                    select(
                        Defect.id, Defect.resolution_status,
                        Defect.jira_ticket_id, Defect.jira_ticket_url,
                        Defect.failure_category, Defect.ai_confidence_score,
                        TestCase.test_name,
                    )
                    .join(TestCase, TestCase.id == Defect.test_case_id)
                    .where(Defect.resolution_status == "OPEN")
                    .order_by(Defect.created_at.desc())
                    .limit(15)
                )
                if project_id:
                    q = q.where(Defect.project_id == project_id)

                rows = (await db.execute(q)).all()
                if not rows:
                    return "No open defects currently tracked.", []

                lines = []
                src = []
                for r in rows:
                    ticket = f"[{r.jira_ticket_id}]" if r.jira_ticket_id else "(no Jira ticket)"
                    conf = f" — {r.ai_confidence_score}% confidence" if r.ai_confidence_score else ""
                    lines.append(
                        f"- **{r.test_name}** {ticket} "
                        f"| {r.failure_category or '?'}{conf}"
                    )
                    src.append({"type": "defect", "id": str(r.id)})
                return "\n".join(lines), src
        except Exception as exc:
            logger.debug("Triage context fetch error: %s", exc)
            return "", []

    async def _fetch_run_summaries(self, project_id: Optional[str]) -> str:
        """Fetch most recent AI-generated run summary from MongoDB."""
        try:
            db = get_mongo_db()
            query: dict = {}
            if project_id:
                query["project_id"] = project_id
            doc = await db[Collections.RUN_SUMMARIES].find_one(
                query, sort=[("generated_at", -1)]
            )
            if doc:
                return str(doc.get("executive_summary", ""))
        except Exception as exc:
            logger.debug("Run summary fetch error: %s", exc)
        return ""

    async def _semantic_search(self, query: str, project_id: Optional[str]) -> str:
        """ChromaDB semantic similarity search — wrapped in asyncio.to_thread to avoid blocking."""
        def _sync_search() -> str:
            try:
                from chromadb import HttpClient
                from app.services.llm_factory import get_embedding_model

                client = HttpClient(host=settings.CHROMA_HOST, port=settings.CHROMA_PORT)
                collection = client.get_or_create_collection(settings.CHROMA_COLLECTION)
                if collection.count() == 0:
                    return ""
                embedder = get_embedding_model()
                vector = embedder.embed_query(query)
                query_kwargs = {"query_embeddings": [vector], "n_results": 3}
                if project_id:
                    query_kwargs["where"] = {"project_id": project_id}
                results = collection.query(**query_kwargs)
                docs = (results.get("documents") or [[]])[0]
                return "\n".join(f"- {d[:300]}" for d in docs) if docs else ""
            except Exception:
                return ""

        return await asyncio.to_thread(_sync_search)

    async def _fetch_project_name(self, project_id: Optional[str]) -> Optional[str]:
        """Fetch project name for system prompt grounding."""
        if not project_id:
            return None
        try:
            from app.models.postgres import Project
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Project.name).where(Project.id == project_id)
                )
                row = result.first()
                return row[0] if row else None
        except Exception:
            return None

    # ── Conversation memory management ───────────────────────────────────────

    async def _load_history(self, session_id: str) -> tuple[list[dict], str]:
        """
        Return (recent_messages, summary_text).

        - recent_messages: last HISTORY_TAIL user/assistant messages (verbatim)
        - summary_text: LLM-generated summary of older messages, empty if not yet compressed
        """
        async with AsyncSessionLocal() as db:
            # Recent messages (tail)
            result = await db.execute(
                select(ChatMessage.role, ChatMessage.content)
                .where(
                    ChatMessage.session_id == session_id,
                    ChatMessage.role.in_(["user", "assistant"]),
                )
                .order_by(ChatMessage.created_at.desc())
                .limit(_HISTORY_TAIL)
            )
            rows = result.all()
            recent = [{"role": r.role, "content": r.content} for r in reversed(rows)]

            # Compressed summary (if exists)
            summary_result = await db.execute(
                select(ChatMessage.content)
                .where(
                    ChatMessage.session_id == session_id,
                    ChatMessage.role == "summary",
                )
                .order_by(ChatMessage.created_at.desc())
                .limit(1)
            )
            summary_row = summary_result.first()
            summary_text = summary_row[0] if summary_row else ""

        return recent, summary_text

    async def _maybe_compress_history(self, session_id: str) -> None:
        """
        When a session exceeds COMPRESS_AFTER messages, summarise all but the last
        HISTORY_TAIL messages and store the summary as a special 'summary' role message.
        This preserves important context (test names, builds, findings) while keeping
        the active context window small for subsequent turns.

        Runs at most once per session (skipped if a summary already exists).
        """
        try:
            async with AsyncSessionLocal() as db:
                # Count user + assistant messages
                count_result = await db.execute(
                    select(func.count(ChatMessage.id)).where(
                        ChatMessage.session_id == session_id,
                        ChatMessage.role.in_(["user", "assistant"]),
                    )
                )
                count = count_result.scalar() or 0
                if count < _COMPRESS_AFTER:
                    return

                # Skip if a summary was recently created (debounce)
                existing = await db.execute(
                    select(ChatMessage.created_at).where(
                        ChatMessage.session_id == session_id,
                        ChatMessage.role == "summary",
                    )
                    .order_by(ChatMessage.created_at.desc())
                    .limit(1)
                )
                existing_row = existing.first()
                if existing_row:
                    age = (datetime.now(timezone.utc) - existing_row[0]).total_seconds()
                    if age < _COMPRESS_DEBOUNCE_SECONDS:
                        return  # Debounce: too recent

                # Fetch all messages except the most recent tail
                all_result = await db.execute(
                    select(ChatMessage.role, ChatMessage.content, ChatMessage.created_at)
                    .where(
                        ChatMessage.session_id == session_id,
                        ChatMessage.role.in_(["user", "assistant"]),
                    )
                    .order_by(ChatMessage.created_at.asc())
                )
                all_msgs = all_result.all()

            # Messages to compress: everything except the tail
            older = all_msgs[:-_HISTORY_TAIL]
            if len(older) < 4:
                return

            transcript = "\n".join(
                f"{m.role.upper()}: {m.content[:600]}" for m in older
            )

            # P2-8: Skip compression if message list is identical to last compression
            content_hash = hashlib.sha256(transcript.encode()).hexdigest()[:16]
            if _LAST_COMPRESSION_HASH.get(session_id) == content_hash:
                return
            _LAST_COMPRESSION_HASH[session_id] = content_hash

            compression_prompt = (
                "Summarise the following QA analysis chat conversation in 4-6 bullet points. "
                "Preserve: specific test names, build numbers, pass rates, failure categories, "
                "key findings, and any decisions or actions discussed. Be concise.\n\n"
                f"{transcript}"
            )

            # Generate summary with timeout (outside DB session to avoid holding connection)
            _COMPRESS_TIMEOUT = min(60, settings.AI_TIMEOUT_SECONDS)
            try:
                llm = await get_llm()
                response = await asyncio.wait_for(
                    llm.ainvoke([HumanMessage(content=compression_prompt)]),
                    timeout=_COMPRESS_TIMEOUT,
                )
                _raw_summary = response.content if hasattr(response, "content") else str(response)
                summary_content = _raw_summary if isinstance(_raw_summary, str) else str(_raw_summary)
            except asyncio.TimeoutError:
                logger.warning(
                    "Compression LLM timed out, using deterministic fallback",
                    session_id=session_id,
                    timeout=_COMPRESS_TIMEOUT,
                )
                # Deterministic fallback: extract key topics from messages
                topics = set()
                for m in older:
                    for word in ("fail", "pass", "build", "flaky", "regression", "bug"):
                        if word in m.content.lower():
                            topics.add(word)
                summary_content = (
                    f"Conversation summary (auto-generated, {len(older)} messages compressed):\n"
                    f"- Topics discussed: {', '.join(sorted(topics)) or 'general QA analysis'}\n"
                    f"- Message count: {len(older)} older messages summarized"
                )

            await self._save_message(session_id, "summary", summary_content, sources=None)
            logger.info(
                "Compressed session history",
                session_id=session_id,
                messages_compressed=len(older),
            )

        except Exception as exc:
            logger.debug("History compression failed (non-critical)", error=str(exc))

    # ── Persistence helpers ───────────────────────────────────────────────────

    async def _save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        sources: Optional[list],
    ) -> None:
        # Enforce max content length to prevent unbounded storage growth
        truncated = False
        safe_content = content or ""
        if len(safe_content) > _MAX_MESSAGE_LENGTH:
            safe_content = safe_content[:_MAX_MESSAGE_LENGTH] + "\n\n[Message truncated — exceeded maximum length]"
            truncated = True

        # Redact secrets from stored content
        safe_content = redact_text(safe_content)

        async with AsyncSessionLocal() as db:
            db.add(ChatMessage(
                session_id=session_id,
                role=role,
                content=safe_content,
                sources=sources,
                created_at=datetime.now(timezone.utc),
            ))
            await db.commit()

        if truncated:
            logger.info("Message truncated", session_id=session_id, role=role, original_length=len(content))

    async def _touch_session(self, session_id: str) -> None:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(ChatSession).where(ChatSession.id == session_id)
            )
            session = result.scalar_one_or_none()
            if session:
                session.updated_at = datetime.now(timezone.utc)
                await db.commit()
