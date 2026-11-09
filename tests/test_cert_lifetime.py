"""The cert pair is served only while the leaf is inside Apple's 398-day cap.

iOS rejects a longer leaf, which makes the PWA unreachable from an iPhone.
Throwaway certs are built in a temp dir; the real pair is never touched.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.webapp.manager import APPLE_MAX_VALIDITY_DAYS, cert_paths, leaf_validity_days


def _write_pair(root: Path, days: int) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.example")])
    start = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + datetime.timedelta(days=days))
        .sign(key, hashes.SHA256())
    )
    cert_dir = root / "webapp" / "certificates"
    cert_dir.mkdir(parents=True)
    (cert_dir / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (cert_dir / "key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


def test_leaf_inside_cap_is_served(tmp_path: Path) -> None:
    _write_pair(tmp_path, APPLE_MAX_VALIDITY_DAYS)
    assert cert_paths(tmp_path) is not None


def test_leaf_over_cap_is_refused_and_warned_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_pair(tmp_path, APPLE_MAX_VALIDITY_DAYS + 1)
    with caplog.at_level(logging.WARNING, logger="app.webapp.manager"):
        assert cert_paths(tmp_path) is None
        assert cert_paths(tmp_path) is None
    assert leaf_validity_days(tmp_path / "webapp" / "certificates" / "cert.pem") == 399
    warnings = [r for r in caplog.records if "398-day cap" in r.getMessage()]
    assert len(warnings) == 1


def test_unreadable_cert_is_not_treated_as_over_cap(tmp_path: Path) -> None:
    _write_pair(tmp_path, 90)
    (tmp_path / "webapp" / "certificates" / "cert.pem").write_bytes(b"not a cert")
    assert cert_paths(tmp_path) is not None
