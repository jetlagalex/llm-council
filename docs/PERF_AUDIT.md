# Performance & Reliability Audit

## Executive Summary
This document details the audit and optimization work performed on the LLM Council application. The primary focus was to resolve blocking I/O operations in the asynchronous backend, improve frontend rendering efficiency, and ensure robust resource management.

## 1. Backend Optimization (Phase 1)

### Blocking I/O Elimination
*   **Issue**: The `storage.py` module relied on synchronous `sqlite3` and `json` operations. When called from `async` FastAPI endpoints, these operations blocked the main event loop, causing severe latency under load and preventing concurrent request processing.
*   **Resolution**:
    *   Refactored `storage.py` to expose `async` counterparts for all database interactions (e.g., `create_conversation` -> `await create_conversation`).
    *   Wrapped synchronous blocking calls using `asyncio.to_thread` to offload execution to a thread pool.
    *   Updated `main.py` to `await` all storage interactions.

### Resource Management
*   **Issue**: The `openrouter.py` module created a `httpx.AsyncClient` without explicit timeouts or connection limits, risking indefinite hangs and resource exhaustion.
*   **Resolution**:
    *   Configured `httpx.AsyncClient` with explicit timeouts (connect=10s, read=40s) and connection limits (max=40, keepalive=20).
    *   Ensured proper client reuse and lifecycle management.

### Async Settings Retrieval
*   **Issue**: `openrouter.py` was calling the synchronous `get_settings()` wrapper inside async functions, blocking the loop.
*   **Resolution**: Updated to use `await get_settings_async()` for non-blocking configuration access.

## 2. Frontend Optimization (Phase 2)

### Rendering Efficiency
*   **Issue**: The `Sidebar` component was re-rendering unnecessarily during high-frequency chat streaming events, as its props were not stable.
*   **Resolution**:
    *   Wrapped `Sidebar` in `React.memo`.
    *   Wrapped all handler functions in `App.jsx` passed to `Sidebar` and `ChatInterface` with `useCallback` to ensure prop stability.

### UX Improvements
*   **Optimistic Updates**: The chat interface continues to use optimistic local state updates while streaming, ensuring immediate feedback.

## 3. Tooling & Maintenance

### Linting
*   Added `ruff` configuration to `pyproject.toml` to enforce code quality standards.

### Recommendations
*   **Database**: For higher scale, migrate from SQLite to PostgreSQL to leverage native async drivers (`asyncpg`) and handle higher concurrency.
*   **Caching**: Implement Redis caching for `get_settings` and `get_council` if they become read-heavy hot paths.
*   **Testing**: Add unit tests for `storage.py` to ensure data integrity during refactoring.
