"""SQLite storage for conversations."""

import asyncio
import json
import sqlite3
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

from .config import DATA_DIR, DB_PATH, MAX_HISTORY_BUFFER


def _connect():
    return sqlite3.connect(DB_PATH)


def _sync_ensure_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                title TEXT NOT NULL,
                last_interacted_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                stage1 TEXT,
                stage2 TEXT,
                stage3 TEXT,
                metadata TEXT,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id)
            )
            """
        )
        # Backfill the last_interacted_at column for pre-existing databases.
        cur = conn.execute("PRAGMA table_info(conversations)")
        columns = {row[1] for row in cur.fetchall()}
        if "last_interacted_at" not in columns:
            conn.execute("ALTER TABLE conversations ADD COLUMN last_interacted_at TEXT")
            conn.execute(
                """
                UPDATE conversations
                SET last_interacted_at = created_at
                WHERE last_interacted_at IS NULL
                """
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS council_profiles (
                key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                council_models TEXT NOT NULL,
                chairman_model TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_council (
                conversation_id TEXT PRIMARY KEY,
                council_key TEXT NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id)
            )
            """
        )


# Initialize database on module import (blocking is okay here)
_sync_ensure_db()


def _sync_create_conversation(conversation_id: str, council_key: Optional[str] = None) -> Dict[str, Any]:
    created_at = datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO conversations (id, created_at, title, last_interacted_at)
            VALUES (?, ?, ?, ?)
            """,
            (conversation_id, created_at, "New Conversation", created_at),
        )
        if council_key:
            conn.execute(
                """
                INSERT OR REPLACE INTO conversation_council (conversation_id, council_key)
                VALUES (?, ?)
                """,
                (conversation_id, council_key),
            )

    return {
        "id": conversation_id,
        "created_at": created_at,
        "title": "New Conversation",
        "last_interacted_at": created_at,
        "messages": [],
    }


async def create_conversation(conversation_id: str, council_key: Optional[str] = None) -> Dict[str, Any]:
    """Create and persist a new conversation."""
    return await asyncio.to_thread(_sync_create_conversation, conversation_id, council_key)


def _row_to_message(row) -> Dict[str, Any]:
    """Convert DB row to API message shape."""
    _, _, created_at, role, content, stage1, stage2, stage3, metadata = row
    message: Dict[str, Any] = {"role": role, "created_at": created_at}
    if role == "user":
        message["content"] = content
    else:
        message["stage1"] = json.loads(stage1) if stage1 else None
        message["stage2"] = json.loads(stage2) if stage2 else None
        message["stage3"] = json.loads(stage3) if stage3 else None
        if metadata:
            message["metadata"] = json.loads(metadata)
    return message


def _sync_set_conversation_council(conversation_id: str, council_key: str):
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO conversation_council (conversation_id, council_key)
            VALUES (?, ?)
            """,
            (conversation_id, council_key),
        )


async def set_conversation_council(conversation_id: str, council_key: str):
    """Link a conversation to a chosen council profile."""
    await asyncio.to_thread(_sync_set_conversation_council, conversation_id, council_key)


def _sync_get_conversation_council(conversation_id: str) -> Optional[str]:
    with _connect() as conn:
        cur = conn.execute(
            "SELECT council_key FROM conversation_council WHERE conversation_id = ?",
            (conversation_id,),
        )
        row = cur.fetchone()
    return row[0] if row else None


async def get_conversation_council(conversation_id: str) -> Optional[str]:
    """Fetch the council key associated with a conversation."""
    return await asyncio.to_thread(_sync_get_conversation_council, conversation_id)


def _sync_list_councils() -> List[Dict[str, Any]]:
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT key, name, council_models, chairman_model
            FROM council_profiles
            ORDER BY name COLLATE NOCASE ASC
            """
        )
        rows = cur.fetchall()
    return [
        {
            "key": row[0],
            "name": row[1],
            "council_models": json.loads(row[2]),
            "chairman_model": row[3],
        }
        for row in rows
    ]


async def list_councils() -> List[Dict[str, Any]]:
    """Return all saved council profiles."""
    return await asyncio.to_thread(_sync_list_councils)


def _sync_get_council(key: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT key, name, council_models, chairman_model
            FROM council_profiles
            WHERE key = ?
            """,
            (key,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "key": row[0],
        "name": row[1],
        "council_models": json.loads(row[2]),
        "chairman_model": row[3],
    }


async def get_council(key: str) -> Optional[Dict[str, Any]]:
    """Return a single council profile by key."""
    return await asyncio.to_thread(_sync_get_council, key)


