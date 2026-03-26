"""Translate ARCHI streaming events to OpenAI Chat Completions SSE format."""

import json
import time
import uuid
from typing import Any, Dict, Iterator


def _make_chunk_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def _sse_line(data: Any) -> str:
    return f"data: {json.dumps(data, default=str)}\n\n"


def _delta_chunk(
    chunk_id: str,
    model: str,
    created: int,
    content: str | None = None,
    finish_reason: str | None = None,
    usage: dict | None = None,
) -> dict:
    delta: Dict[str, Any] = {}
    if content is not None:
        delta["content"] = content
    chunk: Dict[str, Any] = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    if usage is not None:
        chunk["usage"] = usage
    return chunk


def _format_tool_start(event: dict) -> str:
    tool_name = event.get("tool_name", "unknown")
    tool_args = event.get("tool_args", {})
    if isinstance(tool_args, dict) and tool_args:
        args_str = ", ".join(f"{k}={v!r}" for k, v in tool_args.items())
        return f"\n> **Running** `{tool_name}({args_str})`\n"
    return f"\n> **Running** `{tool_name}()`\n"


def _format_tool_output(event: dict) -> str:
    output = event.get("output", "")
    truncated = event.get("truncated", False)
    if not output:
        return ""
    text = f"\n> **Result:**\n> {output}"
    if truncated:
        text += " *(truncated)*"
    return text + "\n"


def translate_events(
    archi_events: Iterator[Dict[str, Any]],
    model: str,
) -> Iterator[str]:
    """Convert an iterator of ARCHI NDJSON events to OpenAI SSE lines.

    Yields strings of the form ``data: {...}\\n\\n``, ending with
    ``data: [DONE]\\n\\n``.
    """
    chunk_id = _make_chunk_id()
    created = int(time.time())

    for event in archi_events:
        event_type = event.get("type", "")

        if event_type == "chunk":
            content = event.get("content", "")
            if content:
                yield _sse_line(
                    _delta_chunk(chunk_id, model, created, content=content)
                )

        elif event_type == "tool_start":
            text = _format_tool_start(event)
            if text:
                yield _sse_line(
                    _delta_chunk(chunk_id, model, created, content=text)
                )

        elif event_type == "tool_output":
            text = _format_tool_output(event)
            if text:
                yield _sse_line(
                    _delta_chunk(chunk_id, model, created, content=text)
                )

        elif event_type in ("tool_end", "thinking_start", "thinking_end", "meta", "warning"):
            # Skip — not meaningful for OpenAI consumers
            continue

        elif event_type == "final":
            usage_raw = event.get("usage") or {}
            usage = {
                "prompt_tokens": usage_raw.get("prompt_tokens", 0),
                "completion_tokens": usage_raw.get("completion_tokens", 0),
                "total_tokens": usage_raw.get("total_tokens", 0),
            }
            yield _sse_line(
                _delta_chunk(
                    chunk_id, model, created,
                    finish_reason="stop",
                    usage=usage,
                )
            )
            yield "data: [DONE]\n\n"
            return

        elif event_type == "error":
            # Emit error as content so the user sees it, then stop
            message = event.get("message", "An error occurred")
            yield _sse_line(
                _delta_chunk(chunk_id, model, created, content=f"\n\n**Error:** {message}\n")
            )
            yield _sse_line(
                _delta_chunk(chunk_id, model, created, finish_reason="stop")
            )
            yield "data: [DONE]\n\n"
            return

    # If we exit the loop without a final event, close the stream
    yield _sse_line(
        _delta_chunk(chunk_id, model, created, finish_reason="stop")
    )
    yield "data: [DONE]\n\n"


def build_non_streaming_response(
    archi_events: Iterator[Dict[str, Any]],
    model: str,
) -> dict:
    """Collect ARCHI events into a single OpenAI Chat Completion response."""
    response_id = _make_chunk_id()
    created = int(time.time())
    content_parts: list[str] = []
    usage_raw: dict = {}

    for event in archi_events:
        event_type = event.get("type", "")

        if event_type == "chunk":
            content_parts.append(event.get("content", ""))
        elif event_type == "tool_start":
            content_parts.append(_format_tool_start(event))
        elif event_type == "tool_output":
            text = _format_tool_output(event)
            if text:
                content_parts.append(text)
        elif event_type == "final":
            usage_raw = event.get("usage") or {}
        elif event_type == "error":
            content_parts.append(f"\n\n**Error:** {event.get('message', 'An error occurred')}\n")

    usage = {
        "prompt_tokens": usage_raw.get("prompt_tokens", 0),
        "completion_tokens": usage_raw.get("completion_tokens", 0),
        "total_tokens": usage_raw.get("total_tokens", 0),
    }

    return {
        "id": response_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "".join(content_parts),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
    }
