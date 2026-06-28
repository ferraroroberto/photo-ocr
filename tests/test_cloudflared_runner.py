"""Tests for src/cloudflared_runner.py — the shared tunnel lifecycle.

Covers the pure / I/O-light pieces extracted from the tray and the
headless script: hostname parsing, URL building with the auth token, and
the persist/remove file round-trip. The process spawn/stop paths drive a
live external binary and are exercised by the two entrypoints' own
import+start smoke checks, not here.
"""

from __future__ import annotations

from pathlib import Path

from src import cloudflared_runner


def _write_yaml(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_read_tunnel_hostname_first_ingress(tmp_path: Path) -> None:
    cfg = _write_yaml(
        tmp_path / "cloudflared.yml",
        "ingress:\n"
        "  - hostname: ocr.example.com\n"
        "    service: https://localhost:8444\n"
        "  - service: http_status:404\n",
    )
    assert cloudflared_runner.read_tunnel_hostname(cfg) == "ocr.example.com"


def test_read_tunnel_hostname_missing_file(tmp_path: Path) -> None:
    assert cloudflared_runner.read_tunnel_hostname(tmp_path / "nope.yml") is None


def test_read_tunnel_hostname_no_ingress(tmp_path: Path) -> None:
    cfg = _write_yaml(tmp_path / "cloudflared.yml", "tunnel: abc\n")
    assert cloudflared_runner.read_tunnel_hostname(cfg) is None


def test_read_tunnel_hostname_unparseable(tmp_path: Path) -> None:
    cfg = _write_yaml(tmp_path / "cloudflared.yml", "ingress: [unterminated\n")
    assert cloudflared_runner.read_tunnel_hostname(cfg) is None


def test_build_tunnel_url_without_token() -> None:
    assert cloudflared_runner.build_tunnel_url("ocr.example.com", "") == (
        "https://ocr.example.com"
    )


def test_build_tunnel_url_with_token() -> None:
    assert cloudflared_runner.build_tunnel_url("ocr.example.com", "s3cret") == (
        "https://ocr.example.com?token=s3cret"
    )


def test_write_and_remove_tunnel_url(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "last_tunnel_url.txt"
    cloudflared_runner.write_tunnel_url(target, "https://ocr.example.com?token=x")
    assert target.read_text(encoding="utf-8") == "https://ocr.example.com?token=x\n"

    cloudflared_runner.remove_tunnel_url(target)
    assert not target.exists()
    # Idempotent: removing an already-gone file is a no-op, not an error.
    cloudflared_runner.remove_tunnel_url(target)
