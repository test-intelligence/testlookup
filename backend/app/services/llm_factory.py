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
    _provider = (provider or settings.LLM_PROVIDER).lower()
    _temperature = temperature if temperature is not None else settings.LLM_TEMPERATURE

    # Resolve model name: explicit override > fine-tuned registry > config default
    if model:
        _model = model
    elif track:
        _model = await _async_get_active_model(track) or settings.LLM_MODEL
    else:
        _model = settings.LLM_MODEL

    if track:
        logger.info("Initialising LLM: provider=%s model=%s track=%s", _provider, _model, track)
    else:
        logger.info("Initialising LLM: provider=%s model=%s", _provider, _model)

    if _provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=_model,
            base_url=settings.OLLAMA_BASE_URL,
            temperature=_temperature,
            num_predict=settings.LLM_MAX_TOKENS,
        )

    elif _provider == "lmstudio":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(  # type: ignore
            model=_model,
            base_url=settings.LMSTUDIO_BASE_URL,
            api_key="lm-studio",  # type: ignore
            temperature=_temperature,
            max_tokens=settings.LLM_MAX_TOKENS,
        )

    elif _provider == "localai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(  # type: ignore
            model=_model,
            base_url=settings.LOCALAI_BASE_URL,
            api_key="localai",  # type: ignore
            temperature=_temperature,
            max_tokens=settings.LLM_MAX_TOKENS,
        )

    elif _provider == "vllm":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(  # type: ignore
            model=_model,
            base_url=settings.VLLM_BASE_URL,
            api_key="vllm",  # type: ignore
            temperature=_temperature,
            max_tokens=settings.LLM_MAX_TOKENS,
        )

    elif _provider == "openai":
        if settings.AI_OFFLINE_MODE:
            raise ValueError("AI_OFFLINE_MODE=true but LLM_PROVIDER=openai — refusing to call external API")
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(  # type: ignore
            model=_model,
            api_key=settings.OPENAI_API_KEY,  # type: ignore
            temperature=_temperature,
            max_tokens=settings.LLM_MAX_TOKENS,
        )

    elif _provider == "gemini":
        if settings.AI_OFFLINE_MODE:
            raise ValueError("AI_OFFLINE_MODE=true but LLM_PROVIDER=gemini — refusing to call external API")
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(  # type: ignore
            model=_model,
            google_api_key=settings.GOOGLE_API_KEY,
            temperature=_temperature,
        )

    else:
        raise ValueError(f"Unknown LLM provider: '{_provider}'. "
                         f"Supported: ollama, lmstudio, localai, vllm, openai, gemini")


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
