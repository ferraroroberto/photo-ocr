"""Regression net for issue #3 (commit 279cc9c): the served leaf
certificate must stay within Apple's 398-day maximum validity window.

iOS/macOS reject a TLS leaf whose validity span exceeds 398 days
(``NSURLErrorServerCertificateHasUnknownRoot`` family) — a too-long cert
makes the whole PWA unreachable from an iPhone. This pulls the leaf the
tray is actually serving over the wire and measures its window. The
Tailscale-issued Let's Encrypt leaf (issue #70) is ~90 days, comfortably
inside the cap; the net stays because it guards whatever ends up served.

Non-browser: inspects the served cert directly, so it runs once on the
chromium projection rather than twice.
"""

from __future__ import annotations

import ssl
from urllib.parse import urlparse

import pytest
from cryptography import x509

pytestmark = pytest.mark.smoke

# Apple's hard cap; the Let's Encrypt leaf from `tailscale cert` is ~90
# days, far under it.
_APPLE_MAX_VALIDITY_DAYS = 398


@pytest.fixture(scope="session", autouse=True)
def _run_once(browser_name: str) -> None:
    if browser_name != "chromium":
        pytest.skip("server-side check; runs once on the chromium projection")


def _served_leaf_cert(base_url: str) -> x509.Certificate:
    parsed = urlparse(base_url)
    if parsed.scheme != "https":
        pytest.skip(f"{base_url} is not HTTPS — no leaf certificate to inspect")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 443
    pem = ssl.get_server_certificate((host, port))
    return x509.load_pem_x509_certificate(pem.encode("ascii"))


def test_leaf_cert_within_apple_398_day_cap(base_url: str) -> None:
    cert = _served_leaf_cert(base_url)
    span = cert.not_valid_after_utc - cert.not_valid_before_utc
    assert span.days <= _APPLE_MAX_VALIDITY_DAYS, (
        f"served leaf cert is valid for {span.days} days — exceeds Apple's "
        f"{_APPLE_MAX_VALIDITY_DAYS}-day cap; iOS will reject it "
        "(see issue #3, commit 279cc9c)"
    )
