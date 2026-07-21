"""Regression tests for non-streaming agent-trace persistence.

Background: ``ChatWrapper.stream()`` creates and finalizes an ``agent_traces``
row live — that row is what the UI "agent activity" panel reads (per assistant
message id) via ``/api/trace`` and via the ``load_conversation`` trace_map.
``ChatWrapper.__call__`` (the shared non-streaming path that the playbook
scheduler and plain REST chat drive) called ``self.archi(...)`` and never wrote
a trace, so scheduled runs showed NO tool activity even when tools really ran.

These tests pin the fix: ``__call__`` synthesizes a trace post-hoc from the
final ``PipelineOutput`` (via ``extract_tool_calls()``), matching the exact
event shapes the panel already renders, and persists exactly ONE trace — while
the shared finalize path stays trace-free so streamed requests never double up.

Mocks follow the ``test_chat_wrapper_playbooks.py`` idiom: a bare ChatWrapper
via ``__new__`` with only what the tested methods touch, and the trace DB
helpers (``create_agent_trace`` / ``update_agent_trace``) spied so no real DB is
needed.
"""
from datetime import datetime, timezone

import pytest

pytest.importorskip("flask", reason="chat app import chain needs flask")
pytest.importorskip("langchain_core", reason="PipelineOutput needs langchain_core")

from unittest.mock import MagicMock, patch

from src.archi.utils.output_dataclass import PipelineOutput
from src.interfaces.chat_app.app import ChatWrapper


def _wrapper() -> ChatWrapper:
    wrapper = ChatWrapper.__new__(ChatWrapper)
    wrapper.pg_config = {"host": "unused-in-tests"}
    return wrapper


def _tool_output(result="RESULT BODY", name="rucio_events_aggregation",
                 args=None, call_id="call_abc"):
    """A PipelineOutput carrying one completed tool call (AIMessage tool_calls +
    matching ToolMessage), the shape an agent pipeline returns."""
    from langchain_core.messages import AIMessage, ToolMessage
    ai = AIMessage(content="", tool_calls=[
        {"name": name, "args": args or {"window": "24h"}, "id": call_id,
         "type": "tool_call"}])
    tm = ToolMessage(content=result, tool_call_id=call_id)
    return PipelineOutput(answer="done", messages=[ai, tm])


def _zero_tool_output():
    """A classic (non-agent) pipeline result: an answer, no tool messages."""
    return PipelineOutput(answer="just an answer", messages=[])


# ── pure event builder: shape parity with the stored streamed traces ──────────

def test_build_events_tool_result_matches_ui_shape():
    wrapper = _wrapper()
    events, count = wrapper._build_nonstreaming_trace_events(
        _tool_output(), conversation_id=55, timestamp="2026-01-01T00:00:00+00:00")

    assert count == 1
    assert [e["type"] for e in events] == ["tool_start", "tool_output"]

    start, out = events
    # tool_start: the fields addHistoricalToolStep reads (name + args + id)
    assert start["tool_call_id"] == "call_abc"
    assert start["tool_name"] == "rucio_events_aggregation"
    assert start["tool_args"] == {"window": "24h"}
    assert start["timestamp"] == "2026-01-01T00:00:00+00:00"
    # tool_output: the fields updateHistoricalToolStep reads (output keyed by id)
    assert out["tool_call_id"] == "call_abc"
    assert out["output"] == "RESULT BODY"
    assert out["truncated"] is False
    assert out["timestamp"] == "2026-01-01T00:00:00+00:00"


def test_build_events_zero_tools_is_empty():
    wrapper = _wrapper()
    events, count = wrapper._build_nonstreaming_trace_events(
        _zero_tool_output(), conversation_id=55, timestamp="t")
    assert events == []
    assert count == 0


def test_build_events_truncates_long_output_like_the_formatter():
    wrapper = _wrapper()
    body = "x" * 3239
    events, _ = wrapper._build_nonstreaming_trace_events(
        _tool_output(result=body), conversation_id=1, timestamp="t")
    out = events[1]
    assert out["truncated"] is True
    assert out["full_length"] == 3239
    assert out["output"].endswith("...")
    assert len(out["output"]) <= 800


