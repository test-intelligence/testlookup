"""
Conversation Agent — the Ask-AI chat.

A turn is a stream of events, produced by a task and read from a queue
(:class:`ChatTurn`). The streaming endpoint forwards them as server-sent
events; the plain JSON endpoint waits for the last one.

    start   {"user_message_id", "retry"}      the question is saved (or re-used on retry)
    status  {"label", "tool"}                 what the agent is doing ("Checking history of …")
    delta   {"text"}                          answer text, as the provider writes it
    done    {"message", "sources", "tool_trace", "suggested_actions", "meta"}
    error   {"code", "message", "retryable"}  the turn failed; no answer was saved

How an answer is grounded:
  1. A snapshot built without the model — the project's last runs with
     reconciled counts and the latest run's failing tests
     (:meth:`ConversationAgent._fetch_run_context`). Enough for the common
     questions, so they need no tool round-trip.
  2. Native tool calling over the read-only, project-scoped tools in
     ``app.tools.chat_read_tools`` for everything else: a test's history, a
     build comparison, flaky tests, quarantine, the release gate, what earlier
     analyses said. Tenancy is a server-side ContextVar; the model never
     supplies an identifier.
  3. The registry prompt ``chat_system`` forbids stating a figure the model
     did not read.

What this replaced (2026-10-09): the AI-6 ReAct copilot handed ``BudgetedLLM``
to ``create_react_agent``, which rejected it ("Expected a Runnable") on every
message, so every answer came from a regex-intent single-shot path. That path
had no per-test history and invented one -- "failed 7 of the last 10 runs" for
a test that failed 1 of 12 -- after a blank wait of 7-10 s, and saved provider
errors as if they were answers. Both paths are gone.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import structlog
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from sqlalchemy import func, select

from app.core.config import settings
from app.db.mongo import Collections, get_mongo_db
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import (
    ChatMessage,
    ChatSession,
    TestCase,
    TestRun,
)
from app.services.llm_factory import BudgetedLLM, get_llm, tools_unsupported_error
from app.services.prompt_registry import get_prompt_text
from app.services.redaction_service import redact_text
from app.services.privacy_service import sanitize_for_llm

logger = structlog.get_logger("agents.conversation")

# Max content length for stored messages (prevents unbounded growth)
_MAX_MESSAGE_LENGTH = 50_000
# Debounce: skip compression if it ran within this many seconds
_COMPRESS_DEBOUNCE_SECONDS = 300
# P2-8: In-memory TTL cache for _fetch_run_context() — the snapshot is rebuilt
# for every turn, and a conversation asks several questions a minute.
_RUN_CONTEXT_CACHE: dict[str, tuple[float, str, list[dict]]] = {}
_RUN_CONTEXT_TTL_SECONDS = 60
# P2-8: Hash of last compressed message list per session — skip if unchanged
_LAST_COMPRESSION_HASH: dict[str, str] = {}

# Runs in the grounding snapshot.
_SNAPSHOT_RUNS = 5
# Earlier messages are cut to this many characters when replayed: the gist is
# enough to resolve "the first one", and a small model given whole earlier
# tables drifts back to the previous topic (measured, mistral-nemo).
_HISTORY_QUESTION_CHARS = 1_000
_HISTORY_ANSWER_CHARS = 1_500
# Tool observations (hosted models): the whole answer, and one call. The chat
# model's context is far larger than its output ceiling (LLM_MAX_TOKENS),
# which is what the tool module's defaults are derived from.
_TOOL_TOTAL_TOKENS = 8_000
_TOOL_CALL_TOKENS = 2_000

# ── Memory settings ───────────────────────────────────────────────────────────

_COMPRESS_AFTER = 20   # compress when session exceeds this many user+assistant messages
_HISTORY_TAIL = 6      # compression keeps the most recent N messages verbatim

# Fire-and-forget work (history compression) is kept referenced until it ends:
# the event loop holds only weak references to tasks.
_BACKGROUND_TASKS: set[asyncio.Task] = set()

Emit = Callable[[str, dict], None]


# ── Errors a user can act on ─────────────────────────────────────────────────


class ChatTurnError(Exception):
    """A turn that cannot produce an answer, said in words a user can act on."""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def as_event(self) -> dict:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


class _ToolsUnsupported(Exception):
    """The provider refused the ``tools`` parameter (a model without tool use)."""


def _status_code(exc: BaseException) -> Optional[int]:
    for candidate in (exc, getattr(exc, "response", None)):
        code = getattr(candidate, "status_code", None)
        if isinstance(code, int):
            return code
    return None


def classify_failure(exc: BaseException) -> ChatTurnError:
    """Map whatever stopped a turn onto the error vocabulary the UI renders."""
    if isinstance(exc, ChatTurnError):
        return exc
    from app.services.llm_circuit_breaker import CircuitBreakerOpen
    from app.services.llm_cluster_semaphore import LLMSlotTimeout
    from app.services.llm_cost_reservation import CostCapExceeded
    from app.services.llm_factory import PipelineBudgetExceeded

    # Before TimeoutError: LLMSlotTimeout subclasses it.
    if isinstance(exc, LLMSlotTimeout):
        return ChatTurnError(
            "busy",
            "The AI provider is busy with other analysis work. Try again in a moment.",
            retryable=True,
        )
    if isinstance(exc, TimeoutError):  # asyncio.TimeoutError is an alias from 3.11
        return ChatTurnError(
            "timeout",
            "The AI provider took too long to answer. Try again, or ask a narrower question.",
            retryable=True,
        )
    if isinstance(exc, CircuitBreakerOpen):
        return ChatTurnError(
            "provider_unavailable",
            "The AI provider has been failing, so requests to it are paused for a short while. "
            "Try again in a minute.",
            retryable=True,
        )
    if isinstance(exc, (CostCapExceeded, PipelineBudgetExceeded)):
        return ChatTurnError(
            "budget_exceeded",
            "This project's AI budget for the month is used up, so chat cannot call the model. "
            "An admin can raise the cap.",
            retryable=False,
        )
    code = _status_code(exc)
    name = type(exc).__name__
    if code in (401, 403):
        return ChatTurnError(
            "provider_auth",
            "The AI provider rejected this deployment's credentials. An admin needs to check "
            "the provider key in Settings > AI Configuration.",
            retryable=False,
        )
    if code == 429:
        return ChatTurnError(
            "rate_limited",
            "The AI provider is rate-limiting requests. Try again in a minute.",
            retryable=True,
        )
    if (code is not None and code >= 500) or "Connect" in name or "Connection" in name:
        return ChatTurnError(
            "provider_unavailable",
            "The AI provider could not be reached. Try again shortly.",
            retryable=True,
        )
    if code is not None and 400 <= code < 500:
        return ChatTurnError(
            "provider_error",
            f"The AI provider refused the request (HTTP {code}). Try rephrasing the question.",
            retryable=True,
        )
    return ChatTurnError(
        "internal", "Something went wrong while answering. Try again.", retryable=True,
    )


# ── Small helpers ─────────────────────────────────────────────────────────────


def _text_of(content: Any) -> str:
    """Text of a message chunk: a string, or the text parts of a list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return ""


