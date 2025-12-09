"""3-stage LLM Council orchestration."""

import logging
from collections.abc import Sequence
from time import perf_counter
from typing import Any, Dict, List, Tuple

from .config import (
    MAX_CONTEXT_MESSAGES,
    MAX_SUMMARY_MESSAGES,
    TITLE_MODEL,
    TITLE_TIMEOUT,
)
from .openrouter import query_model, query_models_parallel

logger = logging.getLogger(__name__)


def _build_context_messages(history: Sequence[Dict[str, Any]], user_query: str) -> List[Dict[str, str]]:
    """
    Build chat messages including prior turns so models have conversation memory.

    Only the final Stage 3 answer from prior assistant turns is used to keep the
    context compact and avoid leaking intermediate deliberation.
    """
    condensed: List[Dict[str, str]] = []
    recent_history = list(history)[-MAX_CONTEXT_MESSAGES:]

    for msg in recent_history:
        if msg["role"] == "user":
            condensed.append({"role": "user", "content": msg["content"]})
        elif msg["role"] == "assistant" and msg.get("stage3"):
            condensed.append({"role": "assistant", "content": msg["stage3"].get("response", "")})

    condensed.append({"role": "user", "content": user_query})
    return condensed


async def stage1_collect_responses(
    user_query: str,
    history: Sequence[Dict[str, Any]],
    council_models: List[str],
) -> List[Dict[str, Any]]:
    """
    Stage 1: Collect individual responses from all council models.

    Args:
        user_query: The user's question

    Returns:
        List of dicts with 'model' and 'response' keys
    """
    start_time = perf_counter()
    logger.info(
        "stage1_collect_responses_start",
        extra={"model_count": len(council_models)},
    )
    messages = _build_context_messages(history, user_query)

    responses = await query_models_parallel(council_models, messages)

    # Format results
    stage1_results = []
    for model, response in responses.items():
        if response is not None:  # Only include successful responses
            stage1_results.append(
                {
                    "model": model,
                    "response": response.get("content", ""),
                }
            )
    elapsed_ms = int((perf_counter() - start_time) * 1000)
    logger.info(
        "stage1_collect_responses_complete",
        extra={"elapsed_ms": elapsed_ms, "success_count": len(stage1_results)},
    )

    return stage1_results


