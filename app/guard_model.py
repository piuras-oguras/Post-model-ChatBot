from llama_index.llms.openai_like import OpenAILike

from app.config import GuardModelSettings


def build_guard_llm(settings: GuardModelSettings) -> OpenAILike:
    """Wraps any OpenAI-compatible chat endpoint (e.g. local Ollama) as the guard-model judge."""
    return OpenAILike(
        model=settings.name,
        api_base=settings.base_url,
        api_key=settings.api_key,
        is_chat_model=True,
        timeout=settings.request_timeout_seconds,
    )