def _meta_of(sources: Any) -> dict:
    for entry in sources or []:
        if isinstance(entry, dict) and entry.get("type") == "meta":
            return entry
    return {}


def _is_stopped(sources: Any) -> bool:
    return _meta_of(sources).get("status") == "stopped"


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else datetime.now(timezone.utc).isoformat()


async def _configured_mode() -> str:
    """The analysis mode the sidebar and the page read (``GET /settings/ai/mode``):
    the ``ai_config`` app setting, else ``ANALYSIS_MODE``."""
    mode = str(settings.ANALYSIS_MODE or "auto")
    try:
        from app.models.postgres import AppSetting

        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(select(AppSetting.value).where(AppSetting.key == "ai_config"))
            ).first()
        stored = (row[0] or {}) if row else {}
        if isinstance(stored, dict) and stored.get("analysis_mode"):
            mode = str(stored["analysis_mode"])
    except Exception as exc:  # noqa: BLE001 -- fall back to the environment
        logger.debug("chat_mode_lookup_failed", error=str(exc)[:200])
    return mode.lower()


# Modes in which chat calls a model: the frontend's isLLMAvailable.
_CHAT_MODES = ("llm", "auto")


# Questions whose answer must come from a lookup (see _prefetch_lookups).
_ASKS_ABOUT_QUARANTINE = re.compile(r"quarantin", re.IGNORECASE)
_ASKS_ABOUT_FLAKY = re.compile(r"flak", re.IGNORECASE)
# "Is the first one you mentioned flaky?" is about one earlier test, not the
# flaky list: it gets no up-front list, so the model must look the test up.
_REFERS_BACK = re.compile(
    r"\b(?:the (?:first|second|third|fourth|fifth|last|other|same) (?:one|test|failure)"
    r"|(?:that|this) (?:one|test|failure)|those|these|it)\b",
    re.IGNORECASE,
)
_ASKS_ABOUT_RELEASE = re.compile(
    r"\b(release|releasing|ship|shipping|go/no-go|no-go|deploy|deployable)\b",
    re.IGNORECASE,
)
# "The first one", "the top one", "the last test": a position in the previous
# answer's list. "The first NEW failure" names a sub-list, so it is left to the
# model (measured: it resolves that one correctly).
_ORDINAL_REFERENCE = re.compile(
    r"\bthe (first|top|second|third|fourth|fifth|last) (?:one|test|failure|failing test|flaky test)\b",
    re.IGNORECASE,
)
_ORDINAL_INDEX = {"first": 0, "top": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4, "last": -1}

# Identifier-shaped words in a question: candidates for a test name.
_TEST_NAME_TOKEN = re.compile(r"[A-Za-z_][\w.:\-/#\[\]]{5,}")


_SMALL_TALK = re.compile(
    r"^\W*(hi|hello|hey|thanks|thank you|ok|okay|cool|great|bye|help|"
    r"what can you do|who are you)\W*$",
    re.IGNORECASE,
)


def _wants_data(question: str) -> bool:
    """A question about the project's data, not small talk."""
    return len(question.split()) >= 3 and not _SMALL_TALK.match(question)


def _looks_like_a_test_name(token: str) -> bool:
    """An identifier rather than an English word: has ``_``/``.``/``:``/a digit,
    or a lower-to-upper case change (``testRefundFlow``)."""
    return bool(
        re.search(r"[_.:#\d]", token) or re.search(r"[a-z][A-Z]", token)
    )


def _test_name_candidates(text: str) -> list[str]:
    """Identifier-shaped words in ``text``, in the order they first appear."""
    seen: dict[str, None] = {}
    for token in _TEST_NAME_TOKEN.findall(text or ""):
        word = token.strip(".,:;!?'\"`()[]*")
        if len(word) >= 6 and _looks_like_a_test_name(word):
            seen.setdefault(word, None)
    return list(seen)


def _position(text: str, needle: str) -> int:
    """Where the answer first names ``needle``, or -1."""
    if not needle:
        return -1
    if len(needle) >= 6:
        return text.find(needle)
    # Short build numbers ("105") are common words in an answer: count them
    # only where the answer names them as a build.
    match = re.search(
        rf"(?:build|#)\s*\**\s*{re.escape(needle)}\b", text, flags=re.IGNORECASE,
    )
    return match.start() if match else -1


def _answer_sources(reply: str, candidates: list[dict]) -> list[dict]:
    """The runs and tests the answer names, as linkable chips (deduplicated),
    in the order the answer names them: the snapshot lists the latest run's
    failures by AI confidence, and chips in that order read as the answer's
    "first one" when it is not."""
    chosen: list[tuple[int, dict]] = []
    seen: set[str] = set()
    for ref in candidates:
        label = str(ref.get("build") or ref.get("name") or "")
        key = f"{ref.get('type')}:{ref.get('id')}"
        at = _position(reply, label)
        if key in seen or at < 0:
            continue
        seen.add(key)
        chosen.append((at, ref))
    chosen.sort(key=lambda item: item[0])
    return [ref for _, ref in chosen[:8]]


@dataclass
class _Timings:
    started: float = field(default_factory=time.perf_counter)
    first_status_ms: Optional[int] = None
    first_token_ms: Optional[int] = None
    llm_ms: float = 0.0
    tool_ms: float = 0.0
    context_ms: float = 0.0

    def since_start(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)


@dataclass
class _TurnState:
    """What a turn has shown and whether its answer is being saved."""

    timings: _Timings = field(default_factory=_Timings)
    shown: list[str] = field(default_factory=list)
    # Set once the complete answer starts to be saved: a Stop arriving after
    # that must not save a second, "stopped" copy of it.
    saving: bool = False


# One turn at a time per conversation: two tabs (or a double Retry) would
# otherwise both pass the retry check and write two answers, and hold two of
# the cluster's LLM slots for one person.
_TURN_LOCK_PREFIX = "testlookup:chat:turn:"


async def _acquire_turn_lock(session_id: str) -> Optional[str]:
    """A token when this turn may run; ``None`` when another turn holds the
    conversation. Fails open: without Redis the lock is skipped, not chat."""
    token = uuid.uuid4().hex
    try:
        from app.db import redis_client

        acquired = await redis_client.get_redis().set(
            _TURN_LOCK_PREFIX + str(session_id), token,
            nx=True, ex=int(settings.CHAT_TURN_TIMEOUT_SECONDS) + 30,
        )
    except Exception as exc:  # noqa: BLE001 -- a lock store outage must not stop chat
        logger.warning("chat_turn_lock_unavailable", error=str(exc)[:200])
        return token
    return token if acquired else None


async def _release_turn_lock(session_id: str, token: str) -> None:
    try:
        from app.db import redis_client

        redis = redis_client.get_redis()
        key = _TURN_LOCK_PREFIX + str(session_id)
        held = await redis.get(key)
        if held is not None and (held.decode() if isinstance(held, bytes) else str(held)) == token:
            await redis.delete(key)
    except Exception as exc:  # noqa: BLE001 -- the lock expires on its own
        logger.debug("chat_turn_lock_release_failed", error=str(exc)[:200])


