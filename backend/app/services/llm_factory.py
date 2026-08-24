"""
Provider-agnostic LLM factory.
Switch between Ollama (offline), OpenAI, Gemini, or any compatible provider
by changing the LLM_PROVIDER environment variable — no agent code changes needed.
"""
import logging
from typing import Optional

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

    def __init__(self, inner: BaseChatModel, *, provider: str = "unknown", model: str = "unknown"):
        self._inner = inner
        self._provider = provider
        self._model = model

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
    def _record_usage(result) -> None:
        from app.services.pipeline_budget_service import get_pipeline_budget_context

        context = get_pipeline_budget_context()
        if context is None:
            return
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

    async def ainvoke(self, *args, **kwargs):
        self._check()
        args, kwargs = self._prepare_invocation(args, kwargs)
        result = await self._inner.ainvoke(*args, **kwargs)
        self._record_usage(result)
        return result

    def invoke(self, *args, **kwargs):
        self._check()
        args, kwargs = self._prepare_invocation(args, kwargs)
        result = self._inner.invoke(*args, **kwargs)
        self._record_usage(result)
        return result

    def bind(self, *args, **kwargs):
        return BudgetedLLM(
            self._inner.bind(*args, **kwargs),
            provider=self._provider,
            model=self._model,
        )

    def with_structured_output(self, *args, **kwargs):
        return BudgetedLLM(
            self._inner.with_structured_output(*args, **kwargs),
            provider=self._provider,
            model=self._model,
        )

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _budgeted(model: BaseChatModel, *, provider: str, model_name: str) -> BudgetedLLM:
    return BudgetedLLM(model, provider=provider, model=model_name)


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
    from app.services.llm_policy_service import enforce_provider_policy

    enforce_provider_policy(_provider, offline=bool(_offline), base_url=_base_url)

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
        return _budgeted(ChatOllama(
            model=_model,
            base_url=_base_url or settings.OLLAMA_BASE_URL,
            temperature=_temperature,
            # num_predict caps OUTPUT; num_ctx is the CONTEXT window. Leaving
            # num_ctx unset let Ollama apply its own default and truncate long
            # prompts server-side with no error -- silent evidence loss (F-5).
            num_predict=_max_tokens,
            num_ctx=settings.OLLAMA_NUM_CTX,
        ), provider=_provider, model_name=_model)

    elif _provider == "lmstudio":
        from langchain_openai import ChatOpenAI
        return _budgeted(ChatOpenAI(  # type: ignore
            model=_model,
            base_url=_base_url or settings.LMSTUDIO_BASE_URL,
            api_key="lm-studio",  # type: ignore
            temperature=_temperature,
            max_tokens=_max_tokens,
        ), provider=_provider, model_name=_model)

    elif _provider == "localai":
        from langchain_openai import ChatOpenAI
        return _budgeted(ChatOpenAI(  # type: ignore
            model=_model,
            base_url=_base_url or settings.LOCALAI_BASE_URL,
            api_key="localai",  # type: ignore
            temperature=_temperature,
            max_tokens=_max_tokens,
        ), provider=_provider, model_name=_model)

    elif _provider == "vllm":
        from langchain_openai import ChatOpenAI
        return _budgeted(ChatOpenAI(  # type: ignore
            model=_model,
            base_url=_base_url or settings.VLLM_BASE_URL,
            api_key="vllm",  # type: ignore
            temperature=_temperature,
            max_tokens=_max_tokens,
        ), provider=_provider, model_name=_model)

    elif _provider == "openai":
        if _offline:
            raise ValueError("AI_OFFLINE_MODE=true but LLM_PROVIDER=openai — refusing to call external API")
        _api_key = _effective.get("openai_api_key") or settings.OPENAI_API_KEY
        from langchain_openai import ChatOpenAI
        return _budgeted(ChatOpenAI(  # type: ignore
            model=_model,
            api_key=_api_key,  # type: ignore
            **({"base_url": _base_url} if _base_url else {}),
            temperature=_temperature,
            max_tokens=_max_tokens,
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
        return OllamaEmbeddings(
            model=model,
            base_url=settings.OLLAMA_BASE_URL,
        )
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
