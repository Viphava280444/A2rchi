"""
Tests for three related MCP timeout / recursion-limit bugs, all under the
same "a turn that runs into trouble should still surface whatever the agent
already gathered" umbrella:

Bug 1 (mcp_utils.py -- AsyncLoopThread.run): the original code raised
TimeoutError in the CALLING thread but never cancelled the coroutine on the
shared background loop -- it kept running to completion and the next queued
tool call inherited its delay. The fix keeps the deadline in the calling
thread (future.result(timeout=...), so it holds even when the coroutine
blocks the loop with synchronous work) and, on expiry, calls future.cancel()
to request cancellation on the loop, raising McpCallTimeout -- a dedicated
type, so a TimeoutError raised by the coroutine ITSELF (e.g. a transport
connect timeout) is re-raised untouched instead of being relabelled as the
deadline. Honest limits: cancellation lands at the coroutine's next await
point; code stuck in blocking synchronous work cannot be interrupted, and
the MCP protocol offers the server no cancellation signal, so server-side
work may continue after the client gives up.

Bug 2 (base_react.py -- sync_wrapper): when AsyncLoopThread.run() raises on
its deadline, the exception used to propagate out of the tool and kill the
whole agent turn with an HTTP 500. The fix catches exactly McpCallTimeout
and raises ToolException; initialize_mcp_client sets handle_tool_error=True
on every MCP tool, so BaseTool.run() converts that into a ToolMessage with
status="error" -- on the sync and async execution paths alike -- and the
model can act on data it already gathered. All other tool errors, including
the tool's own TimeoutError, propagate unchanged.

Bug 3 (base_react.py -- BaseReActAgent.invoke): when the graph exhausts its
recursion limit, invoke() caught GraphRecursionError and called the wrap-up
fallback with an empty message list. self.agent.invoke() raises without
returning, so whatever messages/tool results the graph had accumulated were
never in scope to hand to the fallback -- the wrap-up model then had nothing
to summarize and confabulated a narrative instead of reporting the numbers
the agent had already gathered. The fix drives the graph with
self.agent.stream(..., stream_mode="values") instead of .invoke(), keeping
the last emitted state so it survives a GraphRecursionError. The streaming
path (BaseReActAgent.stream) already did this correctly; this is the sync
invoke() path catching up to it.
"""
import asyncio
import inspect
import time

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from src.archi.pipelines.agents.utils.mcp_utils import AsyncLoopThread
from src.archi.pipelines.agents import base_react
from src.archi.utils.output_dataclass import PipelineOutput


@pytest.fixture
def runner():
    """A fresh background-loop runner, independent of the process-wide
    singleton, shut down after the test."""
    r = AsyncLoopThread()
    yield r
    r.shutdown()


# ---------------------------------------------------------------------------
# Bug 1: AsyncLoopThread.run() must actually cancel the coroutine on timeout,
# not merely raise in the caller while the orphan keeps running.
# ---------------------------------------------------------------------------

def test_timeout_actually_cancels_the_coroutine(runner):
    """The whole point of bug 1: a test that only checks the caller's
    exception would pass even against the broken code, because the orphaned
    coroutine happily runs to completion in the background and nobody
    notices -- which is exactly the cascade that produced the death clusters.
    So this asserts the coroutine itself stopped, not just that the caller
    got an exception."""
    marker = {"completed": False, "cancelled": False}

    async def slow():
        try:
            await asyncio.sleep(0.4)
            marker["completed"] = True
        except asyncio.CancelledError:
            marker["cancelled"] = True
            raise

    with pytest.raises(TimeoutError):
        runner.run(slow(), timeout=0.1)

    # Wait past the coroutine's 0.4s sleep. Against the broken code the
    # orphan is still running and will flip "completed" by now; against the
    # fix it was cancelled at ~0.1s and never gets there.
    time.sleep(0.6)

    assert marker["cancelled"] is True
    assert marker["completed"] is False


def test_fast_coroutine_still_returns_normally(runner):
    async def fast():
        await asyncio.sleep(0.01)
        return "ok"

    assert runner.run(fast(), timeout=0.5) == "ok"


