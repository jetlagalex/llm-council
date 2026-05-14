"""MemPalace-backed persistent memory for the LLM Council.

All mempalace imports are deferred inside ensure_initialized() so the app
starts cleanly even before `uv sync` has pulled in the dependency.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _slugify(model_id: str) -> str:
    """Convert 'openai/gpt-5.1' → 'councilor-openai-gpt-5-1'."""
    slug = re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")
    return f"councilor-{slug}"


@dataclass
class MemoryHit:
    query: str
    answer: str
    score: float
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "answer": self.answer,
            "score": self.score,
            "metadata": self.metadata,
        }


class CouncilMemory:
    """
    Wraps MemPalace for LLM Council memory.

    Wings used:
    - user-queries  : every raw user prompt (primary retrieval target)
    - chairman      : every Stage 3 chairman answer (fetched as linked answer)
    - councilor-*   : per-model Stage 1 responses (diary only, not retrieved)
    """

    def __init__(
        self,
        enabled: bool,
        palace_path: str,
        top_k: int,
        max_answer_chars: int,
    ):
        self.enabled = enabled
        self.palace_path = str(palace_path)
        self.top_k = top_k
        self.max_answer_chars = max_answer_chars
        self._collection = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def ensure_initialized(self) -> None:
        """Called once on app startup (run inside asyncio.to_thread)."""
        if not self.enabled:
            return
        try:
            Path(self.palace_path).mkdir(parents=True, exist_ok=True)
            from mempalace.palace import get_collection  # noqa: PLC0415
            self._collection = get_collection(self.palace_path, create=True)
            logger.info(
                "memory_initialized",
                extra={"palace_path": self.palace_path, "count": self._collection.count()},
            )
        except Exception as exc:
            logger.error("memory_init_failed: %s", exc)
            self._collection = None

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def retrieve(self, query: str) -> List[MemoryHit]:
        """Return semantically relevant prior Q+A pairs for the given query."""
        if not self.enabled or self._collection is None:
            return []
        return await asyncio.to_thread(self._sync_retrieve, query)

    async def save_turn(
        self,
        conversation_id: str,
        turn_index: int,
        user_query: str,
        councilor_responses: Dict[str, str],
        chairman_response: str,
    ) -> None:
        """Persist a completed council turn into the palace."""
        if not self.enabled or self._collection is None:
            return
        await asyncio.to_thread(
            self._sync_save_turn,
            conversation_id,
            turn_index,
            user_query,
            councilor_responses,
            chairman_response,
        )

    # ------------------------------------------------------------------
    # Synchronous internals (run in thread pool)
    # ------------------------------------------------------------------

    def _sync_retrieve(self, query: str) -> List[MemoryHit]:
        try:
            result = self._collection.query(
                query_texts=[query],
                n_results=self.top_k,
                where={"wing": {"$in": ["user-queries", "chairman"]}},
                include=["documents", "metadatas", "distances"],
            )
            return self._parse_hits(result)
        except Exception as exc:
            logger.warning("memory_retrieve_failed: %s", exc)
            return []

    def _parse_hits(self, result) -> List[MemoryHit]:
        ids = result.ids[0] if result.ids else []
        docs = result.documents[0] if result.documents else []
        metas = result.metadatas[0] if result.metadatas else []
        distances = result.distances[0] if result.distances else []

        hits: List[MemoryHit] = []
        seen_turns: set = set()

        for doc, meta, dist in zip(docs, metas, distances):
            if meta is None:
                meta = {}
            turn_key = (meta.get("conversation_id", ""), meta.get("turn_index", -1))
            if turn_key in seen_turns:
                continue
            seen_turns.add(turn_key)

            # ChromaDB cosine distance ∈ [0, 2]; convert to similarity ∈ [-1, 1].
            score = max(0.0, 1.0 - float(dist))
            wing = meta.get("wing", "")

            if wing == "user-queries":
                query_text = doc or ""
                answer_text = self._fetch_doc(meta.get("linked_chairman_id")) or ""
            elif wing == "chairman":
                answer_text = doc or ""
                query_text = self._fetch_doc(meta.get("linked_user_id")) or ""
            else:
                continue

            hits.append(MemoryHit(
                query=query_text,
                answer=answer_text,
                score=score,
                metadata=meta,
            ))

        return hits

    def _fetch_doc(self, doc_id: Optional[str]) -> Optional[str]:
        """Fetch a single document from the palace by ID."""
        if not doc_id or self._collection is None:
            return None
        try:
            result = self._collection.get(ids=[doc_id], include=["documents"])
            docs = result.documents
            return docs[0] if docs else None
        except Exception:
            return None

    def _sync_save_turn(
        self,
        conversation_id: str,
        turn_index: int,
        user_query: str,
        councilor_responses: Dict[str, str],
        chairman_response: str,
    ) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        user_id = f"{conversation_id}-{turn_index}-user"
        chairman_id = f"{conversation_id}-{turn_index}-chairman"

        try:
            # User query — primary retrieval anchor
            self._collection.upsert(
                documents=[user_query],
                ids=[user_id],
                metadatas=[{
                    "wing": "user-queries",
                    "room": "queries",
                    "conversation_id": conversation_id,
                    "turn_index": turn_index,
                    "role": "user",
                    "timestamp": timestamp,
                    "linked_chairman_id": chairman_id,
                }],
            )

            # Per-councilor Stage 1 responses (diary wings, not retrieved)
            for model_id, response in councilor_responses.items():
                wing = _slugify(model_id)
                self._collection.upsert(
                    documents=[response],
                    ids=[f"{conversation_id}-{turn_index}-{wing}"],
                    metadatas=[{
                        "wing": wing,
                        "room": "stage1",
                        "conversation_id": conversation_id,
                        "turn_index": turn_index,
                        "role": "councilor",
                        "model": model_id,
                        "timestamp": timestamp,
                    }],
                )

            # Chairman final answer — fetched as linked answer during retrieval
            self._collection.upsert(
                documents=[chairman_response],
                ids=[chairman_id],
                metadatas=[{
                    "wing": "chairman",
                    "room": "answers",
                    "conversation_id": conversation_id,
                    "turn_index": turn_index,
                    "role": "chairman",
                    "timestamp": timestamp,
                    "linked_user_id": user_id,
                }],
            )
            logger.info(
                "memory_turn_saved",
                extra={"conversation_id": conversation_id, "turn_index": turn_index},
            )
        except Exception as exc:
            logger.error("memory_save_failed: %s", exc)


# ------------------------------------------------------------------
# Formatting
# ------------------------------------------------------------------

def format_memory_block(hits: List[MemoryHit], max_answer_chars: int) -> str:
    """Format retrieved hits into the RELEVANT MEMORY prompt block.

    Returns an empty string when hits is empty so callers can inject
    the block with a simple ``if memory_context:`` guard.
    """
    if not hits:
        return ""
    lines = [
        "## RELEVANT MEMORY FROM PRIOR COUNCIL SESSIONS\n",
        "The following past exchanges may be relevant. Use them as context; do not",
        "treat them as authoritative. The user has not necessarily re-asked these",
        "questions — they are surfaced by semantic similarity.\n",
    ]
    for i, hit in enumerate(hits, 1):
        answer = hit.answer
        if len(answer) > max_answer_chars:
            answer = answer[:max_answer_chars].rstrip() + "…"
        lines.append(f"[{i}] Past user query: {hit.query}")
        lines.append(f"    Council's final answer: {answer}\n")
    lines.append("---")
    return "\n".join(lines)
