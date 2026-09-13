import logging
import secrets
from functools import lru_cache

from fastapi import Depends, FastAPI, Header, HTTPException, status
from llama_index.llms.openai_like import OpenAILike

from app.config import get_settings
from app.guard_model import build_guard_llm
from app.output_guard import (
    GuardModelError,
    OutputGuard,
    OutputGuardAction,
    OutputGuardContext,
    OutputGuardResult,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="post-model-service",
    description=(
        "Standalone post-model output guard (relevance/groundedness/safety/leakage), "
        "extracted verbatim from the ChatBot project."
    ),
    version="0.1.0",
)


@lru_cache
def get_guard_llm() -> OpenAILike:
    """Cached so the guard LLM client is built once per process."""
    return build_guard_llm(get_settings().guard_model)


def require_api_token(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency: enforces Bearer auth only when api_token is configured."""
    settings = get_settings()
    if not settings.api_token:
        return

    scheme, _, token = (authorization or "").partition(" ")
    if (
        scheme.lower() != "bearer"
        or not token
        or not secrets.compare_digest(token, settings.api_token)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/v1/check",
    response_model=OutputGuardResult,
    dependencies=[Depends(require_api_token)],
)
async def check(context: OutputGuardContext) -> OutputGuardResult:
    guard = OutputGuard(get_guard_llm())
    try:
        result = await guard.process(context)
    except GuardModelError:
        # No non-model fallback: a broken guard model must fail loudly (502).
        logger.exception(
            "postmodel.guard_model_failed session_id=%s", context.session_id
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="guard model call failed"
        ) from None

    if result.action != OutputGuardAction.ALLOW:
        logger.warning(
            "postmodel.%s session_id=%s violations=%s",
            result.action,
            context.session_id,
            [v.code for v in result.violations],
        )
    return result