def test_timeout_none_still_waits_forever(runner):
    async def slowish():
        await asyncio.sleep(0.3)
        return "done"

    # timeout=None must not be wrapped in wait_for and must not raise, even
    # though 0.3s exceeds the short timeouts used elsewhere in this file.
    assert runner.run(slowish(), timeout=None) == "done"


def test_default_timeout_is_still_120_seconds():
    """Other callers depend on the 120s default; the fix must not change it."""
    sig = inspect.signature(AsyncLoopThread.run)
    assert sig.parameters["timeout"].default == 120.0


def test_deadline_raises_even_if_coroutine_swallows_cancellation(runner):
    """A tool with a broad `except` around its request loop can absorb the
    CancelledError that cancellation delivers. The runner must still raise
    to its caller -- silently returning the late value would present a call
    that blew its deadline as a success."""

    async def stubborn():
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            return "finished anyway"

    with pytest.raises(TimeoutError):
        runner.run(stubborn(), timeout=0.1)


def test_coroutines_own_timeout_error_is_not_relabelled(runner):
    """A TimeoutError raised INSIDE the coroutine (e.g. a transport-level
    connect timeout, which on py3.10+ is the same class family) is a real
    tool error, not the runner's deadline. It must surface with its own
    message intact, not be relabelled as 'Operation exceeded 120.0s'."""

    async def transport_fail():
        raise TimeoutError("connection to dbs server timed out after 3s")

    with pytest.raises(TimeoutError, match="dbs server"):
        runner.run(transport_fail(), timeout=120.0)


def test_deadline_timeout_is_a_distinct_type(runner):
    """The runner's own deadline raises McpCallTimeout (a TimeoutError
    subclass), so callers can catch exactly the deadline case and let a
    tool's own TimeoutError propagate untouched."""
    from src.archi.pipelines.agents.utils.mcp_utils import McpCallTimeout

    async def slow():
        await asyncio.sleep(5)

    with pytest.raises(McpCallTimeout):
        runner.run(slow(), timeout=0.05)


# ---------------------------------------------------------------------------
# Bug 2: sync_wrapper (base_react.py) must convert the runner's deadline into
# a ToolException instead of letting it kill the whole agent turn.
# initialize_mcp_client sets handle_tool_error=True on every MCP tool
# (src/archi/pipelines/agents/tools/mcp.py), so BaseTool.run() turns a
# ToolException into a ToolMessage with status="error" -- no special return
# shape needed, on the sync and async paths alike.
# ---------------------------------------------------------------------------

class _FakeAsyncTool:
    """The smallest stand-in for the langchain BaseTool object make_synchronous
    wraps. sync_wrapper only ever touches .name, .coroutine, and (by
    assignment) .func, so a real MCP tool is not needed to exercise it.
    (The end-to-end test below uses a real StructuredTool instead, to pin the
    full handle_tool_error path.)"""

    def __init__(self, name, coroutine_fn):
        self.name = name
        self.coroutine = coroutine_fn
        self.func = None


def _shorten_runner_deadline(monkeypatch, seconds=0.2):
    """Make the process-wide runner's default deadline short for this test,
    so the REAL deadline path in AsyncLoopThread.run() fires -- rather than
    faking the timeout inside the tool coroutine, which would never exercise
    the runner's own except clause. sync_wrapper calls runner.run(coro) with
    no timeout argument, so overriding the instance's run() default is the
    only seam."""
    inst = AsyncLoopThread.get_instance()
    orig_run = AsyncLoopThread.run

    def short_run(coro, timeout=120.0):
        return orig_run(inst, coro, timeout=seconds)

    monkeypatch.setattr(inst, "run", short_run)


