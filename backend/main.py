"""FastAPI backend for LLM Council."""

import asyncio
import asyncio.subprocess
import json
import os
import re
import uuid
from collections import deque
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import storage
from .council import (
    calculate_aggregate_rankings,
    generate_conversation_title,
    run_full_council,
    stage1_collect_responses,
    stage2_collect_rankings,
    stage3_synthesize_final,
)
from .config import (
    AVAILABLE_MODELS,
    CHAIRMAN_MODEL,
    COUNCIL_MODELS,
    MAX_HISTORY_BUFFER,
    OPENROUTER_API_KEY,
)
from .openrouter import close_async_client

app = FastAPI(title="LLM Council API")

# Enable CORS. Default to permissive so mobile devices or other hosts (e.g. in
# a proxmox container) can reach the API, but allow tightening via CORS_ORIGINS.
raw_origins = os.environ.get("CORS_ORIGINS")
allow_origins = (
    [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
    if raw_origins
    else ["*"]
)
# When allowing all origins, credentials must be disabled per the CORS spec.
allow_credentials = False if "*" in allow_origins else True

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateConversationRequest(BaseModel):
    """Request to create a new conversation."""
    council_key: Optional[str] = Field(default=None, description="Optional council profile key for the new conversation.")


class UpdateConversationTitleRequest(BaseModel):
    """Request to rename a conversation."""
    title: str


class SendMessageRequest(BaseModel):
    """Request to send a message in a conversation."""
    content: str


class ConversationMetadata(BaseModel):
    """Conversation metadata for list view."""
    id: str
    created_at: str
    title: str
    last_interacted_at: Optional[str] = None
    message_count: int
    council_key: Optional[str] = None


class Conversation(BaseModel):
    """Full conversation with all messages."""
    id: str
    created_at: str
    title: str
    last_interacted_at: Optional[str] = None
    council_key: Optional[str] = None
    messages: List[Dict[str, Any]]


class UpdateStartResponse(BaseModel):
    """Response returned when the updater is successfully launched."""
    status: str
    unit: str
    log_path: str


class SettingsResponse(BaseModel):
    """Current configurable settings."""
    has_openrouter_key: bool
    openrouter_key_last4: Optional[str]
    council_models: List[str]
    chairman_model: str
    available_models: List[str]


class UpdateSettingsRequest(BaseModel):
    """Update settings payload."""
    openrouter_api_key: Optional[str] = Field(
        default=None,
        description="Provide to replace, '' to clear, or null to keep current.",
    )
    council_models: List[str]
    chairman_model: str
    available_models: Optional[List[str]] = Field(
        default=None,
        description="Optional list of available council choices, merged with defaults.",
    )


class CouncilProfile(BaseModel):
    """A named council configuration."""
    key: str
    name: str
    council_models: List[str]
    chairman_model: str


class CreateCouncilRequest(BaseModel):
    """Payload to create a council profile."""
    name: str
    council_models: List[str]
    chairman_model: str
    key: Optional[str] = None


class UpdateCouncilRequest(BaseModel):
    """Payload to update an existing council profile."""
    name: Optional[str] = None
    council_models: Optional[List[str]] = None
    chairman_model: Optional[str] = None


class UpdateConversationCouncilRequest(BaseModel):
    """Assign a council profile to a conversation."""
    council_key: str


@app.on_event("shutdown")
async def shutdown_event() -> None:
    """Ensure outbound HTTP clients are cleaned up."""
    await close_async_client()


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "LLM Council API"}


