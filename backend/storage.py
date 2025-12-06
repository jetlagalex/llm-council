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


# Initialize database on module import
_ensure_db()


def create_conversation(conversation_id: str) -> Dict[str, Any]:
    """Create and persist a new conversation."""
    created_at = datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO conversations (id, created_at, title) VALUES (?, ?, ?)",
            (conversation_id, created_at, "New Conversation"),
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
        "messages": messages,
    }


def save_conversation(_: Dict[str, Any]):
    """No-op retained for compatibility."""
    return None


def get_settings() -> Dict[str, Any]:
    """Load persisted settings, falling back to defaults and env vars."""
    from .config import OPENROUTER_API_KEY, COUNCIL_MODELS, CHAIRMAN_MODEL  # Lazy import to avoid cycles

    defaults = {
        "openrouter_api_key": OPENROUTER_API_KEY or "",
        "council_models": COUNCIL_MODELS,
        "chairman_model": CHAIRMAN_MODEL,
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
    }
    return merged


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
            SELECT c.id, c.created_at, c.title, COUNT(m.id) as message_count
            FROM conversations c
            LEFT JOIN messages m ON c.id = m.conversation_id
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
        cur = conn.execute(
            "DELETE FROM conversations WHERE id = ?", (conversation_id,)
        )
        return cur.rowcount > 0
