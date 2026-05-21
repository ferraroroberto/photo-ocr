"""Cache-hygiene regression net — pins the four pillars of issue #5/#6.

iOS Safari (and especially the PWA-installed shell) used to serve a
stale ``index.html`` referencing a ``?v=<old hash>`` module that no
longer existed — symptom: a blank app after a deploy. These checks make
that class of bug fail loudly:

1. ``/`` is always revalidated.
2. ``/static/*.{css,js}`` is immutable for a year so the bust pays off.
3. The ``?v=<hash>`` stamped into served ``index.html`` matches the
   on-disk fleet hash — catches "edited a JS module, didn't restart".
4. ``/api/version`` returns the keys the Settings build-line relies on.

Non-browser: uses ``requests`` against the live tray. Both Playwright
projections would just hit the same loopback URLs, so this runs once on
the chromium projection.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import requests

from src.static_versioning import compute_asset_hashes

pytestmark = pytest.mark.smoke

_STATIC_DIR = Path(__file__).resolve().parents[2] / "app" / "webapp" / "static"
_INDEX_ASSET_RE = re.compile(
    r"""(?:href|src)=['"]/static/(?P<name>[\w\-.]+\.(?:css|js))"""
    r"""\?v=(?P<hash>[a-f0-9]+)['"]"""
)


@pytest.fixture(scope="session", autouse=True)
def _run_once(browser_name: str) -> None:
    # These checks are server-side, not browser-dependent. Skip the
    # second projection so the file isn't double-counted in the suite.
    if browser_name != "chromium":
        pytest.skip("server-side check; runs once on the chromium projection")


def test_index_is_revalidated(base_url: str) -> None:
    res = requests.get(f"{base_url}/", verify=False, timeout=5)
    res.raise_for_status()
    cc = res.headers.get("Cache-Control", "")
    assert "no-cache" in cc and "must-revalidate" in cc, (
        f"GET / must force revalidation; got Cache-Control={cc!r}"
    )


def test_static_assets_are_immutable(base_url: str) -> None:
    asset_hashes = compute_asset_hashes(_STATIC_DIR)
    assert asset_hashes, "no hashable assets found under app/webapp/static"
    name = "main.js"
    stamp = asset_hashes[name]
    res = requests.get(
        f"{base_url}/static/{name}?v={stamp}", verify=False, timeout=5
    )
    res.raise_for_status()
    cc = res.headers.get("Cache-Control", "")
    assert "immutable" in cc and "max-age=31536000" in cc, (
        f"GET /static/{name} must be immutable for a year; "
        f"got Cache-Control={cc!r}"
    )


def test_served_index_hashes_match_disk(base_url: str) -> None:
    """The check that catches 'edited a JS module but didn't restart'."""
    res = requests.get(f"{base_url}/", verify=False, timeout=5)
    res.raise_for_status()
    served = {
        m.group("name"): m.group("hash")
        for m in _INDEX_ASSET_RE.finditer(res.text)
    }
    assert served, "no hashed /static/*.{css,js} references in served index.html"
    on_disk = compute_asset_hashes(_STATIC_DIR)
    for name, stamp in served.items():
        expected = on_disk.get(name)
        assert expected is not None, (
            f"served index references {name} but it isn't on disk"
        )
        assert stamp == expected, (
            f"{name}: served stamp {stamp!r} != fleet hash {expected!r} — "
            "the tray was computed against different bytes (needs restart)"
        )


def test_api_version_shape(base_url: str) -> None:
    res = requests.get(f"{base_url}/api/version", verify=False, timeout=5)
    res.raise_for_status()
    body = res.json()
    for key in ("git_sha", "built_at", "asset_hash"):
        assert key in body, f"/api/version missing key {key!r}: {body}"
        assert isinstance(body[key], str) and body[key], (
            f"/api/version[{key}] is empty or not a string: {body[key]!r}"
        )
