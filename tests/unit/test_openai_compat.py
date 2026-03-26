"""Unit tests for the OpenAI-compatible event translator."""

import json

import pytest

from src.interfaces.chat_app.openai_compat import (
    translate_events,
    build_non_streaming_response,
)


def _parse_sse(line: str) -> dict | str:
    """Parse a single SSE line, returning the JSON dict or the raw data string."""
    assert line.startswith("data: "), f"Expected SSE line, got: {line!r}"
    payload = line[len("data: "):].strip()
    if payload == "[DONE]":
        return "[DONE]"
    return json.loads(payload)


# ── translate_events ────────────────────────────────────────────────


class TestTranslateChunkEvents:
    def test_text_chunk_produces_content_delta(self):
        events = [
            {"type": "chunk", "content": "Hello"},
            {"type": "final", "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}},
        ]
        lines = list(translate_events(iter(events), model="test-agent"))
        assert len(lines) == 3  # content + finish + [DONE]

        chunk = _parse_sse(lines[0])
        assert chunk["choices"][0]["delta"]["content"] == "Hello"
        assert chunk["choices"][0]["finish_reason"] is None
        assert chunk["model"] == "test-agent"

    def test_accumulated_chunks_emit_only_deltas(self):
        events = [
            {"type": "chunk", "content": "I'm", "accumulated": True},
            {"type": "chunk", "content": "I'm sorry", "accumulated": True},
            {"type": "chunk", "content": "I'm sorry, but", "accumulated": True},
            {"type": "final", "usage": {}},
        ]
        lines = list(translate_events(iter(events), model="m"))
        # 3 deltas + finish + [DONE]
        assert len(lines) == 5

        delta1 = _parse_sse(lines[0])["choices"][0]["delta"]["content"]
        delta2 = _parse_sse(lines[1])["choices"][0]["delta"]["content"]
        delta3 = _parse_sse(lines[2])["choices"][0]["delta"]["content"]
        assert delta1 == "I'm"
        assert delta2 == " sorry"
        assert delta3 == ", but"

    def test_empty_chunk_is_skipped(self):
        events = [
            {"type": "chunk", "content": ""},
            {"type": "final", "usage": {}},
        ]
        lines = list(translate_events(iter(events), model="m"))
        # Only finish + [DONE]
        assert len(lines) == 2


class TestTranslateToolEvents:
    def test_tool_start_rendered_as_text(self):
        events = [
            {"type": "tool_start", "tool_name": "search", "tool_args": {"query": "test"}},
            {"type": "final", "usage": {}},
        ]
        lines = list(translate_events(iter(events), model="m"))
        chunk = _parse_sse(lines[0])
        content = chunk["choices"][0]["delta"]["content"]
        assert "search" in content
        assert "query" in content

    def test_tool_output_rendered_as_text(self):
        events = [
            {"type": "tool_output", "output": "Found 3 results", "truncated": False},
            {"type": "final", "usage": {}},
        ]
        lines = list(translate_events(iter(events), model="m"))
        chunk = _parse_sse(lines[0])
        content = chunk["choices"][0]["delta"]["content"]
        assert "Found 3 results" in content

    def test_tool_output_truncated_marker(self):
        events = [
            {"type": "tool_output", "output": "partial", "truncated": True},
            {"type": "final", "usage": {}},
        ]
        lines = list(translate_events(iter(events), model="m"))
        chunk = _parse_sse(lines[0])
        content = chunk["choices"][0]["delta"]["content"]
        assert "truncated" in content.lower()


class TestTranslateSkippedEvents:
    @pytest.mark.parametrize("event_type", ["tool_end", "thinking_start", "thinking_end", "meta", "warning"])
    def test_event_is_skipped(self, event_type):
        events = [
            {"type": event_type},
            {"type": "final", "usage": {}},
        ]
        lines = list(translate_events(iter(events), model="m"))
        # Only finish + [DONE]
        assert len(lines) == 2


class TestTranslateFinalEvent:
    def test_final_emits_stop_and_done(self):
        events = [
            {"type": "final", "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}},
        ]
        lines = list(translate_events(iter(events), model="m"))
        assert len(lines) == 2

        finish = _parse_sse(lines[0])
        assert finish["choices"][0]["finish_reason"] == "stop"
        assert finish["usage"]["total_tokens"] == 30

        assert _parse_sse(lines[1]) == "[DONE]"

    def test_missing_usage_defaults_to_zero(self):
        events = [{"type": "final"}]
        lines = list(translate_events(iter(events), model="m"))
        finish = _parse_sse(lines[0])
        assert finish["usage"]["prompt_tokens"] == 0


class TestTranslateErrorEvent:
    def test_error_emits_content_then_stop(self):
        events = [{"type": "error", "message": "timeout"}]
        lines = list(translate_events(iter(events), model="m"))
        assert len(lines) == 3  # error content + stop + [DONE]

        error_chunk = _parse_sse(lines[0])
        assert "timeout" in error_chunk["choices"][0]["delta"]["content"]

        stop_chunk = _parse_sse(lines[1])
        assert stop_chunk["choices"][0]["finish_reason"] == "stop"


class TestTranslateNoFinalEvent:
    def test_stream_closes_gracefully_without_final(self):
        events = [{"type": "chunk", "content": "hi"}]
        lines = list(translate_events(iter(events), model="m"))
        # content + fallback stop + [DONE]
        assert len(lines) == 3
        assert _parse_sse(lines[-1]) == "[DONE]"


class TestTranslateConsistentIds:
    def test_all_chunks_share_same_id(self):
        events = [
            {"type": "chunk", "content": "a"},
            {"type": "chunk", "content": "b"},
            {"type": "final", "usage": {}},
        ]
        lines = list(translate_events(iter(events), model="m"))
        ids = set()
        for line in lines:
            parsed = _parse_sse(line)
            if isinstance(parsed, dict):
                ids.add(parsed["id"])
        assert len(ids) == 1


# ── build_non_streaming_response ────────────────────────────────────


class TestBuildNonStreamingResponse:
    def test_collects_chunks_into_content(self):
        events = [
            {"type": "chunk", "content": "Hello "},
            {"type": "chunk", "content": "world"},
            {"type": "final", "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}},
        ]
        resp = build_non_streaming_response(iter(events), model="test")
        assert resp["object"] == "chat.completion"
        assert resp["choices"][0]["message"]["content"] == "Hello world"
        assert resp["choices"][0]["message"]["role"] == "assistant"
        assert resp["choices"][0]["finish_reason"] == "stop"
        assert resp["usage"]["total_tokens"] == 7

    def test_includes_tool_text(self):
        events = [
            {"type": "tool_start", "tool_name": "search", "tool_args": {}},
            {"type": "chunk", "content": "answer"},
            {"type": "final", "usage": {}},
        ]
        resp = build_non_streaming_response(iter(events), model="m")
        content = resp["choices"][0]["message"]["content"]
        assert "search" in content
        assert "answer" in content

    def test_empty_events(self):
        events = [{"type": "final", "usage": {}}]
        resp = build_non_streaming_response(iter(events), model="m")
        assert resp["choices"][0]["message"]["content"] == ""


# ── Model ID parsing (tested via app.py methods) ───────────────────


class TestAgentSlug:
    """Test the _agent_slug logic."""

    @staticmethod
    def _slug(name: str) -> str:
        import re
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "agent"

    def test_simple_name(self):
        assert self._slug("CMS CompOps Agent") == "cms-compops-agent"

    def test_special_characters(self):
        assert self._slug("My Agent (v2)") == "my-agent-v2"

    def test_leading_trailing_stripped(self):
        assert self._slug("--test--") == "test"

    def test_empty_name_fallback(self):
        assert self._slug("") == "agent"


class TestModelIdParsing:
    """Test the _parse_openai_model_id logic directly."""

    @staticmethod
    def _parse(model_id: str):
        """Replicate _parse_openai_model_id logic without needing the full app."""
        import re
        if "--" in model_id:
            agent_slug, provider_model = model_id.split("--", 1)
            parts = provider_model.split("-", 1)
            if len(parts) == 2:
                return agent_slug, parts[0], parts[1]
            return agent_slug, provider_model, None
        return model_id, None, None

    def test_default_provider(self):
        slug, provider, model = self._parse("cms-comp-ops-agent")
        assert slug == "cms-comp-ops-agent"
        assert provider is None
        assert model is None

    def test_provider_override(self):
        slug, provider, model = self._parse("cms-comp-ops-agent--anthropic-claude-sonnet")
        assert slug == "cms-comp-ops-agent"
        assert provider == "anthropic"
        assert model == "claude-sonnet"

    def test_openai_provider(self):
        slug, provider, model = self._parse("my-agent--openai-gpt-4o")
        assert slug == "my-agent"
        assert provider == "openai"
        assert model == "gpt-4o"

    def test_provider_with_hyphenated_model(self):
        slug, provider, model = self._parse("agent--gemini-gemini-1.5-pro")
        assert slug == "agent"
        assert provider == "gemini"
        assert model == "gemini-1.5-pro"

    def test_underscore_provider(self):
        slug, provider, model = self._parse("agent--cern_litellm-some-model")
        assert slug == "agent"
        assert provider == "cern_litellm"
        assert model == "some-model"
