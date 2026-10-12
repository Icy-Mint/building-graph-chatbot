"""Hand-rolled Anthropic tool-use agent loop -- no agent framework. Every
tool call goes through the typed tool layer (app/tools/); the model never
writes Cypher or SQL. Dependency-injected (client, tool_dispatcher) so it
can be unit-tested with a fake Anthropic client, same pattern used
throughout this project for the Streamlit bots' LLM chains.
"""
import json
import os
import time
from datetime import date, datetime
from decimal import Decimal

import anthropic

from app.agent.logging import log_turn
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tool_specs import TOOLS, call_tool

DEFAULT_MODEL = "claude-sonnet-5"
MAX_ITERATIONS = 5
MAX_TOKENS = 2048


def _json_default(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Not JSON serializable: {type(obj)!r}")


def get_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Missing ANTHROPIC_API_KEY. Copy .env.example to .env and fill it in.")
    return anthropic.Anthropic(api_key=api_key)


def get_max_iterations() -> int:
    return int(os.getenv("MAX_AGENT_ITERATIONS", MAX_ITERATIONS))


def get_model() -> str:
    # Hook for a cheap-router tier later: a caller could inspect the question
    # and choose between a cheap and a capable model here. Not built yet --
    # this always returns the one configured model.
    return os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)


def _cached_system_and_tools():
    """Marks the system prompt and the last tool definition as cacheable --
    per Anthropic's prompt-caching rules, a cache_control marker caches
    everything up to and including that content block, so marking the last
    tool caches the whole (stable, identical-every-turn) tools list too."""
    system = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
    tools = TOOLS[:-1] + [{**TOOLS[-1], "cache_control": {"type": "ephemeral"}}]
    return system, tools


def ask(
    question: str,
    client: anthropic.Anthropic | None = None,
    max_iterations: int | None = None,
    tool_dispatcher=call_tool,
) -> dict:
    """Runs the agent loop for one question.

    Returns {"answer": str | None, "tool_calls": [...], "hit_iteration_cap":
    bool, "usage": {...}, "total_latency_ms": int}.
    """
    client = client or get_client()
    model = get_model()
    max_iterations = max_iterations if max_iterations is not None else get_max_iterations()
    system, tools = _cached_system_and_tools()
    start_time = time.time()

    messages = [{"role": "user", "content": question}]
    tool_calls_log = []
    usage_totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}

    def _accumulate_usage(response):
        usage_totals["input_tokens"] += response.usage.input_tokens
        usage_totals["output_tokens"] += response.usage.output_tokens
        usage_totals["cache_read_input_tokens"] += getattr(response.usage, "cache_read_input_tokens", 0) or 0
        usage_totals["cache_creation_input_tokens"] += getattr(response.usage, "cache_creation_input_tokens", 0) or 0

    hit_cap = False
    final_text = None

    for _ in range(max_iterations):
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=tools,
            # claude-sonnet-5 produces an extended-thinking block by default
            # (confirmed live: it consumed an entire 1024-token budget before
            # ever reaching a tool call or an answer, stop_reason="max_tokens").
            # This agent dispatches to simple typed tools -- it doesn't need
            # deep reasoning, so thinking is disabled for speed/cost/predictability.
            thinking={"type": "disabled"},
            messages=messages,
        )
        _accumulate_usage(response)
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            final_text = "".join(block.text for block in response.content if block.type == "text")
            if not final_text and response.stop_reason == "max_tokens":
                # Never silently return an empty answer -- say plainly what
                # happened instead of looking like a model that had nothing
                # to say.
                final_text = (
                    "[No answer was produced -- the response was truncated at the "
                    f"{MAX_TOKENS}-token limit before any text was generated.]"
                )
            break

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            call_start = time.time()
            result = tool_dispatcher(block.name, block.input)
            latency_ms = round((time.time() - call_start) * 1000)

            tool_calls_log.append({
                "tool": block.name,
                "arguments": block.input,
                "status": result.get("status"),
                "latency_ms": latency_ms,
            })
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result, default=_json_default),
                "is_error": result.get("status") == "error",
            })

        messages.append({"role": "user", "content": tool_results})
    else:
        # Spec: on hitting the cap, return the best partial answer and say
        # so -- never silently keep looping or fabricate a confident answer.
        hit_cap = True
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            thinking={"type": "disabled"},
            messages=messages + [{
                "role": "user",
                "content": (
                    "You've reached the maximum number of tool calls for this turn. "
                    "Give your best answer based on what you've found so far, and say "
                    "plainly that you weren't able to fully investigate."
                ),
            }],
        )
        _accumulate_usage(response)
        final_text = "".join(block.text for block in response.content if block.type == "text")
        if not final_text and response.stop_reason == "max_tokens":
            final_text = (
                "[No answer was produced -- the response was truncated at the "
                f"{MAX_TOKENS}-token limit before any text was generated.]"
            )

    total_latency_ms = round((time.time() - start_time) * 1000)

    log_turn({
        "question": question,
        "tool_calls": tool_calls_log,
        "final_answer": final_text,
        "hit_iteration_cap": hit_cap,
        "usage": usage_totals,
        "total_latency_ms": total_latency_ms,
    })

    return {
        "answer": final_text,
        "tool_calls": tool_calls_log,
        "hit_iteration_cap": hit_cap,
        "usage": usage_totals,
        "total_latency_ms": total_latency_ms,
    }
