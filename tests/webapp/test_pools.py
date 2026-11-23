"""The extract pool: work runs on the named pool and keeps the caller's context."""

from __future__ import annotations

import asyncio
import contextvars
import threading

from app.webapp.pools import EXTRACT_POOL, run_in_pool

_marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker", default="unset")


def _read() -> tuple[str, str]:
    return _marker.get(), threading.current_thread().name


def test_run_in_pool_uses_the_named_pool_and_keeps_context() -> None:
    async def go() -> tuple[str, str]:
        _marker.set("request-1")
        return await run_in_pool(EXTRACT_POOL, _read)

    value, thread_name = asyncio.run(go())
    assert value == "request-1"
    assert thread_name.startswith("extract")


def test_run_in_pool_passes_args_and_propagates_errors() -> None:
    def boom(n: int) -> int:
        raise ValueError(n)

    async def go() -> None:
        assert await run_in_pool(EXTRACT_POOL, lambda a, b: a + b, 2, 3) == 5
        try:
            await run_in_pool(EXTRACT_POOL, boom, 7)
        except ValueError as exc:
            assert exc.args == (7,)
        else:
            raise AssertionError("the worker's exception was swallowed")

    asyncio.run(go())