def _sync_upsert_council(key: str, name: str, council_models: List[str], chairman_model: str):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO council_profiles (key, name, council_models, chairman_model)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                name=excluded.name,
                council_models=excluded.council_models,
                chairman_model=excluded.chairman_model
            """,
            (key, name, json.dumps(council_models), chairman_model),
        )


async def upsert_council(key: str, name: str, council_models: List[str], chairman_model: str):
    """Create or update a council profile."""
    await asyncio.to_thread(_sync_upsert_council, key, name, council_models, chairman_model)


def _sync_delete_council(key: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM council_profiles WHERE key = ?", (key,))
        return cur.rowcount > 0


async def delete_council(key: str) -> bool:
    """Remove a council profile."""
    return await asyncio.to_thread(_sync_delete_council, key)


def _sync_conversation_uses_council(key: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "SELECT 1 FROM conversation_council WHERE council_key = ? LIMIT 1",
            (key,),
        )
        return cur.fetchone() is not None


async def conversation_uses_council(key: str) -> bool:
    """Check if any conversation is linked to the given council key."""
    return await asyncio.to_thread(_sync_conversation_uses_council, key)


def _sync_get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        cur = conn.execute(
            "SELECT id, created_at, title, last_interacted_at FROM conversations WHERE id = ?",
            (conversation_id,),
        )
        conv = cur.fetchone()
        if not conv:
            return None
        last_interacted_at = datetime.utcnow().isoformat()
        conn.execute(
            """
            UPDATE conversations
            SET last_interacted_at = ?
            WHERE id = ?
            """,
            (last_interacted_at, conversation_id),
        )
        council_cur = conn.execute(
            "SELECT council_key FROM conversation_council WHERE conversation_id = ?",
            (conversation_id,),
        )
        council_row = council_cur.fetchone()

        messages_cur = conn.execute(
            """
            SELECT id, conversation_id, created_at, role, content, stage1, stage2, stage3, metadata
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (conversation_id,),
        )
        messages = deque(
            (_row_to_message(row) for row in messages_cur.fetchall()),
            maxlen=MAX_HISTORY_BUFFER,
        )

    return {
        "id": conv[0],
        "created_at": conv[1],
        "title": conv[2],
        "last_interacted_at": last_interacted_at,
        "council_key": council_row[0] if council_row else None,
        "messages": list(messages),
    }


async def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    """Load a conversation with all messages."""
    return await asyncio.to_thread(_sync_get_conversation, conversation_id)


def _sync_touch_conversation(conversation_id: str, when: Optional[str] = None) -> Optional[str]:
    """Update the last_interacted_at timestamp for a conversation."""
    timestamp = when or datetime.utcnow().isoformat()
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE conversations
            SET last_interacted_at = ?
            WHERE id = ?
            """,
            (timestamp, conversation_id),
        )
    return timestamp if cur.rowcount else None


async def touch_conversation(conversation_id: str, when: Optional[str] = None) -> Optional[str]:
    """Async wrapper to mark a conversation as recently interacted with."""
    return await asyncio.to_thread(_sync_touch_conversation, conversation_id, when)


def save_conversation(_: Dict[str, Any]):
    """No-op retained for compatibility."""
    return None


def _sync_get_settings() -> Dict[str, Any]:
    # Lazy import avoids circular references when config pulls from storage later.
    from .config import OPENROUTER_API_KEY, COUNCIL_MODELS, CHAIRMAN_MODEL, AVAILABLE_MODELS

    defaults = {
        "openrouter_api_key": OPENROUTER_API_KEY or "",
        "council_models": COUNCIL_MODELS,
        "chairman_model": CHAIRMAN_MODEL,
        "available_models": AVAILABLE_MODELS,
    }

    with _connect() as conn:
        cur = conn.execute(
            "SELECT value FROM settings WHERE key = 'core'"
        )
        row = cur.fetchone()

    if not row:
        return defaults

    try:
        saved = json.loads(row[0])
    except Exception:
        return defaults

    # Merge defaults with saved values, preferring saved when present
    merged = {
        "openrouter_api_key": saved.get("openrouter_api_key", defaults["openrouter_api_key"]),
        "council_models": saved.get("council_models", defaults["council_models"]),
        "chairman_model": saved.get("chairman_model", defaults["chairman_model"]),
        "available_models": saved.get("available_models", defaults["available_models"]),
    }
    return merged


def get_settings() -> Dict[str, Any]:
    """
    Load persisted settings.
    NOTE: Using a sync wrapper for now because this is often called in non-async contexts
    (like config loading) or where async conversion is tricky.
    Use get_settings_async where possible.
    """
    return _sync_get_settings()


async def get_settings_async() -> Dict[str, Any]:
    """Async version of get_settings."""
    return await asyncio.to_thread(_sync_get_settings)


def _sync_ensure_default_council(settings: Dict[str, Any]):
    existing = _sync_get_council("default")
    if existing:
        return existing

    models = settings.get("council_models")
    chair = settings.get("chairman_model")
    # Fallback if settings are empty for any reason.
    if not models:
        from .config import COUNCIL_MODELS as DEFAULT_MODELS
        models = DEFAULT_MODELS
    if not chair:
        from .config import CHAIRMAN_MODEL as DEFAULT_CHAIR
        chair = DEFAULT_CHAIR

    _sync_upsert_council(
        "default",
        "General",
        models,
        chair,
    )
    return _sync_get_council("default")


async def ensure_default_council(settings: Dict[str, Any]):
    """
    Make sure a baseline council profile exists using the provided settings.
    """
    return await asyncio.to_thread(_sync_ensure_default_council, settings)


def _sync_update_settings(settings: Dict[str, Any]):
    with _connect() as conn:
        conn.execute(
            "REPLACE INTO settings (key, value) VALUES ('core', ?)",
            (json.dumps(settings),),
        )


async def update_settings(settings: Dict[str, Any]):
    """Persist settings (overwrites the single settings row)."""
    await asyncio.to_thread(_sync_update_settings, settings)


def _sync_list_conversations() -> List[Dict[str, Any]]:
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT c.id, c.created_at, c.title, c.last_interacted_at, COUNT(m.id) as message_count, cc.council_key
            FROM conversations c
            LEFT JOIN messages m ON c.id = m.conversation_id
            LEFT JOIN conversation_council cc ON cc.conversation_id = c.id
            GROUP BY c.id
            ORDER BY COALESCE(c.last_interacted_at, c.created_at) DESC
            """
        )
        rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "created_at": row[1],
            "title": row[2],
            "last_interacted_at": row[3] or row[1],
            "message_count": row[4],
            "council_key": row[5] or "default",
        }
        for row in rows
    ]


