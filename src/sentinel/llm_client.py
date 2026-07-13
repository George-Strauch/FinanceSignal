"""Shared LLM tool-calling client for OpenRouter.

Non-streaming, non-async — designed for background processes (canonicalization
worker, mass-correct). The streaming SSE version in analysis.py serves the
frontend; this one serves batch processing.

Usage:
    from sentinel.llm_client import run_tool_session

    result = run_tool_session(
        model="deepseek/deepseek-v4-flash",
        system_prompt="...",
        user_message="...",
        tools=TOOL_DEFINITIONS,
        max_rounds=6,
        execute_tool=my_tool_handler,  # (tool_name, args) -> result dict
        trace_session_id=42,          # optional: llm_trace.db session for logging
    )
    # result.terminal_tool, result.terminal_args, result.rounds, result.content
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MAX_RETRIES = 3
RETRY_DELAY = 5.0


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolSessionResult:
    terminal_tool: str | None = None
    terminal_args: dict = field(default_factory=dict)
    terminal_tool_call_id: str | None = None
    rounds: int = 0
    content: str = ""
    all_tool_calls: list[dict] = field(default_factory=list)
    error: str | None = None


def _make_request(messages: list[dict], tools: list[dict] | None,
                  model: str) -> dict:
    """Non-streaming OpenRouter request. Returns parsed response JSON."""
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    body = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            response = httpx.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://financesignal.local",
                    "X-Title": "FinanceSignal",
                },
                json=body,
                timeout=httpx.Timeout(120.0, connect=10.0),
            )
            if response.status_code == 429:
                retry_after = float(response.headers.get("X-RateLimit-Reset", RETRY_DELAY))
                logger.warning("OpenRouter rate limited, waiting %.1fs", retry_after)
                time.sleep(retry_after)
                last_error = f"429 rate limit (attempt {attempt + 1})"
                continue
            if response.status_code >= 500:
                logger.warning("OpenRouter %d, retrying", response.status_code)
                time.sleep(RETRY_DELAY * (attempt + 1))
                last_error = f"{response.status_code} (attempt {attempt + 1})"
                continue
            if response.status_code != 200:
                raise RuntimeError(f"OpenRouter error {response.status_code}: {response.text}")
            return response.json()
        except httpx.HTTPError as e:
            last_error = str(e)
            time.sleep(RETRY_DELAY * (attempt + 1))

    raise RuntimeError(f"OpenRouter request failed after {MAX_RETRIES} retries: {last_error}")


# Tool names that are "terminal" — they end the session (a decision is made)
TERMINAL_TOOLS = {"link_to_canonical", "create_new_canonical", "mark_as_misc",
                  "split", "delete", "link_entity_to_ticker"}


def run_tool_session(
    model: str,
    system_prompt: str,
    user_message: str,
    tools: list[dict],
    max_rounds: int,
    execute_tool: Callable[[str, dict], dict],
    trace_db=None,
    trace_session_id: int | None = None,
) -> ToolSessionResult:
    """Run a multi-round tool-calling session against OpenRouter.

    Args:
        model: OpenRouter model ID (e.g. "deepseek/deepseek-v4-flash")
        system_prompt: The system prompt
        user_message: The initial user message
        tools: OpenRouter tool definitions
        max_rounds: Maximum tool-call rounds before giving up
        execute_tool: Callable(tool_name, arguments_dict) -> result_dict
        trace_db: Optional LLMTraceDB instance for logging
        trace_session_id: Optional session ID in the trace DB

    Returns:
        ToolSessionResult with the terminal tool call (if any), rounds, content, etc.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    result = ToolSessionResult()

    for round_num in range(max_rounds):
        result.rounds = round_num + 1

        try:
            response_json = _make_request(messages, tools, model)
        except Exception as e:
            result.error = str(e)
            if trace_db and trace_session_id:
                trace_db.add_error(trace_session_id, "request", str(e), round=round_num)
            return result

        choice = response_json.get("choices", [{}])[0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason")

        content = message.get("content", "") or ""
        result.content += content

        if trace_db and trace_session_id:
            trace_db.add_message(
                trace_session_id, round=round_num, role="assistant",
                content=content or None,
                tool_calls=message.get("tool_calls"),
            )

        tool_calls = message.get("tool_calls", [])

        if not tool_calls or finish_reason != "tool_calls":
            return result

        assistant_msg = {
            "role": "assistant",
            "content": content or None,
            "tool_calls": tool_calls,
        }
        messages.append(assistant_msg)

        for tc in tool_calls:
            tc_id = tc.get("id", "")
            tc_func = tc.get("function", {})
            tool_name = tc_func.get("name", "")
            args_str = tc_func.get("arguments", "{}")

            try:
                arguments = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                arguments = {}

            try:
                tool_result = execute_tool(tool_name, arguments)
            except Exception as e:
                tool_result = {"error": str(e)}
                if trace_db and trace_session_id:
                    trace_db.add_error(trace_session_id, "tool_exec", str(e),
                                      round=round_num, raw_payload=tool_name)

            result.all_tool_calls.append({
                "round": round_num,
                "name": tool_name,
                "arguments": arguments,
                "result": tool_result,
            })

            if trace_db and trace_session_id:
                trace_db.add_tool_outcome(
                    trace_session_id, tc_id, tool_name, arguments, tool_result,
                )

            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": json.dumps(tool_result),
            })

            if trace_db and trace_session_id:
                trace_db.add_message(
                    trace_session_id, round=round_num, role="tool",
                    content=json.dumps(tool_result),
                    tool_call_id=tc_id, tool_name=tool_name,
                )

            if tool_name in TERMINAL_TOOLS:
                result.terminal_tool = tool_name
                result.terminal_args = arguments
                result.terminal_tool_call_id = tc_id
                return result

    result.error = f"Max rounds ({max_rounds}) reached without terminal tool"
    return result