def test_build_events_synthesizes_id_when_tool_call_lacks_one():
    """A truthy tool_call_id is required for the renderer to count/key the step;
    an id-less tool call (empty id, no matching ToolMessage) still yields a
    renderable, uniquely-keyed pair whose start and output share the same id."""
    from langchain_core.messages import AIMessage
    ai = AIMessage(content="", tool_calls=[
        {"name": "some_tool", "args": {"q": 1}, "id": "", "type": "tool_call"}])
    out = PipelineOutput(answer="x", messages=[ai])
    wrapper = _wrapper()
    events, count = wrapper._build_nonstreaming_trace_events(out, 1, "t")
    assert count == 1
    assert events[0]["tool_call_id"]  # non-empty
    assert events[0]["tool_call_id"] == events[1]["tool_call_id"]


# ── persist helper: exactly one completed trace, best-effort ──────────────────

def _spy_trace(wrapper):
    return (patch.object(wrapper, "create_agent_trace", return_value="trace-1"),
            patch.object(wrapper, "update_agent_trace"))


def test_persist_creates_and_finalizes_one_completed_trace():
    wrapper = _wrapper()
    wrapper.archi = MagicMock()
    wrapper.archi.pipeline_name = "CMSCompOpsAgent"
    ctx = MagicMock(); ctx.conversation_id = 55
    ts = {"lock_acquisition_ts": datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
          "chain_finished_ts": datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc)}

    create_p, update_p = _spy_trace(wrapper)
    with create_p as create, update_p as update:
        tid = wrapper._persist_nonstreaming_trace(
            _tool_output(), ctx, [10, 11], ts)

    assert tid == "trace-1"
    create.assert_called_once()
    # user message id wired at creation (streaming leaves it NULL; we know it here)
    assert create.call_args.kwargs["user_message_id"] == 10
    assert create.call_args.kwargs["pipeline_name"] == "CMSCompOpsAgent"
    update.assert_called_once()
    kw = update.call_args.kwargs
    assert kw["status"] == "completed"
    assert kw["message_id"] == 11           # archi message id → load_conversation trace_map
    assert kw["total_tool_calls"] == 1
    assert kw["total_duration_ms"] == 2000  # from the timestamps dict
    assert [e["type"] for e in kw["events"]] == ["tool_start", "tool_output"]


def test_persist_zero_tool_still_creates_trace_with_empty_events():
    wrapper = _wrapper()
    wrapper.archi = MagicMock(); wrapper.archi.pipeline_name = "QAPipeline"
    ctx = MagicMock(); ctx.conversation_id = 7
    ts = {"lock_acquisition_ts": datetime(2026, 1, 1, tzinfo=timezone.utc),
          "chain_finished_ts": datetime(2026, 1, 1, tzinfo=timezone.utc)}

    create_p, update_p = _spy_trace(wrapper)
    with create_p as create, update_p as update:
        wrapper._persist_nonstreaming_trace(_zero_tool_output(), ctx, [20, 21], ts)

    create.assert_called_once()
    kw = update.call_args.kwargs
    assert kw["events"] == []
    assert kw["total_tool_calls"] == 0
    assert kw["status"] == "completed"


def test_persist_is_best_effort_on_store_failure():
    """A trace-store hiccup must never convert an already-saved response into an
    error: the helper swallows, logs, and returns None."""
    wrapper = _wrapper()
    wrapper.archi = MagicMock(); wrapper.archi.pipeline_name = None
    ctx = MagicMock(); ctx.conversation_id = 1
    ts = {"lock_acquisition_ts": datetime(2026, 1, 1, tzinfo=timezone.utc)}

    with patch.object(wrapper, "create_agent_trace",
                      side_effect=RuntimeError("agent_traces missing")):
        tid = wrapper._persist_nonstreaming_trace(_tool_output(), ctx, [1, 2], ts)

    assert tid is None


