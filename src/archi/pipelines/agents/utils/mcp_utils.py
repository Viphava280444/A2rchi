from typing import Optional, Any
import asyncio
import concurrent.futures
import threading
from src.utils.logging import get_logger

logger = get_logger(__name__)


class McpCallTimeout(TimeoutError):
    """Raised by AsyncLoopThread.run() when its own deadline expires.

    Deliberately a distinct type so callers can tell "the runner gave up
    waiting" apart from a TimeoutError raised inside the coroutine itself
    (e.g. a transport-level connect timeout), which run() re-raises
    untouched.
    """


class AsyncLoopThread:
    """
    A dedicated background thread running a single event loop.

    This ensures all async operations (MCP client init, tool calls) happen
    on the same event loop, preventing ClosedResourceError.

    Usage:
        runner = AsyncLoopThread.get_instance()
        result = runner.run(some_async_coroutine())
    """

    _instance: Optional["AsyncLoopThread"] = None
    _lock = threading.Lock()

    def __init__(self):
        self.loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._started = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="mcp-async-loop"
        )
        self.thread.start()

        # Wait for the loop to actually start before returning
        if not self._started.wait(timeout=10.0):
            raise RuntimeError("Failed to start async loop thread")
        logger.info("Background async loop started for MCP operations")

    def _run(self):
        """Run the event loop forever in the background thread."""
        asyncio.set_event_loop(self.loop)
        self._started.set()
        self.loop.run_forever()

    def run(self, coro, timeout: Optional[float] = 120.0) -> Any:
        """
        Schedule a coroutine on the background loop and wait for result.

        Args:
            coro: An awaitable coroutine
            timeout: Maximum seconds to wait (default 120s for MCP operations).
                Pass None to wait forever.

        Returns:
            The result of the coroutine

        Raises:
            McpCallTimeout: The deadline expired. Cancellation is requested
                on the loop: a coroutine waiting at an await point stops,
                but one stuck in blocking synchronous work cannot be
                interrupted and may keep running on the loop after this
                raises.
            Any exception raised by the coroutine (re-raised untouched,
                including the coroutine's own TimeoutError).
        """
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            # result(timeout=None) waits forever, so timeout=None needs no
            # special branch. The deadline is enforced here, in the calling
            # thread, so it holds even when the coroutine blocks the loop
            # with synchronous work (wait_for's loop-side timer could not).
            return future.result(timeout=timeout)
        except (asyncio.TimeoutError, concurrent.futures.TimeoutError) as exc:
            if future.done() and future.exception() is exc:
                # The coroutine itself raised a timeout-family error (e.g. a
                # transport connect timeout) before our deadline -- that is a
                # real tool error, not the runner's deadline. Re-raise as-is.
                raise
            future.cancel()
            logger.warning("MCP coroutine exceeded %ss deadline; cancellation requested", timeout)
            raise McpCallTimeout(f"Operation exceeded {timeout}s timeout") from None

    def in_loop_thread(self) -> bool:
        """Return True if called from the background event-loop thread."""
        return threading.current_thread() is self.thread
        # or: return threading.get_ident() == self.thread.ident

    @classmethod
    def get_instance(cls) -> "AsyncLoopThread":
        """Get or create the singleton async runner instance."""
        if cls._instance is None:
            with cls._lock:
                # Double-check locking pattern for thread safety
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def shutdown(self):
        """Gracefully shutdown the background loop."""
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5.0)
        logger.info("Background async loop stopped")
