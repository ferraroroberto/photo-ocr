"""Cache hygiene of the served shell, checked in-process.

iOS Safari (and the installed PWA shell) once served a stale ``index.html``
that referenced a ``?v=<old hash>`` module that no longer existed, which
rendered a blank app after a deploy. These pin the contract that prevents it:

1. ``/`` is always revalidated.
2. ``/static/*.{css,js}`` is immutable for a year, so the version stamp pays off.
3. The ``?v=<hash>`` stamped into the served ``index.html`` matches the
   on-disk hash of each asset.
4. ``/api/version`` returns the keys the Settings build line reads.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.webapp.routers._helpers import STATIC_DIR
from src.static_versioning import compute_asset_hashes

_INDEX_ASSET_RE = re.compile(
    r"""(?:href|src)=['"]/static/(?P<name>[\w\-./]+\.(?:css|js))"""
    r"""\?v=(?P<hash>[a-f0-9]+)['"]"""
)


def test_index_is_revalidated(client: TestClient) -> None:
    cc = client.get("/").headers.get("cache-control", "")
    assert "no-cache" in cc and "must-revalidate" in cc


def test_static_assets_are_immutable(client: TestClient) -> None:
    stamp = compute_asset_hashes(STATIC_DIR)["main.js"]
    resp = client.get(f"/static/main.js?v={stamp}")
    assert resp.status_code == 200
    cc = resp.headers.get("cache-control", "")
    assert "immutable" in cc and "max-age=31536000" in cc


def test_served_index_hashes_match_disk(client: TestClient) -> None:
    served = {
        m.group("name"): m.group("hash")
        for m in _INDEX_ASSET_RE.finditer(client.get("/").text)
    }
    assert served, "no hashed /static/*.{css,js} references in served index.html"
    on_disk = compute_asset_hashes(STATIC_DIR)
    for name, stamp in served.items():
        assert on_disk.get(name) == stamp, f"{name}: served stamp != on-disk hash"


def test_api_version_shape(client: TestClient) -> None:
    body = client.get("/api/version").json()
    for key in ("git_sha", "built_at", "asset_hash"):
        assert isinstance(body.get(key), str) and body[key], key