def _make_sync_wrapper(monkeypatch, coroutine_fn, tool_name="fake_mcp_tool", tool=None):
    """Build a REAL sync_wrapper via BaseReActAgent._build_mcp_tools(), faking
    out only initialize_mcp_client() so no network/subprocess MCP server is
    required. Everything downstream of that -- make_synchronous,
    sanitize_value, and (this test's target) the timeout handling around
    runner.run() -- is the actual production code, unmodified.

    Constructing a full BaseReActAgent is impractical here: __init__ requires
    real LLM/provider config. BaseReActAgent.__new__(BaseReActAgent) (bypassing
    __init__) is the existing precedent for this in
    tests/unit/test_playbook_tools.py (_mixin_agent, test_base_agent_has_no_playbook_tools).
    _build_mcp_tools() itself only *sets* instance attributes it needs (async
    runner, mcp client, skills text) -- it never reads any attribute __init__
    would have set -- so the bypass is safe.
    """
    fake_tool = tool if tool is not None else _FakeAsyncTool(tool_name, coroutine_fn)

    async def fake_initialize_mcp_client():
        return (object(), [fake_tool], "")

    monkeypatch.setattr(base_react, "initialize_mcp_client", fake_initialize_mcp_client)

    agent = base_react.BaseReActAgent.__new__(base_react.BaseReActAgent)
    tools = agent._build_mcp_tools()
    assert tools, "expected _build_mcp_tools() to wrap the fake tool"
    return tools[0].func


def test_sync_wrapper_raises_tool_exception_naming_the_tool_on_timeout(monkeypatch):
    """The REAL deadline path: the tool coroutine just hangs, the runner's
    own (shortened) deadline fires, and sync_wrapper must convert exactly
    that into a ToolException naming the tool -- which handle_tool_error
    then turns into a status='error' ToolMessage instead of a dead turn."""
    from langchain_core.tools import ToolException

    async def hangs(*args, **kwargs):
        await asyncio.sleep(5)

    sync_wrapper = _make_sync_wrapper(monkeypatch, hangs, tool_name="dbs_find_files")
    _shorten_runner_deadline(monkeypatch)

    with pytest.raises(ToolException, match="dbs_find_files"):
        sync_wrapper()


def test_sync_wrapper_timeout_yields_error_tool_message_end_to_end(monkeypatch):
    """Full-stack check with a REAL StructuredTool configured exactly like
    production: response_format="content_and_artifact" (hard-coded for every
    MCP tool by langchain_mcp_adapters) and handle_tool_error=True (set by
    initialize_mcp_client). Invoking the tool after its coroutine times out
    must produce a ToolMessage with status='error' naming the tool -- not a
    success-shaped result, and not a crash."""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel

    class _NoArgs(BaseModel):
        pass

    async def hangs(*args, **kwargs):
        await asyncio.sleep(5)

    real_tool = StructuredTool(
        name="dbs_aggregate",
        description="fake DBS aggregation tool",
        coroutine=hangs,
        args_schema=_NoArgs,
        response_format="content_and_artifact",
    )
    real_tool.handle_tool_error = True  # what initialize_mcp_client does

    _make_sync_wrapper(monkeypatch, None, tool=real_tool)
    _shorten_runner_deadline(monkeypatch)

    msg = real_tool.invoke(
        {"type": "tool_call", "id": "call_1", "name": "dbs_aggregate", "args": {}}
    )

    assert isinstance(msg, ToolMessage)
    assert msg.status == "error"
    assert "dbs_aggregate" in msg.content


def test_sync_wrapper_reraises_tools_own_timeout_error(monkeypatch):
    """A TimeoutError raised by the tool's own coroutine (e.g. a transport
    connect timeout) is a real tool error, not the runner's deadline. It
    must propagate unchanged -- not be dressed up as a retryable deadline
    message that sends the model retrying against a dead server."""

    async def transport_fail(*args, **kwargs):
        raise TimeoutError("connection reset by dbs server")

    sync_wrapper = _make_sync_wrapper(monkeypatch, transport_fail)

    with pytest.raises(TimeoutError, match="connection reset"):
        sync_wrapper()


def test_sync_wrapper_does_not_swallow_non_timeout_errors(monkeypatch):
    async def boom(*args, **kwargs):
        raise ValueError("real tool bug, not a timeout")

    sync_wrapper = _make_sync_wrapper(monkeypatch, boom)

    with pytest.raises(ValueError, match="real tool bug"):
        sync_wrapper()


