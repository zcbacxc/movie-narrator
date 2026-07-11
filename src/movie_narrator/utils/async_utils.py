import asyncio
import atexit
import concurrent.futures
import functools
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")

ASYNC_TIMEOUT = 300
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="movie-narrator-async")
atexit.register(lambda: _executor.shutdown(wait=False))


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    fut = _executor.submit(functools.partial(asyncio.run, coro))
    try:
        return fut.result(timeout=ASYNC_TIMEOUT)
    except concurrent.futures.TimeoutError:
        fut.cancel()
        raise TimeoutError(f"Async task timeout ({ASYNC_TIMEOUT}s)")
    except Exception as e:
        fut.cancel()
        raise RuntimeError(f"Async execution failed: {e}") from e