# Tool calls run per round; the rest are dropped (small models sometimes ask
# for the same lookup many times).
_MAX_CALLS_PER_ROUND = 6
# An interactive turn does not queue behind the pipeline for a cluster slot.
_SLOT_TIMEOUT_SECONDS = 15.0
# Providers whose client honours tool_choice="none" (OpenAI wire); the forced
# answer round of any other provider gets the unbound model.
_TOOL_CHOICE_PROVIDERS = frozenset({"openai", "openrouter", "lmstudio", "localai", "vllm"})
# (provider, model) pairs that refused the ``tools`` parameter in this process.
_TOOLS_OFF: set[tuple[str, str]] = set()


def _call_id() -> str:
    # Mistral's API requires tool-call ids of 9 alphanumeric characters.
    return uuid.uuid4().hex[:9]


def _tool_calls_of(message: Any) -> list[dict]:
    """The tool calls of a streamed message, every one with an id.

    Calls whose arguments did not parse (``invalid_tool_calls``) are kept with
    an ``error``: the model is told, instead of the call -- and with it the
    answer -- silently vanishing.
    """
    calls: list[dict] = []
    for call in list(getattr(message, "tool_calls", None) or []):
        calls.append({
            "name": str(call.get("name") or ""),
            "args": call.get("args") or {},
            "id": str(call.get("id") or _call_id()),
        })
    for bad in list(getattr(message, "invalid_tool_calls", None) or []):
        calls.append({
            "name": str(bad.get("name") or ""),
            "args": {},
            "id": str(bad.get("id") or _call_id()),
            "error": str(bad.get("error") or bad.get("args") or "unparseable arguments"),
        })
    return calls


def _tool_budgets(llm: Any) -> tuple[int, int]:
    """(total, per call) token budgets for tool observations.

    A hosted model's context is far larger than anything sent here. Ollama's
    is ``OLLAMA_NUM_CTX`` (8k by default), which must also hold the snapshot,
    the history, the tool schemas and the answer.
    """
    if getattr(llm, "provider_name", None) == "ollama":
        return 2_000, 800
    return _TOOL_TOTAL_TOKENS, _TOOL_CALL_TOKENS


def _prompt_budget_tokens(llm: Any) -> Optional[int]:
    """Tokens the system prompt plus history may use, or ``None`` (no limit)."""
    if getattr(llm, "provider_name", None) != "ollama":
        return None
    context = int(getattr(settings, "OLLAMA_NUM_CTX", 8192) or 8192)
    # Leave room for the answer, the tool schemas (~1.5k) and observations.
    return max(0, context - int(settings.LLM_MAX_TOKENS) - 1_500 - _tool_budgets(llm)[0])


def _fit_history(system: str, history: list[BaseMessage], budget: Optional[int]) -> list[BaseMessage]:
    """Drop the oldest question/answer pairs until the prompt fits ``budget``.

    A prompt larger than an Ollama context is truncated server-side with no
    error (F-5): the model would lose the start of the system prompt, which
    is where its rules are.
    """
    if budget is None:
        return history
    kept = list(history)

    def size() -> int:
        return (len(system) + sum(len(_text_of(m.content)) for m in kept)) // 4

    while kept and size() > budget:
        kept = kept[2:]
    return kept


# ── A running turn ────────────────────────────────────────────────────────────


class ChatTurn:
    """One running turn: a task that puts ``(event, data)`` on a queue.

    The task is created from the caller's context (so it inherits the request's
    ContextVars) but is NOT inside the request's cancel scope: when the reader
    goes away, :meth:`cancel_nowait` stops the model and the task still saves
    what was already shown.
    """

    _END = object()

    def __init__(self, runner: Callable[[Emit], Awaitable[None]]) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task = asyncio.create_task(self._run(runner))

    def _emit(self, event: str, data: dict) -> None:
        self._queue.put_nowait((event, data))

    async def _run(self, runner: Callable[[Emit], Awaitable[None]]) -> None:
        try:
            await runner(self._emit)
        finally:
            self._queue.put_nowait(self._END)

    async def next_event(self, timeout: Optional[float] = None) -> Optional[tuple[str, dict]]:
        """The next event; ``None`` when ``timeout`` passes with nothing new.

        Raises ``StopAsyncIteration`` once the turn has ended.
        """
        try:
            if timeout is None:
                item = await self._queue.get()
            else:
                item = await asyncio.wait_for(self._queue.get(), timeout)
        except asyncio.TimeoutError:
            return None
        if item is self._END:
            raise StopAsyncIteration
        event, data = item
        return str(event), dict(data)

    def cancel_nowait(self) -> None:
        if not self._task.done():
            self._task.cancel()

    async def wait_closed(self) -> None:
        await asyncio.wait({self._task})


# ── ConversationAgent ─────────────────────────────────────────────────────────


