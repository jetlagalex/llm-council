"""FastAPI backend for LLM Council."""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import uuid
import json
import asyncio
import asyncio.subprocess
import os

from . import storage
from .council import run_full_council, generate_conversation_title, stage1_collect_responses, stage2_collect_rankings, stage3_synthesize_final, calculate_aggregate_rankings
from .config import AVAILABLE_MODELS, COUNCIL_MODELS, CHAIRMAN_MODEL, OPENROUTER_API_KEY

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
    pass


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
    message_count: int


class Conversation(BaseModel):
    """Full conversation with all messages."""
    id: str
    created_at: str
    title: str
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


def _ensure_settings_ready() -> Dict[str, Any]:
    """
    Validate that settings contain an API key and at least one model.
    Raises HTTPException if requirements are not met.
    """
    settings = storage.get_settings()
    api_key = settings.get("openrouter_api_key") or OPENROUTER_API_KEY
    if not api_key:
        raise HTTPException(status_code=400, detail="OpenRouter API key not set. Add it in Settings.")

    council_models = settings.get("council_models") or COUNCIL_MODELS
    chairman = settings.get("chairman_model") or CHAIRMAN_MODEL
    if not council_models:
        raise HTTPException(status_code=400, detail="Council models not configured. Update Settings.")
    if chairman not in council_models:
        raise HTTPException(status_code=400, detail="Chairman must be one of the council models.")

    return {
        "api_key": api_key,
        "council_models": council_models,
        "chairman_model": chairman,
    }


@app.get("/api/settings", response_model=SettingsResponse)
async def get_settings():
    """Expose current settings for the UI."""
    settings = storage.get_settings()
    key = settings.get("openrouter_api_key") or ""
    last4 = key[-4:] if key else None

    return SettingsResponse(
        has_openrouter_key=bool(key),
        openrouter_key_last4=last4,
        council_models=settings.get("council_models", COUNCIL_MODELS),
        chairman_model=settings.get("chairman_model", CHAIRMAN_MODEL),
        available_models=AVAILABLE_MODELS,
    )


@app.put("/api/settings", response_model=SettingsResponse)
async def update_settings(request: UpdateSettingsRequest):
    """Update configurable settings with validation."""
    if not request.council_models:
        raise HTTPException(status_code=400, detail="At least one council model is required")
    if len(request.council_models) > 4:
        raise HTTPException(status_code=400, detail="Council limited to 4 members")
    if request.chairman_model not in request.council_models:
        raise HTTPException(status_code=400, detail="Chairman must be one of the council models")

    # Optional: ensure models are from the allowed list
    invalid_models = [m for m in request.council_models if m not in AVAILABLE_MODELS]
    if request.chairman_model not in AVAILABLE_MODELS:
        invalid_models.append(request.chairman_model)
    if invalid_models:
        raise HTTPException(status_code=400, detail=f"Unsupported model(s): {', '.join(sorted(set(invalid_models)))}")

    current = storage.get_settings()
    # openrouter_api_key=None -> keep; '' -> clear; string -> replace
    if request.openrouter_api_key is None:
        new_key = current.get("openrouter_api_key", OPENROUTER_API_KEY or "")
    else:
        new_key = request.openrouter_api_key.strip()

    new_settings = {
        "openrouter_api_key": new_key,
        "council_models": request.council_models,
        "chairman_model": request.chairman_model,
    }
    storage.update_settings(new_settings)

    last4 = new_key[-4:] if new_key else None
    return SettingsResponse(
        has_openrouter_key=bool(new_key),
        openrouter_key_last4=last4,
        council_models=request.council_models,
        chairman_model=request.chairman_model,
        available_models=AVAILABLE_MODELS,
    )


@app.get("/api/conversations", response_model=List[ConversationMetadata])
async def list_conversations():
    """List all conversations (metadata only)."""
    return storage.list_conversations()


@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(request: CreateConversationRequest):
    """Create a new conversation."""
    conversation_id = str(uuid.uuid4())
    conversation = storage.create_conversation(conversation_id)
    return conversation


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(conversation_id: str):
    """Get a specific conversation with all its messages."""
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.patch("/api/conversations/{conversation_id}", response_model=ConversationMetadata)
async def rename_conversation(conversation_id: str, request: UpdateConversationTitleRequest):
    """Rename a conversation."""
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    new_title = request.title.strip()
    if not new_title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")

    storage.update_conversation_title(conversation_id, new_title)

    return {
        "id": conversation_id,
        "created_at": conversation["created_at"],
        "title": new_title,
        "message_count": len(conversation["messages"]),
    }


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """Delete a conversation and its messages."""
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    deleted = storage.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=500, detail="Failed to delete conversation")

    return {"status": "deleted", "id": conversation_id}


@app.post("/api/conversations/{conversation_id}/message")
async def send_message(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and run the 3-stage council process.
    Returns the complete response with all stages.
    """
    _ensure_settings_ready()
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    # Capture history before adding the new turn so we can include it in prompts
    history = conversation["messages"]

    # Add user message
    storage.add_user_message(conversation_id, request.content)

    # If this is the first message, generate a title
    if is_first_message:
        title = await generate_conversation_title(request.content)
        storage.update_conversation_title(conversation_id, title)

    # Run the 3-stage council process
    stage1_results, stage2_results, stage3_result, metadata = await run_full_council(
        request.content,
        history
    )

    # Add assistant message with all stages
    storage.add_assistant_message(
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
    _ensure_settings_ready()
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0
    history = conversation["messages"]

    # Stream stage milestones as SSE events so the UI can update incrementally.
    async def event_generator():
        try:
            # Add user message
            storage.add_user_message(conversation_id, request.content)

            # Start title generation in parallel (don't await yet)
            title_task = None
            if is_first_message:
                title_task = asyncio.create_task(generate_conversation_title(request.content))

            # Stage 1: Collect responses
            yield f"data: {json.dumps({'type': 'stage1_start'})}\n\n"
            stage1_results = await stage1_collect_responses(request.content, history)
            yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"

            # Stage 2: Collect rankings
            yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"
            stage2_results, label_to_model = await stage2_collect_rankings(request.content, stage1_results)
            aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
            yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': {'label_to_model': label_to_model, 'aggregate_rankings': aggregate_rankings}})}\n\n"

            # Stage 3: Synthesize final answer
            yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
            stage3_result = await stage3_synthesize_final(request.content, stage1_results, stage2_results, history)
            yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"

            # Wait for title generation if it was started
            if title_task:
                title = await title_task
                storage.update_conversation_title(conversation_id, title)
                yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

            # Save complete assistant message
            storage.add_assistant_message(
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
