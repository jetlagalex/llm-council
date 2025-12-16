"""OpenRouter API client for making LLM requests."""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Any, Dict, Optional, Sequence

import httpx

from .config import (
    MAX_CONCURRENT_REQUESTS,
    OPENROUTER_API_KEY,
    OPENROUTER_API_URL,
    REQUEST_TIMEOUT,
    RETRY_ATTEMPTS,
    RETRY_BACKOFF_BASE,
    RETRY_JITTER,
)
from .storage import get_settings_async
from .utils import retry_with_backoff

logger = logging.getLogger(__name__)

_async_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()
_request_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    return isinstance(exc, (httpx.TimeoutException, httpx.RequestError))


async def get_async_client() -> httpx.AsyncClient:
    """Return a shared AsyncClient with connection pooling."""
    global _async_client
    if _async_client and not _async_client.is_closed:
        return _async_client

    async with _client_lock:
        if _async_client and not _async_client.is_closed:
            return _async_client
        # Configure robust limits
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=40)
        timeout = httpx.Timeout(connect=10.0, read=40.0, write=10.0, pool=5.0)
        _async_client = httpx.AsyncClient(limits=limits, timeout=timeout)
        return _async_client


async def close_async_client() -> None:
    """Close the shared AsyncClient (used on application shutdown)."""
    global _async_client
    if _async_client and not _async_client.is_closed:
        await _async_client.aclose()
    _async_client = None


async def query_model(
    model: str,
    messages: Sequence[Dict[str, str]],
    timeout: float = REQUEST_TIMEOUT,
) -> Optional[Dict[str, Any]]:
    """
    Query a single model via OpenRouter API.

    Args:
        model: OpenRouter model identifier (e.g., "openai/gpt-4o")
        messages: List of message dicts with 'role' and 'content'
        timeout: Request timeout in seconds

    Returns:
        Response dict with 'content' and optional 'reasoning_details', or None if failed
    """
    settings = await get_settings_async()
    api_key = settings.get("openrouter_api_key") or OPENROUTER_API_KEY

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": messages,
    }

    start_time = perf_counter()

    async def _post_request() -> Dict[str, Any]:
        client = await get_async_client()
        response = await client.post(
            OPENROUTER_API_URL,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()

        data = response.json()
        message = data["choices"][0]["message"]

        return {
            "content": message.get("content"),
            "reasoning_details": message.get("reasoning_details"),
        }

    try:
        async with _request_semaphore:
            return await retry_with_backoff(
                _post_request,
                retries=RETRY_ATTEMPTS,
                base_delay=RETRY_BACKOFF_BASE,
                jitter=RETRY_JITTER,
                exceptions=(
                    httpx.RequestError,
                    httpx.HTTPStatusError,
                    httpx.TimeoutException,
                ),
                operation_name=f"query_model:{model}",
                should_retry=_is_retryable,
            )
    except Exception as exc:  # pragma: no cover - defensive log wrapper
        logger.exception("error querying model", extra={"model": model, "error": str(exc)})
        return None
    finally:
        elapsed_ms = int((perf_counter() - start_time) * 1000)
        logger.info(
            "model request finished",
            extra={"model": model, "elapsed_ms": elapsed_ms},
        )


async def query_models_parallel(
    models: Sequence[str],
    messages: Sequence[Dict[str, str]],
) -> Dict[str, Optional[Dict[str, Any]]]:
    """
    Query multiple models in parallel.

    Args:
        models: List of OpenRouter model identifiers
        messages: List of message dicts to send to each model

    Returns:
        Dict mapping model identifier to response dict (or None if failed)
    """
    tasks = [asyncio.create_task(query_model(model, messages)) for model in models]
    responses = await asyncio.gather(*tasks)
    return {model: response for model, response in zip(models, responses)}
