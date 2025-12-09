"""Configuration for the LLM Council."""

import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv

from .constants import ModelDefaults, RequestLimits

load_dotenv()

DEFAULT_MODELS = ModelDefaults()
REQUEST_LIMITS = RequestLimits()

# OpenRouter API key
OPENROUTER_API_KEY: str | None = os.getenv("OPENROUTER_API_KEY")

# Council members - list of OpenRouter model identifiers (defaults)
COUNCIL_MODELS: List[str] = list(DEFAULT_MODELS.council_models)

# Allow listing the recommended/supported models separately (for UI)
AVAILABLE_MODELS: List[str] = COUNCIL_MODELS

# Chairman model - synthesizes final response
CHAIRMAN_MODEL: str = DEFAULT_MODELS.chairman_model

# Model used for title generation
TITLE_MODEL: str = DEFAULT_MODELS.title_model

# OpenRouter API endpoint
OPENROUTER_API_URL: str = DEFAULT_MODELS.openrouter_api_url

# Request/processing limits
MAX_CONTEXT_MESSAGES: int = REQUEST_LIMITS.max_context_messages
MAX_SUMMARY_MESSAGES: int = REQUEST_LIMITS.max_summary_messages
MAX_HISTORY_BUFFER: int = REQUEST_LIMITS.max_history_buffer
MAX_CONCURRENT_REQUESTS: int = REQUEST_LIMITS.max_concurrent_requests
REQUEST_TIMEOUT: float = REQUEST_LIMITS.request_timeout
TITLE_TIMEOUT: float = REQUEST_LIMITS.title_timeout
RETRY_ATTEMPTS: int = REQUEST_LIMITS.retry_attempts
RETRY_BACKOFF_BASE: float = REQUEST_LIMITS.retry_backoff_base
RETRY_JITTER: float = REQUEST_LIMITS.retry_jitter

# SQLite database for conversation storage
DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "council.sqlite"
