"""Utility helpers for reliability and instrumentation."""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Tuple, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)


async def retry_with_backoff(
    operation: Callable[[], Awaitable[T]],
    *,
    retries: int,
    base_delay: float,
    jitter: float,
    exceptions: Tuple[type[BaseException], ...],
    operation_name: str = "operation",
    should_retry: Callable[[BaseException], bool] | None = None,
) -> T:
    """
    Retry an async operation with exponential backoff and jitter.

    Args:
        operation: Coroutine factory to execute.
        retries: Maximum attempts before surfacing the exception.
        base_delay: Initial delay in seconds before exponential growth.
        jitter: Random jitter applied to each delay.
        exceptions: Exception types that trigger a retry.
        operation_name: Label for logging/diagnostics.
        should_retry: Optional predicate to short-circuit retries for certain exceptions.
    """
    last_exception: BaseException | None = None
    for attempt in range(retries):
        try:
            return await operation()
        except exceptions as exc:  # type: ignore[misc]
            if should_retry and not should_retry(exc):
                raise
            last_exception = exc
            if attempt >= retries - 1:
                break
            delay = base_delay * (2**attempt) + random.uniform(0, jitter)
            logger.warning(
                "retrying %s after failure",
                operation_name,
                extra={
                    "attempt": attempt + 1,
                    "delay_seconds": round(delay, 3),
                    "exception": exc.__class__.__name__,
                },
            )
            await asyncio.sleep(delay)

    if last_exception:
        raise last_exception
    raise RuntimeError(f"{operation_name} failed without exception")
