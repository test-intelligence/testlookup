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
    _base_url = _effective.get("base_url")
    _offline = _effective.get("offline_mode", settings.AI_OFFLINE_MODE)

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
        return ChatOllama(
            model=_model,
            base_url=_base_url or settings.OLLAMA_BASE_URL,
            temperature=_temperature,
            num_predict=_max_tokens,
        )

    elif _provider == "lmstudio":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(  # type: ignore
            model=_model,
            base_url=_base_url or settings.LMSTUDIO_BASE_URL,
            api_key="lm-studio",  # type: ignore
            temperature=_temperature,
            max_tokens=_max_tokens,
        )

    elif _provider == "localai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(  # type: ignore
            model=_model,
            base_url=_base_url or settings.LOCALAI_BASE_URL,
            api_key="localai",  # type: ignore
            temperature=_temperature,
            max_tokens=_max_tokens,
        )

    elif _provider == "vllm":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(  # type: ignore
            model=_model,
            base_url=_base_url or settings.VLLM_BASE_URL,
            api_key="vllm",  # type: ignore
            temperature=_temperature,
            max_tokens=_max_tokens,
        )

    elif _provider == "openai":
        if _offline:
            raise ValueError("AI_OFFLINE_MODE=true but LLM_PROVIDER=openai — refusing to call external API")
        _api_key = _effective.get("openai_api_key") or settings.OPENAI_API_KEY
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(  # type: ignore
            model=_model,
            api_key=_api_key,  # type: ignore
            **({"base_url": _base_url} if _base_url else {}),
            temperature=_temperature,
            max_tokens=_max_tokens,
        )

    elif _provider == "gemini":
        if _offline:
            raise ValueError("AI_OFFLINE_MODE=true but LLM_PROVIDER=gemini — refusing to call external API")
        _api_key = _effective.get("google_api_key") or settings.GOOGLE_API_KEY
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(  # type: ignore
            model=_model,
            google_api_key=_api_key,
            temperature=_temperature,
        )

    elif _provider == "anthropic":
        if _offline:
            raise ValueError("AI_OFFLINE_MODE=true but LLM_PROVIDER=anthropic — refusing to call external API")
        _api_key = _effective.get("anthropic_api_key") or getattr(settings, "ANTHROPIC_API_KEY", None)
        if not _api_key:
            raise ValueError("Anthropic API key not configured. Set it in Settings > AI Configuration.")
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(  # type: ignore
            model=_model,
            api_key=_api_key,
            temperature=_temperature,
            max_tokens=_max_tokens,
        )

    else:
        raise ValueError(f"Unknown LLM provider: '{_provider}'. "
                         f"Supported: ollama, lmstudio, localai, vllm, openai, gemini, anthropic")


def get_embedding_model():
    """Return a LangChain embedding model for semantic search."""
    provider = settings.EMBEDDING_PROVIDER.lower()
    model = settings.EMBEDDING_MODEL

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