# ---------------------------------------------------------------------------
# Bug 3: BaseReActAgent.invoke() must not discard gathered messages when the
# graph hits its recursion limit. It used to call the fallback handler with
# latest_messages=[] because self.agent.invoke() raises GraphRecursionError
# without returning, so nothing the run had gathered was still in scope. The
# fix drives the graph via self.agent.stream(..., stream_mode="values") and
# keeps the last emitted state, mirroring what BaseReActAgent.stream()
# already does for the streaming path.
# ---------------------------------------------------------------------------

class _FakeCompiledGraph:
    """Stand-in for the CompiledStateGraph create_agent() returns.

    Faithfully mirrors the langgraph==1.0.1 Pregel contract this fix depends
    on (verified by reading langgraph.pregel.main.Pregel.invoke/.stream
    source directly, not assumed): .invoke() is implemented purely in terms
    of .stream(..., stream_mode="values") -- it keeps the last yielded state
    and returns it, and if the stream raises partway through, that local
    state is lost when the exception unwinds .invoke()'s own stack. That is
    the production bug in miniature.

    A fake that instead special-cased .invoke() -- e.g. by omitting it, or
    by having it catch its own exception and return the last state anyway
    -- would not exercise the real failure mode, and would repeat the
    mistake documented at the top of this file: the previous fix in this
    file passed its tests and still broke live because its test double
    didn't match the real object's shape (response_format). Real compiled
    graphs always have a working .invoke(); this one does too, and it loses
    state on error exactly like the real one, which is what makes the RED
    result below mean something.
    """

    # Mirrors CompiledStateGraph: the OUTPUT-schema channels, a subset of
    # all stream channels (a real create_agent graph also carries internal
    # ones like 'jump_to' that invoke() never returns).
    output_channels = ["messages", "structured_response"]

    def __init__(self, states, error=None):
        self._states = list(states)
        self._error = error
        self.stream_calls = []

    def stream(self, input, config=None, *, stream_mode=None, output_keys=None, **kwargs):
        assert stream_mode == "values", (
            "the fix must drive the graph with stream_mode='values'"
        )
        self.stream_calls.append(
            {"input": input, "config": config, "output_keys": output_keys}
        )
        for state in self._states:
            yield state
        if self._error is not None:
            raise self._error

    def invoke(self, input, config=None, **kwargs):
        """Mirrors Pregel.invoke(): keep the last 'values' state and return
        it -- but if .stream() raises partway through, that local `last` is
        discarded when the exception propagates, same as the real thing."""
        last = None
        for last in self.stream(input, config=config, stream_mode="values"):
            pass
        return last


def _make_bare_agent(graph, recursion_limit=5, agent_inputs=None):
    """Build a BaseReActAgent with just enough wired up to exercise
    invoke()'s try/except around the graph call. __init__ needs real
    LLM/provider config, so bypass it via __new__ -- the same precedent
    test_playbook_tools.py uses for BaseReActAgent (_mixin_agent /
    test_base_agent_has_no_playbook_tools). _prepare_agent_inputs and
    _recursion_limit are stubbed directly because their own logic (token
    trimming, config parsing) is unrelated to this bug; only the try/except
    block and its interaction with self.agent / self._handle_recursion_limit_error
    is under test."""
    agent = base_react.BaseReActAgent.__new__(base_react.BaseReActAgent)
    agent.agent = graph
    agent._active_memory = None
    fixed_inputs = dict(agent_inputs or {"messages": []})
    agent._prepare_agent_inputs = lambda **kwargs: fixed_inputs
    agent._recursion_limit = lambda: recursion_limit
    return agent