async def list_conversations() -> List[Dict[str, Any]]:
    """List conversation metadata with message counts."""
    return await asyncio.to_thread(_sync_list_conversations)


def _sync_add_user_message(conversation_id: str, content: str):
    created_at = datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO messages (conversation_id, created_at, role, content)
            VALUES (?, ?, ?, ?)
            """,
            (conversation_id, created_at, "user", content),
        )
        conn.execute(
            """
            UPDATE conversations
            SET last_interacted_at = ?
            WHERE id = ?
            """,
            (created_at, conversation_id),
        )


async def add_user_message(conversation_id: str, content: str):
    """Persist a user message."""
    await asyncio.to_thread(_sync_add_user_message, conversation_id, content)


def _sync_add_assistant_message(
    conversation_id: str,
    stage1: List[Dict[str, Any]],
    stage2: List[Dict[str, Any]],
    stage3: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
):
    created_at = datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO messages (conversation_id, created_at, role, stage1, stage2, stage3, metadata)
            VALUES (?, ?, 'assistant', ?, ?, ?, ?)
            """,
            (
                conversation_id,
                created_at,
                json.dumps(stage1),
                json.dumps(stage2),
                json.dumps(stage3),
                json.dumps(metadata) if metadata else None,
            ),
        )
        conn.execute(
            """
            UPDATE conversations
            SET last_interacted_at = ?
            WHERE id = ?
            """,
            (created_at, conversation_id),
        )


async def add_assistant_message(
    conversation_id: str,
    stage1: List[Dict[str, Any]],
    stage2: List[Dict[str, Any]],
    stage3: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
):
    """Persist an assistant message with all stages."""
    await asyncio.to_thread(_sync_add_assistant_message, conversation_id, stage1, stage2, stage3, metadata)


def _sync_update_conversation_title(conversation_id: str, title: str):
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id)
        )
        return cur.rowcount > 0


async def update_conversation_title(conversation_id: str, title: str):
    """Update the title of a conversation."""
    return await asyncio.to_thread(_sync_update_conversation_title, conversation_id, title)


def _sync_delete_conversation(conversation_id: str) -> bool:
    with _connect() as conn:
        conn.execute(
            "DELETE FROM messages WHERE conversation_id = ?", (conversation_id,)
        )
        conn.execute(
            "DELETE FROM conversation_council WHERE conversation_id = ?", (conversation_id,)
        )
        cur = conn.execute(
            "DELETE FROM conversations WHERE id = ?", (conversation_id,)
        )
        return cur.rowcount > 0


async def delete_conversation(conversation_id: str) -> bool:
    """Delete a conversation and all of its messages."""
    return await asyncio.to_thread(_sync_delete_conversation, conversation_id)
