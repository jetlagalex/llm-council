"""Shared constants and defaults for the LLM Council backend."""

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class ModelDefaults:
    """Immutable defaults for model selection and routing."""

    council_models: Tuple[str, ...] = (
        "openai/gpt-5.1",
        "google/gemini-3-pro-preview",
        "anthropic/claude-sonnet-4.5",
        "x-ai/grok-4",
    )
    chairman_model: str = "google/gemini-3-pro-preview"
    title_model: str = "google/gemini-2.5-flash"
    openrouter_api_url: str = "https://openrouter.ai/api/v1/chat/completions"


@dataclass(frozen=True)
class RequestLimits:
    """Tunables for request concurrency, retries, and history sizing."""

    max_context_messages: int = 8
    max_summary_messages: int = 6
    max_history_buffer: int = 100
    max_concurrent_requests: int = 4
    request_timeout: float = 120.0
    title_timeout: float = 30.0
    retry_attempts: int = 3
    retry_backoff_base: float = 1.0
    retry_jitter: float = 0.35
