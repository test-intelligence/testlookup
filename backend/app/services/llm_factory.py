"""
Provider-agnostic LLM factory.
Switch between Ollama (offline), OpenAI, Gemini, or any compatible provider
by changing the LLM_PROVIDER environment variable — no agent code changes needed.
"""
import logging
import time
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel

from app.core.config import settings

logger = logging.getLogger(__name__)


class PipelineBudgetExceeded(RuntimeError):
    """Raised before provider invocation when a graph budget envelope is denied."""


class BudgetedLLM:
    """Small LangChain-compatible gate around a provider model.

    The lifecycle hook installs a ContextVar for the active graph stage. This
    wrapper checks it at the actual invocation boundary so every provider,
    including direct graph call sites, shares the same admission decision.
    """

    def __init__(
        self,
        inner: BaseChatModel,
        *,
        provider: str = "unknown",
        model: str = "unknown",
        connect_retries: int = 0,
    ):
        self._inner = inner
        self._provider = provider
        self._model = model
        # Re-audit L2: AI_MAX_RETRIES for a provider client with no retry of
        # its own (ChatOllama). Connect-phase failures only -- no request
        # reached the server, so a retry cannot double-bill or double-run it,
        # and a read timeout is NOT retried (it would multiply the stage's
        # wall clock by the retry count). Clients with native retries get
        # AI_MAX_RETRIES through their own ``max_retries`` instead.
        self._connect_retries = max(0, int(connect_retries or 0))

    def _retryable(self, exc: BaseException) -> bool:
        # An offline pin refusal (llm_egress.OffBoxTargetError) is an OSError,
        # not a ConnectionError, so it is never retried: it is policy, not a
        # transient fault.
        try:
            import httpx
        except ImportError:  # pragma: no cover
            return isinstance(exc, ConnectionError)
        return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, ConnectionError))

    @staticmethod
    def _retry_delay(attempt: int) -> float:
        return min(0.5 * (2 ** attempt), 8.0)

    async def _call_async(self, args, kwargs):
        import asyncio

        attempt = 0
        while True:
            try:
                return await self._inner.ainvoke(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 -- re-raised unless retryable
                if attempt >= self._connect_retries or not self._retryable(exc):
                    raise
                await asyncio.sleep(self._retry_delay(attempt))
                attempt += 1

    def _call_sync(self, args, kwargs):
        attempt = 0
        while True:
            try:
                return self._inner.invoke(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 -- re-raised unless retryable
                if attempt >= self._connect_retries or not self._retryable(exc):
                    raise
                time.sleep(self._retry_delay(attempt))
                attempt += 1

    def _check(self) -> None:
        from app.services.pipeline_budget_service import get_pipeline_budget_context

        context = get_pipeline_budget_context()
        if context and context.get("blocked"):
            raise PipelineBudgetExceeded(
                str(context.get("stop_reason") or "pipeline_budget_exhausted")
            )

    def _prepare_invocation(self, args, kwargs):
        from app.services.llm_policy_service import (
            record_invocation_audit,
            sanitize_invocation,
        )

        stats = {"redacted_strings": 0}
        clean_args = tuple(sanitize_invocation(item, stats=stats) for item in args)
        clean_kwargs = {
            key: sanitize_invocation(value, stats=stats)
            for key, value in kwargs.items()
        }
        record_invocation_audit(provider=self._provider, model=self._model, stats=stats)
        return clean_args, clean_kwargs

    @staticmethod
    def _record_usage(result) -> bool:
        """Add the call to the active stage's budget context. Returns whether
        there was one: a stage meters (and bills) the calls it observed."""
        from app.services.pipeline_budget_service import get_pipeline_budget_context

        context = get_pipeline_budget_context()
        if context is None:
            return False
        context["observed_llm_calls"] = int(context.get("observed_llm_calls") or 0) + 1
        usage = getattr(result, "usage_metadata", None) or {}
        if not isinstance(usage, dict):
            usage = {}
        response_usage = getattr(result, "response_metadata", None) or {}
        if isinstance(response_usage, dict):
            response_usage = response_usage.get("token_usage") or response_usage.get("usage") or {}
        if not isinstance(response_usage, dict):
            response_usage = {}
        input_tokens = usage.get("input_tokens", response_usage.get("prompt_tokens", 0))
        output_tokens = usage.get("output_tokens", response_usage.get("completion_tokens", 0))
        if isinstance(input_tokens, int) and input_tokens >= 0:
            context["observed_input_tokens"] = int(context.get("observed_input_tokens") or 0) + input_tokens
        if isinstance(output_tokens, int) and output_tokens >= 0:
            context["observed_output_tokens"] = int(context.get("observed_output_tokens") or 0) + output_tokens
        return True

    def _observe(self, status: str, elapsed: float) -> None:
        """Record one provider call. Never raises -- telemetry is not the call.

        ``llm_requests_total`` and ``llm_request_duration_seconds`` were both
        declared and never emitted; the latency histogram backs two panels on
        the Grafana overview dashboard (p50 and p95 by provider), which have
        therefore been empty since they were written. This wrapper is the one
        place every provider and every graph call site passes through, and it
        already knows the provider, so it is where the emission belongs.
        """
        try:
            from app.core.metrics import (
                llm_request_duration_seconds,
                llm_requests_total,
            )

            llm_requests_total.labels(provider=self._provider, status=status).inc()
            # Latency is observed for failures too: a provider that times out
            # at 120s is exactly the signal these panels exist to show, and
            # dropping it would make an outage look like reduced traffic.
            llm_request_duration_seconds.labels(provider=self._provider).observe(elapsed)
        except Exception:  # noqa: BLE001 -- metrics must never break inference
            pass

    @staticmethod
    def _status_for(exc: BaseException) -> str:
        # The declared vocabulary is success|failure|timeout. asyncio.TimeoutError
        # is an alias of the builtin from 3.11, so one check covers both.
        return "timeout" if isinstance(exc, TimeoutError) else "failure"

    async def ainvoke(self, *args, **kwargs):
        # _check() raises PipelineBudgetExceeded BEFORE any provider call, so
        # it is deliberately outside the timed region: no request was made, and
        # counting it as a failed LLM call would blame the provider for a
        # budget decision taken here.
        self._check()
        args, kwargs = self._prepare_invocation(args, kwargs)
        from app.services import llm_cost_reservation as cost
        from app.services.llm_cluster_semaphore import cluster_llm_slot

        # Re-audit M13: reserve this call's worst case against the project's
        # monthly cap atomically, BEFORE the provider is called. Raises
        # CostCapExceeded (no request made) when the cap cannot absorb it.
        input_tokens = cost.estimate_input_tokens(args, kwargs)
        reservation = await cost.reserve(
            self._provider,
            self._model,
            input_tokens=input_tokens,
            max_output_tokens=cost.output_ceiling(self._inner, settings.LLM_MAX_TOKENS),
        )
        # Nothing is charged unless the provider may have been reached: a
        # failure while waiting for the slot (or a cancellation there) never
        # sent anything.
        actual_usd = 0.0
        record_meter = False
        tokens: Optional[tuple[int, int]] = None
        try:
            # Re-audit M12: one cluster-wide slot per in-flight call.
            async with cluster_llm_slot(self._provider):
                started = time.perf_counter()
                try:
                    result = await self._call_async(args, kwargs)
                except BaseException as exc:
                    self._observe(self._status_for(exc), time.perf_counter() - started)
                    # QA-B45-A1: a failure after the request was sent (read
                    # timeout, cancellation, 5xx) may still be billed. Keep
                    # the worst case, and meter it: no stage records a
                    # failed call.
                    if reservation is not None and not cost.failure_proves_no_request(exc):
                        actual_usd = reservation.estimated_usd
                        record_meter = True
                    raise
                self._observe("success", time.perf_counter() - started)
                metered_by_stage = self._record_usage(result)
                if reservation is not None:
                    tokens = cost.usage_tokens(result)
                    actual_usd = (
                        cost.price(self._provider, self._model, *tokens)
                        if tokens is not None
                        # Unreported usage: keep the worst case rather than guess low.
                        else reservation.estimated_usd
                    )
                    record_meter = not metered_by_stage
                return result
        finally:
            await cost.settle(
                reservation, actual_usd, record_meter=record_meter,
                input_tokens=tokens[0] if tokens else 0,
                output_tokens=tokens[1] if tokens else 0,
            )

    def invoke(self, *args, **kwargs):
        self._check()
        args, kwargs = self._prepare_invocation(args, kwargs)
        from app.services import llm_cost_reservation as cost

        # The reservation store is async. A synchronous call charged to a
        # capped-eligible project cannot reserve, so it is refused rather than
        # let through unchecked (re-audit M13). No app code path uses it today.
        if cost.current_cost_scope() and cost.price(
            self._provider,
            self._model,
            cost.estimate_input_tokens(args, kwargs),
            cost.output_ceiling(self._inner, settings.LLM_MAX_TOKENS),
        ) > 0:
            raise cost.CostCapExceeded(
                "synchronous invoke cannot reserve against the LLM cost cap; use ainvoke"
            )
        started = time.perf_counter()
        try:
            result = self._call_sync(args, kwargs)
        except BaseException as exc:
            self._observe(self._status_for(exc), time.perf_counter() - started)
            raise
        self._observe("success", time.perf_counter() - started)
        self._record_usage(result)
        return result

    def bind(self, *args, **kwargs):
        return BudgetedLLM(
            self._inner.bind(*args, **kwargs),
            provider=self._provider,
            model=self._model,
            connect_retries=self._connect_retries,
        )

    def with_structured_output(self, *args, **kwargs):
        return BudgetedLLM(
            self._inner.with_structured_output(*args, **kwargs),
            provider=self._provider,
            model=self._model,
            connect_retries=self._connect_retries,
        )

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _budgeted(
    model: BaseChatModel, *, provider: str, model_name: str, connect_retries: int = 0,
) -> BudgetedLLM:
    return BudgetedLLM(model, provider=provider, model=model_name, connect_retries=connect_retries)


def _pinned_http_clients(offline: bool) -> dict:
    """``http_client``/``http_async_client`` for an OpenAI-wire LOCAL provider.

    Under AI_OFFLINE_MODE the connection is pinned to the on-box addresses
    validated at connect time (re-audit N8); online, the SDK's own clients.
    """
    if not offline:
        return {}
    from app.services.llm_egress import local_only_async_client, local_only_sync_client

    return {
        "http_client": local_only_sync_client(),
        "http_async_client": local_only_async_client(),
    }


def _default_llm_base_url(provider: str) -> str | None:
    return {
        "ollama": settings.OLLAMA_BASE_URL,
        "lmstudio": settings.LMSTUDIO_BASE_URL,
        "localai": settings.LOCALAI_BASE_URL,
        "vllm": settings.VLLM_BASE_URL,
    }.get(provider)


async def get_llm(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    track: Optional[str] = None,
) -> BaseChatModel:
    """
    Return a LangChain chat model for the configured provider.

    Args:
        provider:    Override LLM_PROVIDER env var
        model:       Override LLM_MODEL env var (takes precedence over registry)
        temperature: Override LLM_TEMPERATURE env var
        track:       If set ("reasoning"), check ModelRegistry for a promoted fine-tuned
                     model before falling back to LLM_MODEL. Used by run_triage_agent.

    Returns:
        A LangChain BaseChatModel compatible with ReAct agents
    """
    # LP-1: Use runtime config resolver instead of static env-only settings
    _effective: dict = {}
    if provider is None:
        from app.services.ai_config_resolver import get_effective_ai_config  # noqa: PLC0415
        try:
            _effective = await get_effective_ai_config()
        except Exception:
            pass  # Fall through to env defaults

    _provider = (provider or _effective.get("provider") or settings.LLM_PROVIDER).lower()
    _temperature = temperature if temperature is not None else _effective.get("temperature", settings.LLM_TEMPERATURE)
    _max_tokens = _effective.get("max_tokens", settings.LLM_MAX_TOKENS)
    _base_url = _effective.get("base_url") or _default_llm_base_url(_provider)
    _offline = _effective.get("offline_mode", settings.AI_OFFLINE_MODE)
    from app.services.llm_policy_service import enforce_provider_policy_async

    # Async: the residency lookup must not block the event loop (re-audit N11).
    await enforce_provider_policy_async(_provider, offline=bool(_offline), base_url=_base_url)
    _retries = max(0, int(settings.AI_MAX_RETRIES or 0))

    # Resolve model name: explicit override > fine-tuned registry > effective config > env default
    if model:
        _model = model
    elif track:
        _model = await _async_get_active_model(track) or _effective.get("model", settings.LLM_MODEL)
    else:
        _model = _effective.get("model", settings.LLM_MODEL)

    if track:
        logger.info("Initialising LLM: provider=%s model=%s track=%s", _provider, _model, track)
    else:
        logger.info("Initialising LLM: provider=%s model=%s", _provider, _model)

    if _provider == "ollama":
        from langchain_ollama import ChatOllama
        chat = ChatOllama(
            model=_model,
            base_url=_base_url or settings.OLLAMA_BASE_URL,
            temperature=_temperature,
            # num_predict caps OUTPUT; num_ctx is the CONTEXT window. Leaving
            # num_ctx unset let Ollama apply its own default and truncate long
            # prompts server-side with no error -- silent evidence loss (F-5).
            num_predict=_max_tokens,
            num_ctx=settings.OLLAMA_NUM_CTX,
        )
        if _offline:
            from app.services.llm_egress import pin_ollama_clients
            pin_ollama_clients(chat)
        # ChatOllama has no retry of its own; BudgetedLLM applies AI_MAX_RETRIES
        # to connect-phase failures (re-audit L2).
        return _budgeted(chat, provider=_provider, model_name=_model, connect_retries=_retries)

    elif _provider == "lmstudio":
        from langchain_openai import ChatOpenAI
        return _budgeted(ChatOpenAI(  # type: ignore
            model=_model,
            base_url=_base_url or settings.LMSTUDIO_BASE_URL,
            api_key="lm-studio",  # type: ignore
            temperature=_temperature,
            max_tokens=_max_tokens,
            max_retries=_retries,
            **_pinned_http_clients(bool(_offline)),
        ), provider=_provider, model_name=_model)

    elif _provider == "localai":
        from langchain_openai import ChatOpenAI
        return _budgeted(ChatOpenAI(  # type: ignore
            model=_model,
            base_url=_base_url or settings.LOCALAI_BASE_URL,
            api_key="localai",  # type: ignore
            temperature=_temperature,
            max_tokens=_max_tokens,
            max_retries=_retries,
            **_pinned_http_clients(bool(_offline)),
        ), provider=_provider, model_name=_model)

    elif _provider == "vllm":
        from langchain_openai import ChatOpenAI
        return _budgeted(ChatOpenAI(  # type: ignore
            model=_model,
            base_url=_base_url or settings.VLLM_BASE_URL,
            api_key="vllm",  # type: ignore
            temperature=_temperature,
            max_tokens=_max_tokens,
            max_retries=_retries,
            **_pinned_http_clients(bool(_offline)),
        ), provider=_provider, model_name=_model)

    elif _provider == "openai":
        if _offline:
            raise ValueError("AI_OFFLINE_MODE=true but LLM_PROVIDER=openai — refusing to call external API")
        _api_key = _effective.get("openai_api_key") or settings.OPENAI_API_KEY
        from langchain_openai import ChatOpenAI
        # Omitted (not None) when unset, so langchain's OPENAI_API_BASE
        # fallback still applies; typed so mypy does not match a narrowed
        # dict value against every keyword parameter.
        _openai_base: dict[str, Any] = {"base_url": _base_url} if _base_url else {}
        return _budgeted(ChatOpenAI(  # type: ignore
            model=_model,
            api_key=_api_key,  # type: ignore
            **_openai_base,
            temperature=_temperature,
            max_tokens=_max_tokens,
            max_retries=_retries,
        ), provider=_provider, model_name=_model)

    elif _provider == "gemini":
        if _offline:
            raise ValueError("AI_OFFLINE_MODE=true but LLM_PROVIDER=gemini — refusing to call external API")
        _api_key = _effective.get("google_api_key") or settings.GOOGLE_API_KEY
        from langchain_google_genai import ChatGoogleGenerativeAI
        return _budgeted(ChatGoogleGenerativeAI(  # type: ignore
            model=_model,
            google_api_key=_api_key,
            temperature=_temperature,
            max_retries=_retries,
        ), provider=_provider, model_name=_model)

    elif _provider == "anthropic":
        if _offline:
            raise ValueError("AI_OFFLINE_MODE=true but LLM_PROVIDER=anthropic — refusing to call external API")
        _api_key = _effective.get("anthropic_api_key") or getattr(settings, "ANTHROPIC_API_KEY", None)
        if not _api_key:
            raise ValueError("Anthropic API key not configured. Set it in Settings > AI Configuration.")
        from langchain_anthropic import ChatAnthropic
        return _budgeted(ChatAnthropic(  # type: ignore
            model=_model,
            api_key=_api_key,
            temperature=_temperature,
            max_tokens=_max_tokens,
            max_retries=_retries,
        ), provider=_provider, model_name=_model)

    elif _provider == "openrouter":
        if _offline:
            raise ValueError("AI_OFFLINE_MODE=true but LLM_PROVIDER=openrouter — refusing to call external API")
        _api_key = _effective.get("openrouter_api_key") or settings.OPENROUTER_API_KEY
        if not _api_key:
            raise ValueError(
                "OpenRouter API key not configured. Set OPENROUTER_API_KEY "
                "(or configure it in Settings > AI Configuration)."
            )
        # OpenRouter is OpenAI-wire-compatible, so ChatOpenAI drives it with a
        # different base URL. The two extra headers are how OpenRouter
        # attributes spend on its dashboard.
        from langchain_openai import ChatOpenAI
        return _budgeted(ChatOpenAI(  # type: ignore
            model=_model,
            api_key=_api_key,  # type: ignore
            base_url=_base_url or settings.OPENROUTER_BASE_URL,
            temperature=_temperature,
            max_tokens=_max_tokens,
            max_retries=_retries,
            default_headers={
                "HTTP-Referer": settings.OPENROUTER_SITE_URL,
                "X-Title": settings.OPENROUTER_APP_NAME,
            },
        ), provider=_provider, model_name=_model)

    else:
        raise ValueError(f"Unknown LLM provider: '{_provider}'. "
                         f"Supported: ollama, lmstudio, localai, vllm, openai, "
                         f"gemini, anthropic, openrouter")


def get_embedding_model():
    """Return a LangChain embedding model for semantic search."""
    provider = settings.EMBEDDING_PROVIDER.lower()
    model = settings.EMBEDDING_MODEL
    from app.services.llm_policy_service import enforce_provider_policy

    enforce_provider_policy(
        provider,
        offline=bool(settings.AI_OFFLINE_MODE),
        base_url=settings.OLLAMA_BASE_URL if provider == "ollama" else None,
    )

    logger.info(f"Initialising embedding model: provider={provider}, model={model}")

    if provider == "ollama":
        from langchain_ollama import OllamaEmbeddings
        embeddings = OllamaEmbeddings(
            model=model,
            base_url=settings.OLLAMA_BASE_URL,
        )
        if settings.AI_OFFLINE_MODE:
            # Same connect-time pin as the chat models (re-audit N8).
            from app.services.llm_egress import pin_ollama_clients
            pin_ollama_clients(embeddings)
        return embeddings
    elif provider == "openai":
        if settings.AI_OFFLINE_MODE:
            raise ValueError("AI_OFFLINE_MODE=true — cannot use OpenAI embeddings")
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(model=model, api_key=settings.OPENAI_API_KEY)
    else:
        # Fallback: use Ollama with default embedding model
        from langchain_ollama import OllamaEmbeddings
        return OllamaEmbeddings(model="nomic-embed-text", base_url=settings.OLLAMA_BASE_URL)


async def _async_get_active_model(track: str) -> Optional[str]:
    """Check ModelRegistry for a promoted fine-tuned model.

    Returns None (best-effort) if the registry is unreachable.
    """
    try:
        from app.services.model_registry import ModelRegistry
        return await ModelRegistry.get_active_model(track)
    except Exception:
        return None
