"""ETag + 304 on ``/`` so a relaunch doesn't re-download the page.

Issue #128 (perf-review): the entry document never answered a repeat with
304. A 304 must never outlive a build.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.webapp.routers import _helpers, misc


def test_index_carries_weak_etag(client: TestClient) -> None:
    resp = client.get("/")
    assert re.fullmatch(r'W/"[0-9a-f]{20}"', resp.headers.get("etag", ""))


def test_matching_if_none_match_answers_304(client: TestClient) -> None:
    etag = client.get("/").headers["etag"]
    resp = client.get(
        "/", headers={"If-None-Match": etag, "Accept-Encoding": "gzip"}
    )
    assert resp.status_code == 304
    assert resp.content == b""
    assert resp.headers["etag"] == etag
    # Still revalidates next launch, and a bodyless 304 is never gzipped.
    assert "no-cache" in resp.headers.get("cache-control", "")
    assert "content-encoding" not in resp.headers


@pytest.mark.parametrize(
    "wrap",
    [lambda e: e[2:], lambda e: "*", lambda e: f'"zzz", {e}'],
    ids=["strong-form", "star", "list"],
)
def test_if_none_match_variants_answer_304(client: TestClient, wrap) -> None:
    etag = client.get("/").headers["etag"]
    resp = client.get("/", headers={"If-None-Match": wrap(etag)})
    assert resp.status_code == 304


def test_stale_if_none_match_answers_200_with_body(client: TestClient) -> None:
    resp = client.get("/", headers={"If-None-Match": 'W/"0000000000"'})
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert resp.text


def test_new_commit_invalidates_etag(client: TestClient, monkeypatch) -> None:
    old = client.get("/").headers["etag"]
    monkeypatch.setattr(_helpers.BUILD_INFO, "git_sha", "feedbee")
    resp = client.get("/", headers={"If-None-Match": old})
    assert resp.status_code == 200
    assert resp.headers["etag"] != old


def test_changed_asset_invalidates_etag(client: TestClient, monkeypatch) -> None:
    """The stamped HTML only names some assets; an edit to a module reached
    through ``import`` must still move the validator."""
    old = client.get("/").headers["etag"]
    hashes = {k: "ffffffff" for k in _helpers.BUILD_INFO.asset_hashes}
    monkeypatch.setattr(_helpers.BUILD_INFO, "asset_hashes", hashes)
    resp = client.get("/", headers={"If-None-Match": old})
    assert resp.status_code == 200
    assert resp.headers["etag"] != old


def test_edited_index_invalidates_etag(
    client: TestClient, monkeypatch, tmp_path
) -> None:
    old = client.get("/").headers["etag"]
    edited = tmp_path / "static"
    edited.mkdir()
    (edited / "index.html").write_text(
        (misc.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        + "<!-- edit -->",
        encoding="utf-8",
    )
    monkeypatch.setattr(misc, "STATIC_DIR", edited)
    resp = client.get("/", headers={"If-None-Match": old})
    assert resp.status_code == 200
    assert resp.headers["etag"] != old
