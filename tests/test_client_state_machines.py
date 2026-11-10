"""The client-side state machines, driven through Node.

``photo_state.js`` (one photo's upload) and ``extract_phase.js`` (the phase the
poll loop reads) are DOM-free ES modules, so a Node one-liner imports them and
prints JSON. The scoreboard reads only Python tests, which is why these live
here rather than under ``static/__tests__``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

from src.no_window import NO_WINDOW

STATIC_DIR = Path(__file__).resolve().parent.parent / "app" / "webapp" / "static"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


def _run(script: str) -> Any:
    """Run ``script`` as an ES module with the static dir's modules importable."""
    header = (
        f"import * as photo from {json.dumps((STATIC_DIR / 'photo_state.js').as_uri())};\n"
        f"import * as phase from {json.dumps((STATIC_DIR / 'extract_phase.js').as_uri())};\n"
    )
    proc = subprocess.run(
        [str(NODE), "--input-type=module", "-e", header + textwrap.dedent(script)],
        capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL,
        creationflags=NO_WINDOW, timeout=30, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_photo_illegal_transition_is_refused_and_leaves_the_photo_untouched() -> None:
    # state-machine-proof: photo-upload
    out = _run("""
        const p = { status: 'ready' };
        let refused = null;
        try { photo.setPhotoStatus(p, 'uploading'); } catch (e) { refused = e.name + ':' + e.message; }
        console.log(JSON.stringify({ refused, status: p.status,
          isIllegal: refused !== null }));
    """)
    assert out["isIllegal"]
    assert "cannot move from ready to uploading" in out["refused"]
    assert out["status"] == "ready"


def test_photo_walks_the_upload_path_and_failed_is_final() -> None:
    out = _run("""
        const ok = { status: photo.newPhotoStatus() };
        const seen = [photo.derivePhotoStatus(ok)];
        photo.setPhotoStatus(ok, 'uploading'); seen.push(ok.status);
        photo.setPhotoStatus(ok, 'ready'); seen.push(ok.status);
        const bad = { status: 'pending' };
        photo.setPhotoStatus(bad, 'uploading');
        photo.setPhotoStatus(bad, 'failed');
        let back = false;
        try { photo.setPhotoStatus(bad, 'ready'); } catch (e) { back = true; }
        let skip = false;
        try { photo.setPhotoStatus({}, 'ready'); } catch (e) { skip = true; }
        console.log(JSON.stringify({ seen, back, skip, failed: photo.isPhotoFailed(bad) }));
    """)
    assert out == {
        "seen": ["pending", "uploading", "ready"], "back": True, "skip": True, "failed": True,
    }


def test_photo_derive_table() -> None:
    out = _run("""
        const row = (s) => { const p = s === undefined ? {} : { status: s };
          return [photo.isPhotoReady(p), photo.isPhotoFailed(p), photo.isPhotoUploading(p)]; };
        let unknown = false;
        try { photo.derivePhotoStatus({ status: 'paused' }); } catch (e) { unknown = true; }
        console.log(JSON.stringify({
          none: row(undefined), pending: row('pending'), uploading: row('uploading'),
          ready: row('ready'), failed: row('failed'), unknown }));
    """)
    assert out == {
        "none": [False, False, True], "pending": [False, False, True],
        "uploading": [False, False, True], "ready": [True, False, False],
        "failed": [False, True, False], "unknown": True,
    }


def test_extract_phase_backward_poll_is_refused() -> None:
    # state-machine-proof: extract-phase-client
    out = _run("""
        let refused = null;
        try { phase.requireForwardPhase('merging', 'running'); } catch (e) { refused = e.message; }
        console.log(JSON.stringify({ refused,
          same: phase.requireForwardPhase('running', 'running'),
          skip: phase.requireForwardPhase('queued', 'merging'),
          done: phase.requireForwardPhase('merging', 'succeeded'),
          fresh: phase.requireForwardPhase('idle', 'queued') }));
    """)
    assert out["refused"] == "extraction moved back from merging to running"
    assert (out["same"], out["skip"], out["done"], out["fresh"]) == (
        "running", "merging", "succeeded", "queued",
    )


def test_extract_phase_derive_table_matches_the_server_values() -> None:
    from src.extract_state import ExtractPhase as ServerPhase

    out = _run("""
        const body = (v) => phase.deriveExtractPhase(v === undefined ? {} : { phase: v });
        console.log(JSON.stringify({
          values: Object.values(phase.ExtractPhase),
          unknown: body('paused'), missing: body(undefined), nullBody: phase.deriveExtractPhase(null),
          done: phase.isExtractDone({ phase: 'succeeded' }),
          failed: phase.isExtractFailed({ phase: 'failed' }),
          running: [phase.isExtractDone({ phase: 'running' }), phase.isExtractFailed({ phase: 'running' })] }));
    """)
    assert sorted(out["values"]) == sorted(p.value for p in ServerPhase)
    assert out["unknown"] == out["missing"] == out["nullBody"] == "idle"
    assert out["done"] is True and out["failed"] is True
    assert out["running"] == [False, False]
