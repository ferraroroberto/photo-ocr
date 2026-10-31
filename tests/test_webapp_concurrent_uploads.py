"""Concurrent photo uploads to one session must not lose photos.

The PWA fires one upload per picked photo without awaiting between them. Starlette
reads a multipart part over 1 MB through the threadpool, so each request yields
mid-handler; without per-session serialisation every in-flight request hydrates its
own copy of the session and they overwrite each other's photo and meta.
"""

from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.webapp.server import create_app
from src.archive import SessionArchive

# Over Starlette's 1 MB spool threshold, the size that makes a part's read yield.
_MIN_PART_BYTES = 1_100_000


def _noisy_jpeg() -> bytes:
    img = Image.frombytes("RGB", (1200, 900), os.urandom(1200 * 900 * 3))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    data = buf.getvalue()
    assert len(data) > _MIN_PART_BYTES, "fixture must exceed the spool threshold"
    return data


@pytest.fixture
def app(tmp_path: Path):
    app = create_app()
    app.state.archive = SessionArchive(root=tmp_path / "archive")
    return app


async def test_concurrent_large_uploads_keep_every_photo(app) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        sid = (await client.post("/api/sessions", json={})).json()["session_id"]
        parts = [_noisy_jpeg() for _ in range(4)]

        async def upload(i: int, data: bytes) -> httpx.Response:
            return await client.post(
                f"/api/sessions/{sid}/photos",
                files=[("files", (f"p{i}.jpg", data, "image/jpeg"))],
            )

        responses = await asyncio.gather(*(upload(i, d) for i, d in enumerate(parts)))

    assert [r.status_code for r in responses] == [200] * 4
    added = sorted(r.json()["added"][0]["sequence_index"] for r in responses)
    assert added == [1, 2, 3, 4]

    session = app.state.archive.get(sid)
    assert sorted(p.sequence_index for p in session.meta.photos) == [1, 2, 3, 4]
    on_disk = sorted(p.name for p in session.folder.glob("*.jpg"))
    assert on_disk == ["01.jpg", "02.jpg", "03.jpg", "04.jpg"]


# Handlers that write a session's meta without the lock because the session is
# brand new and unreachable by any other request until they return.
_UNSHARED_SESSION_HANDLERS = {"extract_single_shot"}


def test_every_meta_writing_handler_takes_the_session_lock() -> None:
    """A route that hydrates a session and writes its meta back must hold
    ``session_lock``: that read-modify-write is the race the concurrent-upload
    test exercises, and a new handler would silently reintroduce it."""
    import ast

    from app.webapp.routers import sessions

    tree = ast.parse(Path(sessions.__file__).read_text(encoding="utf-8"))
    unlocked = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef) or not fn.decorator_list:
            continue
        calls = {
            c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")
            for c in ast.walk(fn)
            if isinstance(c, ast.Call)
        }
        writes_meta = bool(calls & {"write_meta", "set_incognito"})
        if writes_meta and "session_lock" not in calls and fn.name not in _UNSHARED_SESSION_HANDLERS:
            unlocked.append(fn.name)
    assert not unlocked, f"meta-writing handlers without session_lock: {unlocked}"
