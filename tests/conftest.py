"""pytest fixtures shared across the test suite."""

from __future__ import annotations

# Standard library imports
import io
import sys
from pathlib import Path

import pytest
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _isolated_webapp_config(tmp_path_factory, monkeypatch) -> None:
    """Point webapp-config loading at a throwaway path.

    ``create_app()`` calls ``load_webapp_config()`` with no argument, so
    every API test would otherwise read the developer's real
    ``config/webapp_config.json`` — which carries a live ``auth_token``
    and would gate (401) every non-loopback request the TestClient
    makes. Isolating the path means tests build on clean
    ``WebappConfig()`` defaults regardless of local machine state.
    """
    import src.webapp_config as webapp_config

    isolated = tmp_path_factory.mktemp("webapp_cfg") / "webapp_config.json"
    monkeypatch.setattr(webapp_config, "DEFAULT_CONFIG_PATH", isolated)


@pytest.fixture
def jpeg_bytes() -> bytes:
    """A tiny in-memory JPEG so image_utils has something to chew on."""
    img = Image.new("RGB", (128, 96), (200, 80, 40))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


@pytest.fixture
def png_bytes() -> bytes:
    img = Image.new("RGB", (64, 64), (40, 80, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def big_jpeg_bytes() -> bytes:
    """Above max_dim threshold so the downscale path runs."""
    img = Image.new("RGB", (3000, 2000), (100, 100, 100))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()
