"""Helpers for sync wrappers that also work inside Jupyter's running loop."""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Coroutine


def run_sync[T](coro: Coroutine[object, object, T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # Jupyter / already-running loop: execute in a fresh loop on a worker thread.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        # The lambda (not ``pool.submit(asyncio.run, coro)``) lets type checkers
        # see that the result is ``T``.
        return pool.submit(lambda: asyncio.run(coro)).result()
