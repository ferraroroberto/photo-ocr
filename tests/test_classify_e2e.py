"""Anti-drift tests for the `[e2e]` routing table in `.fleet.toml`.

`scripts/classify_e2e.py` supplies only the mechanism; the path -> tier rules
live in this repo's own `.fleet.toml`. These tests pin one representative path
per declared rule so a table edit that silently re-tiers a directory fails
here instead of quietly under-testing a PR. Convention:
project-scaffolding `docs/e2e-routing.md`.
"""

from __future__ import annotations

import pytest

from scripts.classify_e2e import FLEET_TOML, classify, load_config


@pytest.fixture(scope="module")
def config():
    """The real, live `[e2e]` table -- never a fixture copy."""
    cfg = load_config(FLEET_TOML)
    assert cfg.source == "declared", (
        f"[e2e] table unusable (source={cfg.source}); every diff would "
        "fail-safe to the full suite"
    )
    return cfg


def route(paths, config):
    return classify(paths, config).tier


# --- one representative path per declared rule ------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "docs/consuming-the-session-api.md",
        "docs/architecture.mmd",
        "README.md",
        "CLAUDE.md",
        "LICENSE",
        "assets/tray/photo-ocr.ico",
        "assets/stream-deck/photo-ocr-144.png",
    ],
)
def test_inert_paths_route_skip(path, config):
    assert route([path], config) == "skip"


@pytest.mark.parametrize(
    "path",
    [
        "app/webapp/static/icon-192.png",
        "app/webapp/static/favicon.ico",
    ],
)
def test_served_images_route_static(path, config):
    assert route([path], config) == "static"


@pytest.mark.parametrize(
    "path",
    [
        "app/webapp/static/main.js",
        "app/webapp/static/index.html",
        "app/webapp/static/_vendored/nav/nav-tabs.css",
        "app/webapp/routers/sessions.py",
        "app/webapp/server.py",
        "src/ocr_client.py",
        "src/static_versioning.py",
    ],
)
def test_rendered_surface_routes_full(path, config):
    assert route([path], config) == "full"


# --- the fail-safe invariant ------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "app/tray/tray_app.py",
        "app/cli/main.py",
        "launcher.py",
        "config/config.json",
        "requirements.txt",
    ],
)
def test_unclassified_paths_fail_safe_to_full(path, config):
    """Anything the table does not name must escalate, never narrow."""
    assert route([path], config) == "full"


def test_mixed_diff_takes_the_widest_tier(config):
    """The worst-matching path wins across the whole diff."""
    assert route(["docs/x.md", "app/webapp/static/icon-192.png"], config) == "static"
    assert route(["docs/x.md", "src/ocr_client.py"], config) == "full"
    assert route(["app/webapp/static/icon-192.png", "src/ocr_client.py"], config) == "full"


def test_empty_diff_fail_safes_to_full(config):
    """A clean tree cannot prove narrowness."""
    assert route([], config) == "full"