async def stage2_collect_rankings(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    council_models: List[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """
    Stage 2: Each model ranks the anonymized responses.

    Args:
        user_query: The original user query
        stage1_results: Results from Stage 1

    Returns:
        Tuple of (rankings list, label_to_model mapping)
    """
    start_time = perf_counter()
    logger.info(
        "stage2_collect_rankings_start",
        extra={"model_count": len(council_models)},
    )
    # Create anonymized labels for responses (Response A, Response B, etc.)
    labels = [chr(65 + i) for i in range(len(stage1_results))]  # A, B, C, ...

    # Create mapping from label to model name
    label_to_model = {
        f"Response {label}": result['model']
        for label, result in zip(labels, stage1_results)
    }

    # Build the ranking prompt
    responses_text = "\n\n".join([
        f"Response {label}:\n{result['response']}"
        for label, result in zip(labels, stage1_results)
    ])

    ranking_prompt = f"""You are evaluating different responses to the following question:

Question: {user_query}

Here are the responses from different models (anonymized):

{responses_text}

Your task:
1. First, evaluate each response individually. For each response, explain what it does well and what it does poorly.
2. Then, at the very end of your response, provide a final ranking.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line should be: number, period, space, then ONLY the response label (e.g., "1. Response A")
- Do not add any other text or explanations in the ranking section

Example of the correct format for your ENTIRE response:

Response A provides good detail on X but misses Y...
Response B is accurate but lacks depth on Z...
Response C offers the most comprehensive answer...

FINAL RANKING:
1. Response C
2. Response A
3. Response B

Now provide your evaluation and ranking:"""

    messages = [{"role": "user", "content": ranking_prompt}]

    responses = await query_models_parallel(council_models, messages)

    # Format results
    stage2_results = []
    for model, response in responses.items():
        if response is not None:
            full_text = response.get("content", "")
            parsed = parse_ranking_from_text(full_text)
            stage2_results.append(
                {
                    "model": model,
                    "ranking": full_text,
                    "parsed_ranking": parsed,
                }
            )

    elapsed_ms = int((perf_counter() - start_time) * 1000)
    logger.info(
        "stage2_collect_rankings_complete",
        extra={"elapsed_ms": elapsed_ms, "success_count": len(stage2_results)},
    )

    return stage2_results, label_to_model


async def stage3_synthesize_final(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
    history: Sequence[Dict[str, Any]],
    chairman_model: str,
) -> Dict[str, Any]:
    """
    Stage 3: Chairman synthesizes final response.

    Args:
        user_query: The original user query
        stage1_results: Individual model responses from Stage 1
        stage2_results: Rankings from Stage 2

    Returns:
        Dict with 'model' and 'response' keys
    """
    start_time = perf_counter()
    logger.info("stage3_synthesize_final_start", extra={"chairman_model": chairman_model})
    # Build comprehensive context for chairman
    stage1_text = "\n\n".join([
        f"Model: {result['model']}\nResponse: {result['response']}"
        for result in stage1_results
    ])

    stage2_text = "\n\n".join([
        f"Model: {result['model']}\nRanking: {result['ranking']}"
        for result in stage2_results
    ])

    history_text = "\n\n".join([
        f"{msg['role'].capitalize()}: {msg['content'] if msg['role']=='user' else msg['stage3'].get('response', '')}"
        for msg in list(history)[-MAX_SUMMARY_MESSAGES:]
        if msg["role"] == "user" or msg.get("stage3")
    ])

    chairman_prompt = f"""You are the Chairman of an LLM Council. Multiple AI models have provided responses to a user's question, and then ranked each other's responses.

Original Question: {user_query}

Conversation so far (recent turns):
{history_text}

STAGE 1 - Individual Responses:
{stage1_text}

STAGE 2 - Peer Rankings:
{stage2_text}

Your task as Chairman is to synthesize all of this information into a single, comprehensive, accurate answer to the user's original question. Consider:
- The individual responses and their insights
- The peer rankings and what they reveal about response quality
- Any patterns of agreement or disagreement

Provide a clear, well-reasoned final answer that represents the council's collective wisdom:"""

    messages = [{"role": "user", "content": chairman_prompt}]

    response = await query_model(chairman_model, messages)

    if response is None:
        # Fallback if chairman fails
        elapsed_ms = int((perf_counter() - start_time) * 1000)
        logger.warning(
            "stage3_synthesize_final_failed",
            extra={"chairman_model": chairman_model, "elapsed_ms": elapsed_ms},
        )
        return {
            "model": chairman_model,
            "response": "Error: Unable to generate final synthesis."
        }

    elapsed_ms = int((perf_counter() - start_time) * 1000)
    logger.info(
        "stage3_synthesize_final_complete",
        extra={"chairman_model": chairman_model, "elapsed_ms": elapsed_ms},
    )

    return {"model": chairman_model, "response": response.get("content", "")}


def parse_ranking_from_text(ranking_text: str) -> List[str]:
    """
    Parse the FINAL RANKING section from the model's response.

    Args:
        ranking_text: The full text response from the model

    Returns:
        List of response labels in ranked order
    """
    import re

    # Look for "FINAL RANKING:" section
    if "FINAL RANKING:" in ranking_text:
        # Extract everything after "FINAL RANKING:"
        parts = ranking_text.split("FINAL RANKING:")
        if len(parts) >= 2:
            ranking_section = parts[1]
            # Try to extract numbered list format (e.g., "1. Response A")
            # This pattern looks for: number, period, optional space, "Response X"
            numbered_matches = re.findall(r'\d+\.\s*Response [A-Z]', ranking_section)
            if numbered_matches:
                # Extract just the "Response X" part
                return [re.search(r'Response [A-Z]', m).group() for m in numbered_matches]

            # Fallback: Extract all "Response X" patterns in order
            matches = re.findall(r'Response [A-Z]', ranking_section)
            return matches

    # Fallback: try to find any "Response X" patterns in order
    matches = re.findall(r'Response [A-Z]', ranking_text)
    return matches


def calculate_aggregate_rankings(
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str]
) -> List[Dict[str, Any]]:
    """
    Calculate aggregate rankings across all models.

    Args:
        stage2_results: Rankings from each model
        label_to_model: Mapping from anonymous labels to model names

    Returns:
        List of dicts with model name and average rank, sorted best to worst
    """
    from collections import defaultdict

    # Track positions for each model
    model_positions = defaultdict(list)

    for ranking in stage2_results:
        ranking_text = ranking['ranking']

        # Parse the ranking from the structured format
        parsed_ranking = parse_ranking_from_text(ranking_text)

        for position, label in enumerate(parsed_ranking, start=1):
            if label in label_to_model:
                model_name = label_to_model[label]
                model_positions[model_name].append(position)

    # Calculate average position for each model
    aggregate = []
    for model, positions in model_positions.items():
        if positions:
            avg_rank = sum(positions) / len(positions)
            aggregate.append({
                "model": model,
                "average_rank": round(avg_rank, 2),
                "rankings_count": len(positions)
            })

    # Sort by average rank (lower is better)
    aggregate.sort(key=lambda x: x['average_rank'])

    return aggregate


async def generate_conversation_title(user_query: str) -> str:
    """
    Generate a short title for a conversation based on the first user message.

    Args:
        user_query: The first user message

    Returns:
        A short title (3-5 words)
    """
    title_prompt = f"""Generate a very short title (3-5 words maximum) that summarizes the following question.
The title should be concise and descriptive. Do not use quotes or punctuation in the title.

Question: {user_query}

Title:"""

    messages = [{"role": "user", "content": title_prompt}]

    # Use configured fast title model for title generation
    start_time = perf_counter()
    response = await query_model(TITLE_MODEL, messages, timeout=TITLE_TIMEOUT)

    if response is None:
        # Fallback to a generic title
        return "New Conversation"

    title = response.get('content', 'New Conversation').strip()

    # Clean up the title - remove quotes, limit length
    title = title.strip('"\'')

    # Truncate if too long
    if len(title) > 50:
        title = title[:47] + "..."

    elapsed_ms = int((perf_counter() - start_time) * 1000)
    logger.info("title_generated", extra={"elapsed_ms": elapsed_ms})

    return title


async def run_full_council(
    user_query: str,
    history: Sequence[Dict[str, Any]],
    council_models: List[str],
    chairman_model: str,
) -> Tuple[
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    Dict[str, Any],
    Dict[str, Any],
]:
    """
    Run the complete 3-stage council process.

    Args:
        user_query: The user's question

    Returns:
        Tuple of (stage1_results, stage2_results, stage3_result, metadata)
    """
    overall_start = perf_counter()
    # Stage 1: Collect individual responses
    stage1_results = await stage1_collect_responses(user_query, history, council_models)

    # If no models responded successfully, return error
    if not stage1_results:
        return [], [], {
            "model": "error",
            "response": "All models failed to respond. Please try again."
        }, {}

    # Stage 2: Collect rankings
    stage2_results, label_to_model = await stage2_collect_rankings(
        user_query,
        stage1_results,
        council_models,
    )

    # Calculate aggregate rankings
    aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)

    # Stage 3: Synthesize final answer
    stage3_result = await stage3_synthesize_final(
        user_query,
        stage1_results,
        stage2_results,
        history,
        chairman_model,
    )

    # Prepare metadata
    metadata = {
        "label_to_model": label_to_model,
        "aggregate_rankings": aggregate_rankings
    }

    elapsed_ms = int((perf_counter() - overall_start) * 1000)
    logger.info(
        "run_full_council_complete",
        extra={
            "elapsed_ms": elapsed_ms,
            "stage1_count": len(stage1_results),
            "stage2_count": len(stage2_results),
        },
    )

    return stage1_results, stage2_results, stage3_result, metadata