@app.post("/api/update", response_model=UpdateStartResponse)
async def run_update_script():
    """
    Kick off the update script via systemd-run so it continues after this API
    process stops (the script intentionally restarts services). We return
    immediately with the transient unit name and log path.
    """
    script_path = "/opt/llm-council/update.sh"
    log_path = "/opt/llm-council/update.log"
    if not os.path.exists(script_path):
        raise HTTPException(status_code=404, detail="Update script not found")

    # Launch a transient unit so the script runs outside this service's cgroup.
    unit_name = f"llm-council-update-{uuid.uuid4().hex[:8]}"
    cmd = [
        "systemd-run",
        "--unit", unit_name,
        "--description", "LLM Council self-update",
        "--collect",
        "--property=WorkingDirectory=/opt/llm-council",
        f"--property=StandardOutput=append:{log_path}",
        f"--property=StandardError=append:{log_path}",
        script_path,
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="systemd-run not available on host")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Insufficient permissions to launch update")
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=f"Failed to start update: {exc}")

    if process.returncode != 0:
        detail = stderr.decode().strip() or stdout.decode().strip() or "Unknown error launching update"
        raise HTTPException(status_code=500, detail=detail)

    return {"status": "started", "unit": unit_name, "log_path": log_path}


async def _ensure_settings_ready(council: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Validate that settings contain an API key and at least one model.
    Raises HTTPException if requirements are not met.
    """
    settings = await storage.get_settings_async()
    api_key = settings.get("openrouter_api_key") or OPENROUTER_API_KEY
    if not api_key:
        raise HTTPException(status_code=400, detail="OpenRouter API key not set. Add it in Settings.")

    council_models = (council or settings).get("council_models") or COUNCIL_MODELS
    chairman = (council or settings).get("chairman_model") or CHAIRMAN_MODEL
    if not council_models:
        raise HTTPException(status_code=400, detail="Council models not configured. Update Settings.")
    if chairman not in council_models:
        raise HTTPException(status_code=400, detail="Chairman must be one of the council models.")

    return {
        "api_key": api_key,
        "council_models": council_models,
        "chairman_model": chairman,
    }


def _normalize_models(models: List[str]) -> List[str]:
    """
    Trim, deduplicate, and preserve order for a list of model identifiers.
    Empty entries are discarded so validation only works with real IDs.
    """
    seen = set()
    normalized: List[str] = []
    for model in models:
        cleaned = model.strip()
        if not cleaned or cleaned in seen:
            continue
        normalized.append(cleaned)
        seen.add(cleaned)
    return normalized


def _build_available_models(
    settings: Dict[str, Any],
    extras: Optional[List[str]] = None,
) -> List[str]:
    """
    Merge persisted available models with any ad-hoc additions while keeping
    ordering stable. If nothing has been saved yet, fall back to defaults.
    """
    existing = settings.get("available_models")
    merged: List[str] = []
    if existing is not None:
        merged.extend(existing)
    else:
        merged.extend(AVAILABLE_MODELS)
    if extras:
        merged.extend(extras)
    return _normalize_models(merged)


def _generate_council_key(name: str) -> str:
    """Create a slug-style council key from a human-readable name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or f"council-{uuid.uuid4().hex[:6]}"


async def _load_council_or_default(key: Optional[str]) -> Dict[str, Any]:
    """
    Fetch a council profile by key, falling back to the default if missing.
    Raises HTTPException if nothing can be found.
    """
    settings = await storage.get_settings_async()
    await storage.ensure_default_council(settings)
    target_key = key or "default"
    council = await storage.get_council(target_key) if target_key else None
    if not council and target_key != "default":
        council = await storage.get_council("default")
    if not council:
        raise HTTPException(status_code=400, detail="No council profiles are configured.")
    return council


@app.get("/api/settings", response_model=SettingsResponse)
async def get_settings():
    """Expose current settings for the UI."""
    settings = await storage.get_settings_async()
    await storage.ensure_default_council(settings)
    councils = await storage.list_councils()
    key = settings.get("openrouter_api_key") or ""
    last4 = key[-4:] if key else None
    council_models = []
    for council in councils:
        council_models.extend(council.get("council_models", []))
        council_models.append(council.get("chairman_model"))
    available_models = _build_available_models(settings, council_models)

    return SettingsResponse(
        has_openrouter_key=bool(key),
        openrouter_key_last4=last4,
        council_models=settings.get("council_models", COUNCIL_MODELS),
        chairman_model=settings.get("chairman_model", CHAIRMAN_MODEL),
        available_models=available_models,
    )


@app.put("/api/settings", response_model=SettingsResponse)
async def update_settings(request: UpdateSettingsRequest):
    """Update configurable settings with validation."""
    normalized_council = _normalize_models(request.council_models)
    chairman_model = request.chairman_model.strip()

    if not normalized_council:
        raise HTTPException(status_code=400, detail="At least one council model is required")
    if len(normalized_council) > 4:
        raise HTTPException(status_code=400, detail="Council limited to 4 members")
    if not chairman_model:
        raise HTTPException(status_code=400, detail="Chairman model is required")
    if chairman_model not in normalized_council:
        raise HTTPException(status_code=400, detail="Chairman must be one of the council models")

    current = await storage.get_settings_async()
    # If the client passes an explicit list of available models, respect it.
    base_available = (
        request.available_models
        if request.available_models is not None
        else current.get("available_models", [])
    )
    available_models = _build_available_models(
        {**current, "available_models": base_available},
        [*normalized_council, chairman_model],
    )

    # openrouter_api_key=None -> keep; '' -> clear; string -> replace
    if request.openrouter_api_key is None:
        new_key = current.get("openrouter_api_key", OPENROUTER_API_KEY or "")
    else:
        new_key = request.openrouter_api_key.strip()

    new_settings = {
        "openrouter_api_key": new_key,
        "council_models": normalized_council,
        "chairman_model": chairman_model,
        "available_models": available_models,
    }
    storage.update_settings(new_settings)
    # Keep default council profile in sync with the saved defaults.
    default_profile = await storage.get_council("default")
    default_name = default_profile["name"] if default_profile else "General"
    await storage.upsert_council("default", default_name, normalized_council, chairman_model)

    last4 = new_key[-4:] if new_key else None
    return SettingsResponse(
        has_openrouter_key=bool(new_key),
        openrouter_key_last4=last4,
        council_models=normalized_council,
        chairman_model=chairman_model,
        available_models=available_models,
    )


@app.get("/api/councils", response_model=List[CouncilProfile])
async def list_council_profiles():
    """List all council profiles."""
    settings = await storage.get_settings_async()
    await storage.ensure_default_council(settings)
    return await storage.list_councils()


@app.post("/api/councils", response_model=CouncilProfile)
async def create_council_profile(request: CreateCouncilRequest):
    """Create a new council profile."""
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Council name is required.")

    normalized_council = _normalize_models(request.council_models)
    chairman_model = request.chairman_model.strip()

    if not normalized_council:
        raise HTTPException(status_code=400, detail="At least one council model is required")
    if len(normalized_council) > 4:
        raise HTTPException(status_code=400, detail="Council limited to 4 members")
    if not chairman_model:
        raise HTTPException(status_code=400, detail="Chairman model is required")
    if chairman_model not in normalized_council:
        raise HTTPException(status_code=400, detail="Chairman must be one of the council models")

    settings = await storage.get_settings_async()
    await storage.ensure_default_council(settings)

    key = request.key.strip() if request.key else _generate_council_key(name)
    if await storage.get_council(key):
        raise HTTPException(status_code=400, detail="Council key already exists.")

    await storage.upsert_council(key, name, normalized_council, chairman_model)

    # Keep available list in sync so UI shows all used models.
    updated_available = _build_available_models(settings, [*normalized_council, chairman_model])
    await storage.update_settings({**settings, "available_models": updated_available})

    created = await storage.get_council(key)
    if not created:
        raise HTTPException(status_code=500, detail="Failed to create council profile.")
    return created


@app.put("/api/councils/{council_key}", response_model=CouncilProfile)
async def update_council_profile(council_key: str, request: UpdateCouncilRequest):
    """Update an existing council profile."""
    existing = await storage.get_council(council_key)
    if not existing:
        raise HTTPException(status_code=404, detail="Council not found.")

    name = request.name.strip() if request.name is not None else existing["name"]
    normalized_council = (
        _normalize_models(request.council_models) if request.council_models is not None else existing["council_models"]
    )
    chairman_model = request.chairman_model.strip() if request.chairman_model is not None else existing["chairman_model"]

    if not normalized_council:
        raise HTTPException(status_code=400, detail="At least one council model is required")
    if len(normalized_council) > 4:
        raise HTTPException(status_code=400, detail="Council limited to 4 members")
    if not chairman_model:
        raise HTTPException(status_code=400, detail="Chairman model is required")
    if chairman_model not in normalized_council:
        raise HTTPException(status_code=400, detail="Chairman must be one of the council models")

    settings = await storage.get_settings_async()
    await storage.ensure_default_council(settings)

    await storage.upsert_council(council_key, name, normalized_council, chairman_model)

    updated_available = _build_available_models(settings, [*normalized_council, chairman_model])
    await storage.update_settings({**settings, "available_models": updated_available})

    updated = await storage.get_council(council_key)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update council profile.")
    return updated


@app.delete("/api/councils/{council_key}")
async def delete_council_profile(council_key: str):
    """Delete a council profile when it is not in use."""
    if council_key == "default":
        raise HTTPException(status_code=400, detail="Default council cannot be deleted.")
    if await storage.conversation_uses_council(council_key):
        raise HTTPException(status_code=400, detail="Cannot delete a council that is assigned to conversations.")
    deleted = await storage.delete_council(council_key)
    if not deleted:
        raise HTTPException(status_code=404, detail="Council not found.")
    return {"status": "deleted", "key": council_key}


@app.get("/api/conversations", response_model=List[ConversationMetadata])
async def list_conversations():
    """List all conversations (metadata only)."""
    return await storage.list_conversations()


@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(request: CreateConversationRequest):
    """Create a new conversation."""
    conversation_id = str(uuid.uuid4())
    settings = await storage.get_settings_async()
    await storage.ensure_default_council(settings)
    desired_council = request.council_key or "default"
    council = await storage.get_council(desired_council)
    if not council:
        if request.council_key:
            raise HTTPException(status_code=400, detail="Unknown council selection.")
        desired_council = "default"
    conversation = await storage.create_conversation(conversation_id, desired_council)
    conversation["council_key"] = desired_council
    return conversation


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(conversation_id: str):
    """Get a specific conversation with all its messages."""
    conversation = await storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    settings = await storage.get_settings_async()
    await storage.ensure_default_council(settings)
    council_key = conversation.get("council_key") or "default"
    if not await storage.get_council(council_key):
        council_key = "default"
        await storage.set_conversation_council(conversation_id, council_key)
        conversation["council_key"] = council_key
    else:
        if not conversation.get("council_key"):
            await storage.set_conversation_council(conversation_id, council_key)
        conversation["council_key"] = council_key
    return conversation


@app.patch("/api/conversations/{conversation_id}", response_model=ConversationMetadata)
async def rename_conversation(conversation_id: str, request: UpdateConversationTitleRequest):
    """Rename a conversation."""
    conversation = await storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    new_title = request.title.strip()
    if not new_title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")

    await storage.update_conversation_title(conversation_id, new_title)

    return {
        "id": conversation_id,
        "created_at": conversation["created_at"],
        "title": new_title,
        "message_count": len(conversation["messages"]),
        "last_interacted_at": conversation.get("last_interacted_at"),
        "council_key": conversation.get("council_key"),
    }


@app.patch("/api/conversations/{conversation_id}/council", response_model=ConversationMetadata)
async def update_conversation_council(conversation_id: str, request: UpdateConversationCouncilRequest):
    """Assign a council profile to a conversation."""
    conversation = await storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    settings = await storage.get_settings_async()
    await storage.ensure_default_council(settings)
    council = await storage.get_council(request.council_key) if request.council_key else None
    if not council:
        raise HTTPException(status_code=400, detail="Unknown council selection.")

    await storage.set_conversation_council(conversation_id, council["key"])

    return {
        "id": conversation_id,
        "created_at": conversation["created_at"],
        "title": conversation["title"],
        "message_count": len(conversation["messages"]),
        "last_interacted_at": conversation.get("last_interacted_at"),
        "council_key": council["key"],
    }


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """Delete a conversation and its messages."""
    conversation = await storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    deleted = await storage.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=500, detail="Failed to delete conversation")

    return {"status": "deleted", "id": conversation_id}