class ConversationAgent:
    """The Ask-AI chat. Sessions are persisted to PostgreSQL (ChatSession +
    ChatMessage); every model call goes through ``get_llm()``'s gates."""

    def start_turn(
        self,
        *,
        session_id: str,
        user_id: str,
        project_id: Optional[str],
        user_message: Optional[str] = None,
        retry: bool = False,
        test_run_id: Optional[str] = None,
        report_id: Optional[str] = None,
        report_version: Optional[int] = None,
    ) -> ChatTurn:
        async def runner(emit: Emit) -> None:
            await self._run_turn(
                emit,
                session_id=session_id,
                user_id=user_id,
                project_id=project_id,
                user_message=user_message,
                retry=retry,
                test_run_id=test_run_id,
                report_id=report_id,
                report_version=report_version,
            )

        return ChatTurn(runner)

    async def chat(
        self,
        session_id: str,
        user_message: str,
        user_id: str,
        project_id: Optional[str] = None,
        test_run_id: Optional[str] = None,
        report_id: Optional[str] = None,
        report_version: Optional[int] = None,
    ) -> dict:
        """One whole turn for callers that do not stream (``POST …/messages``).

        Raises :class:`ChatTurnError` when the turn fails; returns the reply,
        its sources, the tool trace and the suggested actions otherwise.
        """
        turn = self.start_turn(
            session_id=session_id,
            user_id=user_id,
            project_id=project_id,
            user_message=user_message,
            test_run_id=test_run_id,
            report_id=report_id,
            report_version=report_version,
        )
        done: Optional[dict] = None
        failure: Optional[dict] = None
        while True:
            try:
                item = await turn.next_event()
            except StopAsyncIteration:
                break
            if item is None:
                continue
            event, data = item
            if event == "done":
                done = data
            elif event == "error":
                failure = data
        if failure is not None or done is None:
            failure = failure or {"code": "internal", "message": "No answer was produced.", "retryable": True}
            raise ChatTurnError(failure["code"], failure["message"], retryable=bool(failure["retryable"]))
        return {
            "reply": done["message"]["content"],
            "sources": done["sources"],
            "tool_trace": done["tool_trace"],
            "suggested_actions": done["suggested_actions"],
            "message": done["message"],
            "meta": done["meta"],
        }

    # ── The turn ─────────────────────────────────────────────────────────────

    async def _run_turn(self, emit: Emit, **params: Any) -> None:
        """Run one turn inside the project's cost scope and the turn deadline.

        Re-audit R-B45-1: every LLM call of a project chat (the answer and the
        history compression it spawns, which copies this context) reserves
        against the project's monthly cap. The scope is entered HERE, inside
        the turn's own task: ContextVars are copied when a task is created.

        The answer is saved after the deadline and shielded: a Stop or a
        timeout that lands during the save must neither lose the answer nor
        save a second, "stopped" copy of it.
        """
        from app.services.llm_cost_reservation import cost_budget_scope

        session_id = params["session_id"]
        state = _TurnState()
        lock = await _acquire_turn_lock(session_id)
        if lock is None:
            emit("error", ChatTurnError(
                "turn_in_progress",
                "An answer is already being written in this conversation. Wait for it, or stop it first.",
                retryable=True,
            ).as_event())
            return
        try:
            with cost_budget_scope(params.get("project_id")):
                async with asyncio.timeout(settings.CHAT_TURN_TIMEOUT_SECONDS):
                    outcome = await self._answer(emit, state, **params)
                state.saving = True
                done = await asyncio.shield(self._finish(session_id, outcome))
            emit("done", done)
        except asyncio.CancelledError:
            # The reader went away (Stop, a closed tab). Keep what they saw,
            # unless the complete answer is already being saved.
            if not state.saving:
                await self._save_stopped(session_id, state)
            raise
        except Exception as exc:  # noqa: BLE001 -- every failure becomes an error event
            failure = classify_failure(exc)
            logger.warning(
                "chat_turn_failed",
                session_id=str(session_id),
                code=failure.code,
                error_type=type(exc).__name__,
                error=str(exc)[:300],
                elapsed_ms=state.timings.since_start(),
            )
            emit("error", failure.as_event())
        finally:
            await _release_turn_lock(session_id, lock)

    async def _answer(
        self,
        emit: Emit,
        state: _TurnState,
        *,
        session_id: str,
        user_id: str,
        project_id: Optional[str],
        user_message: Optional[str],
        retry: bool,
        test_run_id: Optional[str],
        report_id: Optional[str],
        report_version: Optional[int],
    ) -> dict:
        """Produce the answer (streamed through ``emit``); return what to save."""
        timings = state.timings
        if not project_id:
            raise ChatTurnError(
                "project_required",
                "Select a project first: answers come from one project's test data.",
                retryable=False,
            )
        mode = await _configured_mode()
        if mode not in _CHAT_MODES:
            raise ChatTurnError(
                "llm_disabled",
                f"Chat is unavailable in {mode.upper() if mode == 'ml' else mode.capitalize()} mode. "
                "Switch to LLM or Auto mode in Settings > AI Configuration.",
                retryable=False,
            )

        # 1. The question: saved now (a retry re-uses the unanswered one).
        if retry:
            question, question_id = await self._prepare_retry(session_id)
        else:
            question = (user_message or "").strip()
            if not question:
                raise ChatTurnError("empty_question", "Type a question first.", retryable=False)
            question_id = (await self._save_message(session_id, "user", question, sources=None))["id"]
        emit("start", {"user_message_id": str(question_id), "retry": retry})
        emit("status", {"label": "Reading this project's latest runs…", "tool": None})
        timings.first_status_ms = timings.since_start()

        from app.tools.chat_read_tools import (
            build_suggested_actions,
            chat_tools,
            get_chat_tool_state,
            reset_chat_tool_context,
            set_chat_tool_context,
        )

        # A session bound to one immutable DecisionReport answers from that
        # report: the latest-runs snapshot and the up-front lookups would put
        # newer data beside it, which its contract forbids ("do not substitute
        # an unbound or latest report"). The tools stay, and say what they read.
        bound = bool(report_id)

        try:
            # CHAT_LLM_MODEL gives chat its own model (a stronger tool user)
            # without changing the analysis pipeline's; empty = the global one.
            llm = await get_llm(model=settings.CHAT_LLM_MODEL or None)
        except Exception as exc:  # noqa: BLE001 -- configuration, said plainly
            reason = str(exc).strip().split("\n")[0][:240] or type(exc).__name__
            raise ChatTurnError(
                "llm_unavailable",
                f"No AI provider is available for chat: {reason}",
                retryable=False,
            ) from exc
        total_tokens, call_tokens = _tool_budgets(llm)

        # The tools' tenancy + budget state is bound for the whole turn: the
        # up-front lookups below go through the same tools.
        token = set_chat_tool_context(
            project_id=project_id,
            total_token_budget=total_tokens,
            per_call_token_cap=call_tokens,
        )
        try:
            tool_state = get_chat_tool_state()

            # 2. Context, gathered concurrently and without the model.
            context_started = time.perf_counter()
            (
                (history, summary_ctx),
                (snapshot, snapshot_refs),
                project_name,
                recall_ctx,
                (report_ctx, report_sources),
                lookups_ctx,
            ) = await asyncio.gather(
                self._load_history(session_id, before_id=str(question_id)),
                self._no_context() if bound else self._fetch_run_context(project_id, limit=_SNAPSHOT_RUNS),
                self._fetch_project_name(project_id),
                self._fetch_fingerprint_recall(question, project_id),
                self._fetch_bound_report_context(project_id, test_run_id, report_id, report_version),
                self._no_text() if bound else self._prefetch_lookups(
                    question, project_id, emit, session_id=session_id, before_id=str(question_id),
                ),
            )
            timings.context_ms = (time.perf_counter() - context_started) * 1000

            system = self._system_prompt(
                project_name=project_name,
                project_id=project_id,
                snapshot=(
                    "This conversation is bound to the decision report below; answer from it. "
                    "Tools read the project's CURRENT data: say so whenever you use them."
                    if bound else snapshot
                ),
                recall_ctx=recall_ctx,
                report_ctx=report_ctx,
                summary_ctx=summary_ctx,
            )
            history = _fit_history(system, history, _prompt_budget_tokens(llm))
            # The up-front lookups ride WITH the question, not in the system
            # prompt: measured 2026-10-09, mistral-nemo four turns into a
            # conversation answered "is it ready to release?" by repeating its
            # previous answer, with the release-gate verdict sitting unread at
            # the far end of the system prompt.
            asked = question
            if lookups_ctx:
                asked = (
                    f"{question}\n\n---\nLooked up for this question (read this first; it is "
                    f"current data):\n{lookups_ctx}"
                )
            messages: list[BaseMessage] = [
                SystemMessage(content=system), *history, HumanMessage(content=asked),
            ]

            # 3. The model, with the project's read tools. When nothing was
            # looked up for it, the first round must call a tool: asked "is
            # the first one you mentioned flaky?", the model answered from its
            # previous message and offered "Shall I look into its history?"
            # instead of looking.
            stats = await self._converse(
                llm, chat_tools(), messages, emit, state,
                require_tool=not lookups_ctx and _wants_data(question),
            )
        finally:
            reset_chat_tool_context(token)

        reply = "".join(state.shown).strip()
        if not reply:
            raise ChatTurnError(
                "empty_answer",
                "The AI provider returned an empty answer. Try again or rephrase the question.",
                retryable=True,
            )

        tool_trace = list(tool_state.trace) if tool_state else []
        suggested_actions = build_suggested_actions(tool_state) if tool_state else []
        candidates = list(snapshot_refs) + (list(tool_state.refs.values()) if tool_state else [])
        sources = report_sources + _answer_sources(reply, candidates)
        meta = {
            "type": "meta",
            "status": "complete",
            "provider": getattr(llm, "provider_name", None),
            "model": getattr(llm, "model_label", None),
            "first_status_ms": timings.first_status_ms,
            "first_token_ms": timings.first_token_ms,
            "total_ms": timings.since_start(),
            "llm_ms": int(timings.llm_ms),
            "tool_ms": int(timings.tool_ms),
            "context_ms": int(timings.context_ms),
            "rounds": stats["rounds"],
            "tool_calls": stats["tool_calls"],
            "tools": stats["tools"],
        }
        return {
            "reply": reply,
            "sources": sources,
            "tool_trace": tool_trace,
            "suggested_actions": suggested_actions,
            "meta": meta,
            "project_id": project_id,
        }

    async def _finish(self, session_id: str, outcome: dict) -> dict:
        """Save the answer with what it was built from; the ``done`` payload."""
        persisted: list[dict] = list(outcome["sources"])
        if outcome["tool_trace"]:
            persisted.append({"type": "tool_trace", "trace": outcome["tool_trace"]})
        if outcome["suggested_actions"]:
            persisted.append({"type": "suggested_actions", "actions": outcome["suggested_actions"]})
        persisted.append(outcome["meta"])
        message = await self._save_message(session_id, "assistant", outcome["reply"], sources=persisted)
        await self._touch_session(session_id)
        self._spawn(self._maybe_compress_history(session_id))
        meta = outcome["meta"]
        logger.info(
            "chat_turn_answered",
            session_id=str(session_id),
            project_id=str(outcome["project_id"]),
            rounds=meta["rounds"],
            tool_calls=meta["tool_calls"],
            first_token_ms=meta["first_token_ms"],
            total_ms=meta["total_ms"],
            llm_ms=meta["llm_ms"],
            tool_ms=meta["tool_ms"],
        )
        return {
            "message": message,
            "sources": outcome["sources"],
            "tool_trace": outcome["tool_trace"],
            "suggested_actions": outcome["suggested_actions"],
            "meta": meta,
        }

    @staticmethod
    async def _no_context() -> tuple[str, list[dict]]:
        return "", []

    @staticmethod
    async def _no_text() -> str:
        return ""

    def _system_prompt(
        self,
        *,
        project_name: Optional[str],
        project_id: str,
        snapshot: str,
        recall_ctx: str,
        report_ctx: str,
        summary_ctx: str,
    ) -> str:
        system = get_prompt_text("chat_system").format(
            now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            project_scope=project_name or f"project …{project_id[-8:]}",
            snapshot=snapshot or "No test runs are recorded for this project yet.",
        )
        if recall_ctx:
            system += (
                "\n\n## What the platform already knows about the test named in the question\n"
                + recall_ctx
            )
        if report_ctx:
            system += f"\n\n{report_ctx}"
        if summary_ctx:
            system += f"\n\n## Earlier in this conversation (summary)\n{summary_ctx}"
        return system

    async def _converse(
        self,
        llm: Any,
        tools: list,
        messages: list[BaseMessage],
        emit: Emit,
        state: _TurnState,
        *,
        require_tool: bool = False,
    ) -> dict:
        """Stream the model, run the tools it asks for, repeat; return stats.

        ``require_tool`` makes the first round call a tool (``tool_choice=
        "required"``; OpenAI-wire providers only).

        At most ``CHAT_MAX_TOOL_ROUNDS`` rounds may call tools. The round after
        that must answer: OpenAI-wire providers keep the tools declared (one may
        refuse tool messages without them) with ``tool_choice="none"``; others
        (Ollama ignores ``tool_choice``) get the unbound model.
        """
        provider = str(getattr(llm, "provider_name", "") or "")
        model_key = (provider, str(getattr(llm, "model_label", "") or ""))
        by_name = {t.name: t for t in tools}
        use_tools = bool(tools) and model_key not in _TOOLS_OFF
        stats = {"rounds": 0, "tool_calls": 0, "tools": use_tools}
        max_rounds = max(0, int(settings.CHAT_MAX_TOOL_ROUNDS))
        while True:
            last = stats["rounds"] >= max_rounds
            if not use_tools:
                model = llm
            elif last:
                model = llm.bind_tools(tools, tool_choice="none") if provider in _TOOL_CHOICE_PROVIDERS else llm
            elif require_tool and stats["rounds"] == 0 and provider in _TOOL_CHOICE_PROVIDERS:
                model = llm.bind_tools(tools, tool_choice="required")
            else:
                model = llm.bind_tools(tools)
            stats["rounds"] += 1
            try:
                aggregate = await self._stream_once(
                    model, messages, emit, state,
                    # Ollama does not stream a call that declares tools: its
                    # answer arrives whole, after the full generation, so
                    # only the turn deadline bounds it.
                    idle_timeout=None if (provider == "ollama" and use_tools) else
                    float(settings.CHAT_FIRST_TOKEN_TIMEOUT_SECONDS),
                )
            except Exception as exc:
                if not use_tools or not tools_unsupported_error(exc):
                    raise
                # The model has no tool use. Remember it (each turn would
                # otherwise pay a refused call first) and answer from the
                # snapshot and the up-front lookups.
                logger.info("chat_tools_unsupported_by_provider", provider=provider, model=model_key[1])
                _TOOLS_OFF.add(model_key)
                use_tools = False
                stats["tools"] = False
                continue
            if not use_tools or last or aggregate is None:
                return stats
            calls = _tool_calls_of(aggregate)
            if not calls:
                return stats
            calls = calls[:_MAX_CALLS_PER_ROUND]
            messages.append(AIMessage(
                content=_text_of(getattr(aggregate, "content", "")),
                tool_calls=[{k: c[k] for k in ("name", "args", "id")} | {"type": "tool_call"} for c in calls],
            ))
            stats["tool_calls"] += len(calls)
            tool_started = time.perf_counter()
            results = await asyncio.gather(*(self._call_tool(by_name, call, emit) for call in calls))
            state.timings.tool_ms += (time.perf_counter() - tool_started) * 1000
            for call, result in zip(calls, results):
                messages.append(ToolMessage(content=result, tool_call_id=call["id"]))

    async def _stream_once(
        self,
        model: Any,
        messages: list[BaseMessage],
        emit: Emit,
        state: _TurnState,
        *,
        idle_timeout: Optional[float],
    ) -> Any:
        """One provider call, streamed. Text goes to the reader as it arrives.

        ``idle_timeout`` bounds the wait for the first chunk and every gap
        after it (inside ``BudgetedLLM.astream``, so a stall counts against the
        provider's circuit breaker); a provider that stops sending is reported,
        not waited out for the turn's whole budget.
        """
        timings = state.timings
        aggregate = None
        separator_due = bool(state.shown)
        started = time.perf_counter()
        stream_kwargs: dict[str, Any] = {}
        if isinstance(model, BudgetedLLM):
            stream_kwargs = {"idle_timeout": idle_timeout, "slot_timeout": _SLOT_TIMEOUT_SECONDS}
        try:
            async with contextlib.aclosing(model.astream(messages, **stream_kwargs)) as stream:
                async for chunk in stream:
                    aggregate = chunk if aggregate is None else aggregate + chunk
                    text = _text_of(getattr(chunk, "content", ""))
                    if not text:
                        continue
                    if separator_due:
                        state.shown.append("\n\n")
                        emit("delta", {"text": "\n\n"})
                        separator_due = False
                    if timings.first_token_ms is None:
                        timings.first_token_ms = timings.since_start()
                    state.shown.append(text)
                    emit("delta", {"text": text})
        finally:
            timings.llm_ms += (time.perf_counter() - started) * 1000
        return aggregate

    async def _call_tool(self, by_name: dict, call: dict, emit: Emit) -> str:
        from app.tools.chat_read_tools import tool_status_label

        name = str(call.get("name") or "")
        if call.get("error"):
            # Arguments the provider could not parse: tell the model, so it
            # can call again, instead of dropping the call (and the answer).
            return f"{name or 'The tool call'} was not run: its arguments were not valid JSON ({call['error'][:160]})."
        args = call.get("args") or {}
        tool = by_name.get(name)
        if tool is None:
            return f"There is no tool named {name!r}. Available tools: {', '.join(sorted(by_name))}."
        emit("status", {"label": tool_status_label(name, args), "tool": name})
        try:
            result = await tool.ainvoke(args)
        except Exception as exc:  # noqa: BLE001 -- a bad argument is the model's to fix
            logger.info("chat_tool_call_rejected", tool=name, error=str(exc)[:200])
            return f"{name} could not run with those arguments: {str(exc)[:200]}"
        return str(result)

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)

    # ── Focused data fetchers ─────────────────────────────────────────────────

    async def _fetch_bound_report_context(
        self,
        project_id: Optional[str],
        test_run_id: Optional[str],
        report_id: Optional[str],
        report_version: Optional[int],
    ) -> tuple[str, list[dict]]:
        """Load only the immutable report explicitly bound to this session."""
        if not project_id or not test_run_id or not report_id:
            return "", []
        source_base = {
            "type": "decision_report",
            "id": str(report_id),
            "test_run_id": str(test_run_id),
            "report_version": report_version,
        }
        try:
            query = {
                "report_id": str(report_id),
                "project_id": str(project_id),
                "test_run_id": str(test_run_id),
                "status": "published",
            }
            if report_version is not None:
                query["report_version"] = int(report_version)
            report = await get_mongo_db()[Collections.DECISION_REPORTS].find_one(query, {"_id": 0})
            if report is None:
                return (
                    "### Bound Decision Report\nThe selected DecisionReport version is unavailable. Do not substitute an unbound or latest report.",
                    [{**source_base, "status": "unavailable"}],
                )
            version = int(report.get("report_version") or report_version or 0)
            projection = {
                "report_id": report.get("report_id"),
                "report_version": version,
                "test_run_id": report.get("test_run_id"),
                "verification": report.get("verification"),
                "decision_intelligence": report.get("decision_intelligence"),
            }
            rendered = sanitize_for_llm(json.dumps(projection, default=str, ensure_ascii=False))[:30_000]
            return (
                f"### Bound Decision Report (immutable v{version})\n{rendered}",
                [{**source_base, "report_version": version, "status": "published"}],
            )
        except Exception as exc:
            logger.warning("bound_report_context_unavailable", error_type=type(exc).__name__, report_id=str(report_id)[:64])
            return (
                "### Bound Decision Report\nThe selected DecisionReport could not be loaded. Do not substitute an unbound or latest report.",
                [{**source_base, "status": "unavailable"}],
            )

    async def _fetch_run_context(
        self, project_id: Optional[str], limit: int = 5
    ) -> tuple[str, list[dict]]:
        """The grounding snapshot: recent runs in a table, and the latest run's
        failing tests by name.

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
                        TestRun.total_tests, TestRun.passed_tests, TestRun.failed_tests,
                        TestRun.skipped_tests, TestRun.broken_tests, TestRun.pass_rate,
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

                # Every bucket, and the basis of the rate.
                #
                # This used to be Tests | Failures | Pass Rate — 10 | 4 | 44.4%
                # for a run of 4 passed / 4 failed / 1 broken / 1 skipped. Those
                # three numbers cannot all be true of the same run as a reader
                # (or a model) would combine them: 10 - 4 = 6 passed is 60%, not
                # 44.4%. The rate is passed/executed and the missing terms were
                # never shown. The model then reproduced the contradiction
                # verbatim — "4 tests failed out of 10, resulting in a 44.4%
                # pass rate" (homelab, 2026-08-16).
                #
                # Same defect the run summary had. Handing a model figures that
                # do not reconcile is handing it a reason to invent one that
                # does.
                # `Executed` is stated, not left to be derived. With only a
                # total and a skipped count, the model answered "4 tests failed
                # out of 10 executed" — executed is 9. The figures reconciled;
                # the term it had to compute did not. Publishing a rate always
                # means publishing its denominator.
                header = (
                    "| Build | Branch | Status | Tests | Executed | Passed | Failed "
                    "| Skipped | Broken | Pass Rate | Date |\n"
                    "|---|---|---|---|---|---|---|---|---|---|---|"
                )
                lines = []
                src = []
                for r in rows:
                    ts = r.start_time.strftime("%Y-%m-%d %H:%M") if r.start_time else "?"
                    executed = max((r.total_tests or 0) - (r.skipped_tests or 0), 0)
                    # A run still in progress has no pass rate. `:.1f` on None
                    # raised, the except below returned "", and the whole
                    # snapshot vanished whenever any listed run was running.
                    rate = f"**{r.pass_rate:.1f}%**" if r.pass_rate is not None else "n/a"
                    lines.append(
                        f"| {r.build_number} | {r.branch or '?'} | {r.status} "
                        f"| {r.total_tests} | {executed} "
                        f"| {r.passed_tests} | {r.failed_tests} "
                        f"| {r.skipped_tests} | {r.broken_tests or 0} "
                        f"| {rate} | {ts} |"
                    )
                    src.append({"type": "test_run", "id": str(r.id), "build": r.build_number})

                result_text = (
                    header + "\n" + "\n".join(lines)
                    + "\n\nPass rate is passed / executed; skipped tests are "
                      "excluded from the denominator. Newest run first."
                )

                # FR-002: which tests failed, in which suite, and why.
                #
                # Until now this context was run-level aggregates only, so the
                # model was asked to describe failures it had never been shown.
                # It could not name a test, name a suite, or give a reason — the
                # owner's report that the summary "lack[s] credible details" was
                # a fair description of its input, not of the model.
                #
                # Detail is fetched for the MOST RECENT run only: it is the one
                # people ask about, and quoting every failure of every listed run
                # would crowd the prompt without being read.
                from app.services.failure_detail_service import (  # noqa: PLC0415
                    failure_detail,
                    format_for_prompt,
                )

                detail = await failure_detail(db, rows[0].id)
                if detail.get("failures"):
                    result_text += (
                        f"\n\n### Failing tests in the most recent run "
                        f"({rows[0].build_number})\n"
                        + format_for_prompt(detail)
                        + "\n\nName these tests and their suites when asked what "
                        "failed. Do not invent test names or reasons beyond the "
                        "list above; if something is not listed, say it was not "
                        "recorded."
                    )
                    for failure in detail["failures"]:
                        if failure.get("test_case_id") and failure.get("test_name"):
                            src.append({
                                "type": "test_case",
                                "id": failure["test_case_id"],
                                "run_id": str(rows[0].id),
                                "name": failure["test_name"],
                            })
                else:
                    result_text += (
                        f"\n\nThe most recent run ({rows[0].build_number}) recorded "
                        "no failed or broken tests."
                    )

                _RUN_CONTEXT_CACHE[cache_key] = (time.monotonic(), result_text, src)
                return result_text, src
        except Exception as exc:
            logger.debug("run_context_fetch_error", error=str(exc))
            return "", []

    async def _fetch_fingerprint_recall(
        self, query: str, project_id: Optional[str]
    ) -> str:
        """AI-F3: fingerprint-history recall when the query names a test.

        Matches recently-failing test names against the query text (recall is
        project-scoped, so this is skipped without a project), then reuses the
        same ``memory_recall`` service the ReAct tool consumes. Best-effort —
        any failure returns an empty string so chat keeps working.
        """
        if not project_id or not query:
            return ""
        try:
            import uuid as _uuid

            from app.services.memory_recall import (
                recall_failure_history,
                render_recall_report,
            )

            from app.models.postgres import TestStatus

            q_lower = query.lower()
            async with AsyncSessionLocal() as db:
                rows = (
                    await db.execute(
                        select(TestCase.test_name, TestCase.test_fingerprint)
                        .join(TestRun, TestRun.id == TestCase.test_run_id)
                        .where(
                            TestRun.project_id == project_id,
                            TestCase.status.in_(
                                [TestStatus.FAILED.value, TestStatus.BROKEN.value]
                            ),
                            TestCase.test_fingerprint.isnot(None),
                        )
                        .order_by(TestCase.created_at.desc())
                        .limit(300)
                    )
                ).all()
                matched = next(
                    (
                        r for r in rows
                        if r.test_name and len(r.test_name) >= 6
                        and r.test_name.lower() in q_lower
                    ),
                    None,
                )
                if matched is None:
                    return ""
                recall = await recall_failure_history(
                    db,
                    _uuid.UUID(str(project_id)),
                    test_fingerprint=matched.test_fingerprint,
                    error_text=query,
                    test_name=matched.test_name,
                )
            if not recall.get("has_history"):
                return ""
            return render_recall_report(recall)
        except Exception as exc:
            logger.debug("fingerprint_recall_fetch_error", error=str(exc))
            return ""

    async def _existing_tests(self, candidates: list[str], project_id: str) -> set[str]:
        """The ``candidates`` that are test names in this project."""
        if not candidates:
            return set()
        try:
            async with AsyncSessionLocal() as db:
                rows = (
                    await db.execute(
                        select(TestCase.test_name)
                        .join(TestRun, TestRun.id == TestCase.test_run_id)
                        .where(
                            TestRun.project_id == project_id,
                            TestCase.test_name.in_(candidates),
                        )
                        .distinct()
                    )
                ).scalars().all()
            return {str(name) for name in rows}
        except Exception as exc:  # noqa: BLE001 -- the model can still call the tool
            logger.debug("chat_named_test_lookup_failed", error=str(exc)[:200])
            return set()

    async def _named_tests(self, question: str, project_id: str) -> list[str]:
        """Test names, as written in the question, that exist in this project."""
        candidates = _test_name_candidates(question)[:20]
        found = await self._existing_tests(candidates, project_id)
        return [name for name in candidates if name in found][:3]

    async def _previous_answer(self, session_id: str, before_id: Optional[str]) -> str:
        """The whole text of the last answer before the question ``before_id``
        (the replayed history is truncated; a long table's last row is not)."""
        try:
            async with AsyncSessionLocal() as db:
                query = select(ChatMessage.content).where(
                    ChatMessage.session_id == session_id, ChatMessage.role == "assistant",
                )
                if before_id is not None:
                    asked_at = (
                        select(ChatMessage.created_at)
                        .where(ChatMessage.id == uuid.UUID(str(before_id)))
                        .scalar_subquery()
                    )
                    query = query.where(ChatMessage.created_at < asked_at)
                row = (
                    await db.execute(query.order_by(ChatMessage.created_at.desc()).limit(1))
                ).first()
            return str(row[0]) if row else ""
        except Exception as exc:  # noqa: BLE001 -- the model can still look it up
            logger.debug("chat_previous_answer_lookup_failed", error=str(exc)[:200])
            return ""

    async def _referred_test(
        self, question: str, project_id: str, session_id: Optional[str], before_id: Optional[str],
    ) -> Optional[tuple[str, str, str]]:
        """("the first one", the test it means, "first") when the question
        points at a position in the previous answer's list.

        Measured on the homelab, 2026-10-09: after a table whose first row was
        testAuthenticationCase01, "Is the first one flaky or a regression?" got
        testAuthenticationCase02's history; after a flaky-tests table headed by
        testCheckoutCase03, "the top one" got testAuthenticationCase02 again.
        The model took "first" from the snapshot's list of the latest run's
        failures (ordered by AI confidence), not from its own answer.
        """
        match = _ORDINAL_REFERENCE.search(question or "")
        if not match or not session_id:
            return None
        previous = await self._previous_answer(session_id, before_id)
        if not previous:
            return None
        candidates = _test_name_candidates(previous)[:80]
        found = await self._existing_tests(candidates, project_id)
        listed = [name for name in candidates if name in found]
        word = match.group(1).lower()
        index = _ORDINAL_INDEX[word]
        if not listed or index >= len(listed):
            return None
        position = "first" if word == "top" else word
        return match.group(0), listed[index], position

    async def _prefetch_lookups(
        self,
        question: str,
        project_id: str,
        emit: Emit,
        *,
        session_id: Optional[str] = None,
        before_id: Optional[str] = None,
    ) -> str:
        """Look up, before the model runs, the facts it was measured inventing.

        Measured against mistral-nemo on the homelab, 2026-10-09:
          * "Has test_refund_flow failed before, and is it quarantined?" — no
            tool call; a table of builds, statuses and dates that do not exist,
            and a quarantine state it never read.
          * "Is the latest build ready to release?" — no tool call; "not ready,
            it failed 10 tests", with the release gate never consulted.
        So a question that names a test gets that test's history (and its
        quarantine state when it asks), and a release question gets the
        release-gate verdict, whatever the model would have done. Each goes
        through the tool, so it is traced and budgeted like any other call.
        """
        from app.tools.chat_read_tools import (
            check_quarantine_status,
            get_release_gate_verdict,
            get_test_history,
            list_flaky_tests,
            tool_status_label,
        )

        lookups: list[tuple[str, Any, dict]] = []
        named = (await self._named_tests(question, project_id))[:2]
        meant = ""
        if not named:
            referred = await self._referred_test(question, project_id, session_id, before_id)
            if referred:
                phrase, name, position = referred
                named = [name]
                meant = (
                    f'### Which test "{phrase}" is\n"{phrase}" is {name}: the {position} '
                    "test your previous answer lists. Answer about this test.\n\n"
                )
        for name in named:
            lookups.append((f"History of {name}", get_test_history, {"test_name": name}))
            if _ASKS_ABOUT_QUARANTINE.search(question):
                lookups.append((f"Quarantine status of {name}", check_quarantine_status, {"test_name": name}))
        if not named and _ASKS_ABOUT_FLAKY.search(question) and not _REFERS_BACK.search(question):
            # "Which tests are flaky?" Measured: the model called the list AND,
            # in the same breath, quarantine checks for two tests it guessed
            # from the previous answer -- then reported its guesses as the
            # flaky tests. The list (with quarantine state) comes first.
            lookups.append(("Flaky tests", list_flaky_tests, {"query": ""}))
        if _ASKS_ABOUT_RELEASE.search(question):
            lookups.append(("Release-gate verdict", get_release_gate_verdict, {"query": ""}))
        if not lookups:
            return ""

        async def run(title: str, tool: Any, args: dict) -> str:
            emit("status", {"label": tool_status_label(tool.name, args), "tool": tool.name})
            return f"### {title}\n{await tool.ainvoke(args)}"

        parts = await asyncio.gather(*(run(*lookup) for lookup in lookups))
        return meant + "\n\n".join(parts)

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

    async def _load_history(
        self, session_id: str, before_id: Optional[str] = None,
    ) -> tuple[list[BaseMessage], str]:
        """The conversation so far, as model messages, plus the compression summary.

        Only answered questions are replayed: a question whose turn failed (or
        was stopped before any text) has no answer, and two user messages in a
        row are refused by some providers. ``before_id`` is the question being
        answered now; it and anything after it are excluded.
        """
        limit = max(0, int(settings.CHAT_HISTORY_MESSAGES))
        keep = limit - (limit % 2)
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(
                        ChatMessage.id, ChatMessage.role, ChatMessage.content,
                        ChatMessage.created_at,
                    )
                    .where(
                        ChatMessage.session_id == session_id,
                        ChatMessage.role.in_(["user", "assistant"]),
                    )
                    .order_by(ChatMessage.created_at.desc())
                    .limit(keep + 6)
                )
            ).all()
            summary_row = (
                await db.execute(
                    select(ChatMessage.content)
                    .where(
                        ChatMessage.session_id == session_id,
                        ChatMessage.role == "summary",
                    )
                    .order_by(ChatMessage.created_at.desc())
                    .limit(1)
                )
            ).first()
        summary_text = summary_row[0] if summary_row else ""

        chronological = list(reversed(rows))
        if before_id is not None:
            ids = [str(r.id) for r in chronological]
            if before_id in ids:
                chronological = chronological[: ids.index(before_id)]
        pairs: list[BaseMessage] = []
        i = 0
        while i < len(chronological):
            current = chronological[i]
            following = chronological[i + 1] if i + 1 < len(chronological) else None
            if current.role == "user" and following is not None and following.role == "assistant":
                pairs.append(HumanMessage(content=current.content[:_HISTORY_QUESTION_CHARS]))
                pairs.append(AIMessage(content=following.content[:_HISTORY_ANSWER_CHARS]))
                i += 2
            else:
                i += 1
        return (pairs[-keep:] if keep else []), summary_text

    async def _prepare_retry(self, session_id: str) -> tuple[str, Any]:
        """The question to answer again, and its id.

        The latest question must be unanswered, or answered only by a stopped
        partial answer — which the retry replaces (it is deleted here).
        """
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(ChatMessage)
                    .where(
                        ChatMessage.session_id == session_id,
                        ChatMessage.role.in_(["user", "assistant"]),
                    )
                    .order_by(ChatMessage.created_at.desc())
                    .limit(10)
                )
            ).scalars().all()
            question = next((r for r in rows if r.role == "user"), None)
            if question is None:
                raise ChatTurnError("nothing_to_retry", "There is no question to retry.", retryable=False)
            answers = [r for r in rows if r.role == "assistant" and r.created_at > question.created_at]
            if any(not _is_stopped(r.sources) for r in answers):
                raise ChatTurnError(
                    "nothing_to_retry", "The last question already has an answer.", retryable=False,
                )
            for stale in answers:
                await db.delete(stale)
            await db.commit()
            return question.content, question.id

    async def _save_stopped(self, session_id: str, state: _TurnState) -> None:
        """Keep a stopped answer's text: the reader saw it."""
        text = "".join(state.shown).strip()
        if not text:
            return
        try:
            await self._save_message(session_id, "assistant", text, sources=[{
                "type": "meta",
                "status": "stopped",
                "first_token_ms": state.timings.first_token_ms,
                "total_ms": state.timings.since_start(),
            }])
        except Exception as exc:  # noqa: BLE001 -- the reader is gone; log only
            logger.warning("chat_stopped_answer_not_saved", session_id=str(session_id), error=str(exc)[:200])

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

            # Registry-versioned template (AI-F2); rendered output is
            # byte-identical to the previous inline f-string.
            compression_prompt = get_prompt_text("chat_compression").format(
                transcript=transcript,
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
    ) -> dict:
        """Persist one message and return it in ``ChatMessageResponse`` shape."""
        # Enforce max content length to prevent unbounded storage growth
        truncated = False
        safe_content = content or ""
        if len(safe_content) > _MAX_MESSAGE_LENGTH:
            safe_content = safe_content[:_MAX_MESSAGE_LENGTH] + "\n\n[Message truncated — exceeded maximum length]"
            truncated = True

        # Redact secrets from stored content
        safe_content = redact_text(safe_content)
        message_id = uuid.uuid4()
        created_at = datetime.now(timezone.utc)

        async with AsyncSessionLocal() as db:
            db.add(ChatMessage(
                id=message_id,
                session_id=session_id,
                role=role,
                content=safe_content,
                sources=sources,
                created_at=created_at,
            ))
            await db.commit()

        if truncated:
            logger.info("Message truncated", session_id=session_id, role=role, original_length=len(content))
        return {
            "id": str(message_id),
            "session_id": str(session_id),
            "role": role,
            "content": safe_content,
            "sources": sources,
            "created_at": _iso(created_at),
        }

    async def _touch_session(self, session_id: str) -> None:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(ChatSession).where(ChatSession.id == session_id)
            )
            session = result.scalar_one_or_none()
            if session:
                session.updated_at = datetime.now(timezone.utc)
                await db.commit()
