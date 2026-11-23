"""Named worker pools for the webapp, one per class of blocking work.

The OCR extract jobs run for minutes against the LLM hub. On the event loop's
one shared default pool they would sit in front of every short read, so they
get a pool of their own: a slow extraction can fill this pool, never the pool
the quick requests use. Each pool is a ``TimedExecutor`` so a request's
``Server-Timing`` header splits thread queueing from the work itself.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
from typing import Any, Callable, TypeVar

from src.request_timing import TimedExecutor

T = TypeVar("T")

# Eight threads: several people's captures at once, and well past what a hub that
# serves one model at a time can answer usefully.
EXTRACT_POOL = TimedExecutor("extract", 8, "extract")


async def run_in_pool(pool: TimedExecutor, fn: Callable[..., T], /, *args: Any) -> T:
    """Run ``fn(*args)`` on ``pool``, keeping the caller's context variables.

    ``asyncio.to_thread`` copies the context into the worker; ``run_in_executor``
    does not, so the copy is made here and the request's timing stays attached.
    """
    ctx = contextvars.copy_context()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(pool, functools.partial(ctx.run, fn, *args))
