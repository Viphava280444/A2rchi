"""
Tests for two related MCP timeout bugs:

Bug 1 (mcp_utils.py -- AsyncLoopThread.run): concurrent.futures.Future.result(
timeout=...) raises TimeoutError in the CALLING thread but does not cancel the
coroutine running on the shared background loop -- it keeps running to
completion (downloading/parsing data nobody will read), and the next queued
tool call inherits its delay. The fix wraps the coroutine in
asyncio.wait_for() so expiry cancels it natively on the loop.

Bug 2 (base_react.py -- sync_wrapper): when AsyncLoopThread.run() raises on
timeout, the exception used to propagate out of the tool and kill the whole
agent turn with an HTTP 500. The fix catches only the timeout and returns a
structured error string so the model can act on data it already gathered.
"""
import asyncio
import inspect
import time

import pytest

from src.archi.pipelines.agents.utils.mcp_utils import AsyncLoopThread
from src.archi.pipelines.agents import base_react


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


# ---------------------------------------------------------------------------
# Bug 2: sync_wrapper (base_react.py) must convert a tool timeout into a
# structured error string instead of letting it kill the whole agent turn.
# ---------------------------------------------------------------------------

class _FakeAsyncTool:
    """The smallest stand-in for the langchain BaseTool object make_synchronous
    wraps. sync_wrapper only ever touches .name, .coroutine, and (by
    assignment) .func, so a real MCP tool is not needed to exercise it."""

    def __init__(self, name, coroutine_fn):
        self.name = name
        self.coroutine = coroutine_fn
        self.func = None


def _make_sync_wrapper(monkeypatch, coroutine_fn, tool_name="fake_mcp_tool"):
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
    fake_tool = _FakeAsyncTool(tool_name, coroutine_fn)

    async def fake_initialize_mcp_client():
        return (object(), [fake_tool], "")

    monkeypatch.setattr(base_react, "initialize_mcp_client", fake_initialize_mcp_client)

    agent = base_react.BaseReActAgent.__new__(base_react.BaseReActAgent)
    tools = agent._build_mcp_tools()
    assert tools, "expected _build_mcp_tools() to wrap the fake tool"
    return tools[0].func


def test_sync_wrapper_returns_string_naming_the_tool_on_timeout(monkeypatch):
    async def times_out(*args, **kwargs):
        # Simulate a tool call that would eventually hit AsyncLoopThread's
        # (120s default) timeout, without a test actually waiting 120s: the
        # tool's own coroutine times out against a short internal deadline,
        # which is what runner.run() ultimately raises up through here.
        await asyncio.wait_for(asyncio.sleep(5), timeout=0.05)

    sync_wrapper = _make_sync_wrapper(monkeypatch, times_out, tool_name="dbs_find_files")

    result = sync_wrapper()

    assert isinstance(result, str)
    assert "dbs_find_files" in result


def test_sync_wrapper_does_not_swallow_non_timeout_errors(monkeypatch):
    async def boom(*args, **kwargs):
        raise ValueError("real tool bug, not a timeout")

    sync_wrapper = _make_sync_wrapper(monkeypatch, boom)

    with pytest.raises(ValueError, match="real tool bug"):
        sync_wrapper()