@app.post("/api/conversations/{conversation_id}/message")
async def send_message(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and run the 3-stage council process.
    Returns the complete response with all stages.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    settings = storage.get_settings()
    storage.ensure_default_council(settings)
    council_key = conversation.get("council_key") or "default"
    council = storage.get_council(council_key) or storage.get_council("default")
    if not council:
        raise HTTPException(status_code=400, detail="No council profiles are configured.")
    _ensure_settings_ready(council)
    if not conversation.get("council_key"):
        storage.set_conversation_council(conversation_id, council["key"])
        conversation["council_key"] = council["key"]

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    # Capture history before adding the new turn so we can include it in prompts
    history = deque(conversation["messages"], maxlen=MAX_HISTORY_BUFFER)

    # Add user message
    await storage.add_user_message(conversation_id, request.content)

    # If this is the first message, generate a title
    if is_first_message:
        title = await generate_conversation_title(request.content)
        await storage.update_conversation_title(conversation_id, title)

    # Run the 3-stage council process
    stage1_results, stage2_results, stage3_result, metadata = await run_full_council(
        request.content,
        history,
        council["council_models"],
        council["chairman_model"],
    )

    # Add assistant message with all stages
    await storage.add_assistant_message(
        conversation_id,
        stage1_results,
        stage2_results,
        stage3_result,
        metadata
    )

    # Return the complete response with metadata
    return {
        "stage1": stage1_results,
        "stage2": stage2_results,
        "stage3": stage3_result,
        "metadata": metadata
    }


@app.post("/api/conversations/{conversation_id}/message/stream")
async def send_message_stream(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and stream the 3-stage council process.
    Returns Server-Sent Events as each stage completes.
    """
    # Check if conversation exists
    conversation = await storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    settings = await storage.get_settings_async()
    await storage.ensure_default_council(settings)
    council_key = conversation.get("council_key") or "default"
    council = await storage.get_council(council_key) or await storage.get_council("default")
    if not council:
        raise HTTPException(status_code=400, detail="No council profiles are configured.")
    await _ensure_settings_ready(council)
    if not conversation.get("council_key"):
        await storage.set_conversation_council(conversation_id, council["key"])
        conversation["council_key"] = council["key"]

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0
    history = deque(conversation["messages"], maxlen=MAX_HISTORY_BUFFER)

    # Stream stage milestones as SSE events so the UI can update incrementally.
    async def event_generator():
        try:
            # Add user message
            await storage.add_user_message(conversation_id, request.content)

            # Start title generation in parallel (don't await yet)
            title_task = None
            if is_first_message:
                title_task = asyncio.create_task(generate_conversation_title(request.content))

            # Stage 1: Collect responses
            yield f"data: {json.dumps({'type': 'stage1_start'})}\n\n"
            stage1_results = await stage1_collect_responses(
                request.content,
                history,
                council["council_models"],
            )
            yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"

            # Stage 2: Collect rankings
            yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"
            stage2_results, label_to_model = await stage2_collect_rankings(
                request.content,
                stage1_results,
                council["council_models"],
            )
            aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
            yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': {'label_to_model': label_to_model, 'aggregate_rankings': aggregate_rankings}})}\n\n"

            # Stage 3: Synthesize final answer
            yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
            stage3_result = await stage3_synthesize_final(
                request.content,
                stage1_results,
                stage2_results,
                history,
                council["chairman_model"],
                aggregate_rankings,
            )
            yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"

            # Wait for title generation if it was started
            if title_task:
                title = await title_task
                await storage.update_conversation_title(conversation_id, title)
                yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

            # Save complete assistant message
            await storage.add_assistant_message(
                conversation_id,
                stage1_results,
                stage2_results,
                stage3_result,
                {"label_to_model": label_to_model, "aggregate_rankings": aggregate_rankings}
            )

            # Send completion event
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"

        except Exception as e:
            # Send error event
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
