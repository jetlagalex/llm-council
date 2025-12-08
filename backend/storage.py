"""SQLite storage for conversations."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from .config import DB_PATH, DATA_DIR


def _connect():
    return sqlite3.connect(DB_PATH)


def _ensure_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                title TEXT NOT NULL
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


# Initialize database on module import
_ensure_db()


def create_conversation(conversation_id: str, council_key: Optional[str] = None) -> Dict[str, Any]:
    """Create and persist a new conversation."""
    created_at = datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO conversations (id, created_at, title) VALUES (?, ?, ?)",
            (conversation_id, created_at, "New Conversation"),
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
        "messages": [],
    }


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


def set_conversation_council(conversation_id: str, council_key: str):
    """Link a conversation to a chosen council profile."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO conversation_council (conversation_id, council_key)
            VALUES (?, ?)
            """,
            (conversation_id, council_key),
        )


def get_conversation_council(conversation_id: str) -> Optional[str]:
    """Fetch the council key associated with a conversation."""
    with _connect() as conn:
        cur = conn.execute(
            "SELECT council_key FROM conversation_council WHERE conversation_id = ?",
            (conversation_id,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def list_councils() -> List[Dict[str, Any]]:
    """Return all saved council profiles."""
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


def get_council(key: str) -> Optional[Dict[str, Any]]:
    """Return a single council profile by key."""
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


def upsert_council(key: str, name: str, council_models: List[str], chairman_model: str):
    """Create or update a council profile."""
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


def delete_council(key: str) -> bool:
    """Remove a council profile."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM council_profiles WHERE key = ?", (key,))
        return cur.rowcount > 0


def conversation_uses_council(key: str) -> bool:
    """Check if any conversation is linked to the given council key."""
    with _connect() as conn:
        cur = conn.execute(
            "SELECT 1 FROM conversation_council WHERE council_key = ? LIMIT 1",
            (key,),
        )
        return cur.fetchone() is not None


def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    """Load a conversation with all messages."""
    with _connect() as conn:
        cur = conn.execute(
            "SELECT id, created_at, title FROM conversations WHERE id = ?",
            (conversation_id,),
        )
        conv = cur.fetchone()
        if not conv:
            return None
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
        messages = [_row_to_message(row) for row in messages_cur.fetchall()]

    return {
        "id": conv[0],
        "created_at": conv[1],
        "title": conv[2],
        "council_key": council_row[0] if council_row else None,
        "messages": messages,
    }


def save_conversation(_: Dict[str, Any]):
    """No-op retained for compatibility."""
    return None


def get_settings() -> Dict[str, Any]:
    """Load persisted settings, falling back to defaults and env vars."""
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


def ensure_default_council(settings: Dict[str, Any]):
    """
    Make sure a baseline council profile exists using the provided settings.
    This is idempotent and will not overwrite existing profiles.
    """
    existing = get_council("default")
    if existing:
        return existing

    models = settings.get("council_models")
    chair = settings.get("chairman_model")
    # Fallback if settings are empty for any reason.
    if not models:
        from .config import COUNCIL_MODELS as DEFAULT_MODELS  # Lazy import to avoid cycles
        models = DEFAULT_MODELS
    if not chair:
        from .config import CHAIRMAN_MODEL as DEFAULT_CHAIR
        chair = DEFAULT_CHAIR

    upsert_council(
        "default",
        "General",
        models,
        chair,
    )
    return get_council("default")


def update_settings(settings: Dict[str, Any]):
    """Persist settings (overwrites the single settings row)."""
    with _connect() as conn:
        conn.execute(
            "REPLACE INTO settings (key, value) VALUES ('core', ?)",
            (json.dumps(settings),),
        )


def list_conversations() -> List[Dict[str, Any]]:
    """List conversation metadata with message counts."""
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT c.id, c.created_at, c.title, COUNT(m.id) as message_count, cc.council_key
            FROM conversations c
            LEFT JOIN messages m ON c.id = m.conversation_id
            LEFT JOIN conversation_council cc ON cc.conversation_id = c.id
            GROUP BY c.id
            ORDER BY c.created_at DESC
            """
        )
        rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "created_at": row[1],
            "title": row[2],
            "message_count": row[3],
            "council_key": row[4] or "default",
        }
        for row in rows
    ]


def add_user_message(conversation_id: str, content: str):
    """Persist a user message."""
    created_at = datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO messages (conversation_id, created_at, role, content)
            VALUES (?, ?, ?, ?)
            """,
            (conversation_id, created_at, "user", content),
        )


def add_assistant_message(
    conversation_id: str,
    stage1: List[Dict[str, Any]],
    stage2: List[Dict[str, Any]],
    stage3: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
):
    """Persist an assistant message with all stages."""
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


def update_conversation_title(conversation_id: str, title: str):
    """Update the title of a conversation."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id)
        )
        return cur.rowcount > 0


def delete_conversation(conversation_id: str) -> bool:
    """Delete a conversation and all of its messages."""
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