def test_invoke_passes_gathered_messages_to_recursion_handler():
    """The core regression test: when the graph raises GraphRecursionError
    after producing real messages (a question, a tool call, and a tool
    result with actual numbers in it -- mirroring the production trace),
    the fallback handler must receive those messages, not an empty list.

    Fails against current HEAD: the handler is called with
    latest_messages=[] no matter what the graph gathered, because
    self.agent.invoke() raises without returning."""
    human = HumanMessage(content="how big is campaign X?")
    ai_tool_call = AIMessage(
        content="",
        tool_calls=[{"name": "dbs_find_files", "args": {"dataset": "/X"}, "id": "call_1"}],
    )
    tool_result = ToolMessage(content="1234 files, 56.7 TB", tool_call_id="call_1")

    states = [
        {"messages": [human]},
        {"messages": [human, ai_tool_call]},
        {"messages": [human, ai_tool_call, tool_result]},
    ]
    graph = _FakeCompiledGraph(states, error=GraphRecursionError("recursion limit hit"))
    agent = _make_bare_agent(graph)

    captured = {}

    def fake_handler(**kwargs):
        captured.update(kwargs)
        return "FALLBACK_OUTPUT"

    agent._handle_recursion_limit_error = fake_handler

    result = agent.invoke()

    assert result == "FALLBACK_OUTPUT"
    assert "latest_messages" in captured
    got = list(captured["latest_messages"])
    assert got != [], "handler must not get an empty list when the graph gathered real messages"
    assert got == states[-1]["messages"]
    # The number the production trace showed missing from the fallback answer.
    assert any("56.7 TB" in getattr(m, "content", "") for m in got)


def test_invoke_success_path_unchanged():
    """A normal run (no recursion limit hit) must return the exact same
    PipelineOutput as before: answer text from the final AI message, the
    full accumulated message list, empty metadata, final=True.

    Passes against current HEAD too -- this is a regression guard pinning
    down that the fix only changes behavior on the exception path."""
    human = HumanMessage(content="how big is campaign X?")
    ai_tool_call = AIMessage(
        content="",
        tool_calls=[{"name": "dbs_find_files", "args": {"dataset": "/X"}, "id": "call_1"}],
    )
    tool_result = ToolMessage(content="1234 files, 56.7 TB", tool_call_id="call_1")
    final_answer = AIMessage(content="Campaign X has 1234 files totaling 56.7 TB.")

    final_messages = [human, ai_tool_call, tool_result, final_answer]
    states = [
        {"messages": [human]},
        {"messages": [human, ai_tool_call]},
        {"messages": [human, ai_tool_call, tool_result]},
        {"messages": final_messages},
    ]
    graph = _FakeCompiledGraph(states, error=None)
    agent = _make_bare_agent(graph, recursion_limit=7)

    result = agent.invoke()

    assert isinstance(result, PipelineOutput)
    assert result.answer == "Campaign X has 1234 files totaling 56.7 TB."
    assert result.messages == final_messages
    assert result.metadata == {}
    assert result.final is True
    assert result.source_documents == []
    assert graph.stream_calls, "the fix must drive the graph via .stream(...)"
    assert graph.stream_calls[0]["config"] == {"recursion_limit": 7}


def test_invoke_pins_output_keys_to_the_graphs_output_channels():
    """Pregel.invoke() pins output_keys=self.output_channels before
    delegating to stream(); a bare stream() call falls back to ALL stream
    channels, which for a real create_agent graph adds internal routing
    keys like 'jump_to' to the returned state. invoke() must pass
    output_keys explicitly so its success-path return shape stays identical
    to what .invoke() produced before this change."""
    human = HumanMessage(content="q")
    final = AIMessage(content="a")
    graph = _FakeCompiledGraph([{"messages": [human, final]}], error=None)
    agent = _make_bare_agent(graph)

    agent.invoke()

    assert graph.stream_calls[0]["output_keys"] == graph.output_channels


def test_invoke_recursion_error_before_any_state_is_handled_gracefully():
    """If the graph raises before producing anything at all (e.g. the
    recursion limit is hit on the very first tick), the handler must get an
    empty list, not crash.

    Passes against current HEAD too, since the old code always passed [];
    this guards against a regression where the fix tries to read messages
    off a state that was never captured."""
    graph = _FakeCompiledGraph([], error=GraphRecursionError("no progress"))
    agent = _make_bare_agent(graph)

    captured = {}

    def fake_handler(**kwargs):
        captured.update(kwargs)
        return "FALLBACK_OUTPUT"

    agent._handle_recursion_limit_error = fake_handler

    result = agent.invoke()

    assert result == "FALLBACK_OUTPUT"
    assert captured["latest_messages"] == []