# ── __call__ end-to-end: exactly one trace on the non-streaming path ──────────

def _drive_call(wrapper, result):
    """Run __call__ with every heavy seam mocked, so only trace persistence is
    exercised. Mirrors the argument order runner.py / the REST handler use."""
    ctx = MagicMock(); ctx.conversation_id = 55
    wrapper._init_timestamps = MagicMock(return_value={
        "lock_acquisition_ts": datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)})
    wrapper._prepare_chat_context = MagicMock(return_value=(ctx, None))
    wrapper._resolve_config_name = MagicMock(return_value="cfg")
    wrapper.update_config = MagicMock()
    wrapper.archi = MagicMock(return_value=result)
    wrapper.archi.pipeline_name = "CMSCompOpsAgent"
    wrapper._finalize_result = MagicMock(return_value=("answer", [10, 11]))
    wrapper.number_of_queries = 0
    wrapper.cursor = None
    wrapper.conn = None
    return wrapper(["hi"], 55, "client-1", False,
                   datetime.now(timezone.utc), 0.0, 60.0, "cfg")


def test_call_persists_exactly_one_trace_for_tool_using_result():
    """RED before the fix (no trace written on __call__); GREEN after: one
    create + one finalize, events in the panel's tool_start/tool_output shape."""
    wrapper = _wrapper()
    create_p, update_p = _spy_trace(wrapper)
    with create_p as create, update_p as update:
        out, cid, mids, _ts, err = _drive_call(wrapper, _tool_output())

    assert err is None and cid == 55 and mids == [10, 11]
    create.assert_called_once()
    update.assert_called_once()
    kw = update.call_args.kwargs
    assert kw["status"] == "completed"
    assert kw["message_id"] == 11
    assert kw["total_tool_calls"] == 1
    assert [e["type"] for e in kw["events"]] == ["tool_start", "tool_output"]


def test_call_persists_empty_events_trace_for_zero_tool_result():
    wrapper = _wrapper()
    create_p, update_p = _spy_trace(wrapper)
    with create_p as create, update_p as update:
        _drive_call(wrapper, _zero_tool_output())

    create.assert_called_once()
    assert update.call_args.kwargs["events"] == []
    assert update.call_args.kwargs["total_tool_calls"] == 0


def test_call_trace_failure_does_not_break_the_response():
    """__call__ must still return the finalized answer even if the trace write
    raises — the response is already persisted at that point."""
    wrapper = _wrapper()
    with patch.object(wrapper, "create_agent_trace",
                      side_effect=RuntimeError("boom")), \
         patch.object(wrapper, "update_agent_trace"):
        out, cid, mids, _ts, err = _drive_call(wrapper, _tool_output())

    assert err is None
    assert out == "answer"
    assert mids == [10, 11]


# ── behavior parity: the SHARED finalize path must not create a trace ─────────

def test_finalize_result_does_not_create_a_trace():
    """stream() and __call__ both call _finalize_result. Trace creation lives in
    __call__ only — if it leaked into the shared finalize, streamed requests
    (which already build a trace live) would persist a SECOND one."""
    wrapper = _wrapper()
    ctx = MagicMock()
    ctx.conversation_id = 55
    ctx.history = []
    ctx.sender = "User"
    ctx.content = "hi"
    ctx.is_refresh = False
    wrapper.get_top_sources = MagicMock(return_value=[])
    wrapper.append_source_section = MagicMock(
        side_effect=lambda out, src, render_markdown: out)
    wrapper.prepare_context_for_storage = MagicMock(return_value="")
    wrapper.insert_conversation = MagicMock(return_value=[10, 11])
    wrapper.insert_tool_calls_from_output = MagicMock()
    now = datetime.now(timezone.utc)

    with patch.object(wrapper, "create_agent_trace") as create:
        wrapper._finalize_result(
            _tool_output(), context=ctx, server_received_msg_ts=now,
            timestamps={"lock_acquisition_ts": now}, render_markdown=False)

    create.assert_not_called()
