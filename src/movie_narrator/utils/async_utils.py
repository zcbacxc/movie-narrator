import asyncio
import atexit
import concurrent.futures
import functools
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="movie-narrator-async")
atexit.register(lambda: _executor.shutdown(wait=False))


def run_async(coro: Coroutine[Any, Any, T], timeout: float = 300) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(asyncio.wait_for(coro, timeout))

    fut = _executor.submit(functools.partial(asyncio.run, asyncio.wait_for(coro, timeout)))
    try:
        return fut.result(timeout=timeout + 10)  # grace period beyond inner timeout
    except concurrent.futures.TimeoutError:
        fut.cancel()
        raise TimeoutError(f"Async task timeout ({timeout}s)")
    except Exception as e:
        fut.cancel()
        raise RuntimeError(f"Async execution failed: {e}") from e
