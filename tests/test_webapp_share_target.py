"""The Android share-sheet target: manifest entry, worker route, fallback.

The share POST carries no bearer token, so the server half must stay
reachable without one and must never take the photos itself: the service
worker parks them for the page, and the fallback route only redirects.
TestClient's host is non-loopback, so a configured token gates every call
here exactly as it gates a phone on the tunnel.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.webapp.routers._helpers import STATIC_DIR
from app.webapp.routers.misc import SHARE_TARGET_PATH
from app.webapp.server import create_app
from src.archive import SessionArchive
from tests.test_webapp_api_auth import _set_token


def _js_const(source: str, name: str) -> str:
    match = re.search(rf"const {name} = '([^']*)';", source)
    assert match, f"{name} not declared"
    return match.group(1)


@pytest.fixture
def gated_client(tmp_path: Path):
    app = create_app()
    app.state.archive = SessionArchive(root=tmp_path / "archive")
    _set_token(app, token="secret")
    with TestClient(app) as client:
        yield client, app


def test_manifest_share_target_matches_worker() -> None:
    manifest = json.loads((STATIC_DIR / "manifest.webmanifest").read_text(encoding="utf-8"))
    target = manifest["share_target"]
    sw = (STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    page = (STATIC_DIR / "share_inbox.js").read_text(encoding="utf-8")

    assert target["method"] == "POST"
    assert target["enctype"] == "multipart/form-data"
    assert target["action"] == SHARE_TARGET_PATH == _js_const(sw, "SHARE_PATH")
    (files_param,) = target["params"]["files"]
    assert files_param["name"] == _js_const(sw, "SHARE_FIELD")
    assert files_param["accept"] == ["image/*"]
    # The worker writes what the page reads.
    for name in ("SHARE_CACHE", "SHARE_ENTRY", "SHARE_PARAM"):
        assert _js_const(sw, name) == _js_const(page, name), name


def test_worker_served_from_root_without_token(gated_client) -> None:
    client, _ = gated_client
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    # Revalidated, so a deploy's worker change is picked up on the next check.
    assert "no-cache" in r.headers["cache-control"]
    assert r.text == (STATIC_DIR / "sw.js").read_text(encoding="utf-8")


def test_fallback_redirects_without_storing_photos(gated_client) -> None:
    client, app = gated_client
    r = client.post(
        SHARE_TARGET_PATH,
        files=[("photos", ("a.jpg", b"\xff\xd8\xff\xe0not-really-a-jpeg", "image/jpeg"))],
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/?share-target=missed"
    # An unauthenticated caller must not be able to put anything in the archive.
    assert app.state.archive.list_sessions() == []


def test_share_exemption_is_exact(gated_client) -> None:
    """Only the share action itself is open: its neighbours still need the token."""
    client, _ = gated_client
    assert client.get("/share-target/x").status_code == 401
    assert client.post("/api/sessions").status_code == 401
