# 2026-05-12 — Initial MVP

## What was done

Built the photo-ocr app per `docs/photo-ocr-app.md`, mirroring
`voice-transcriber`'s conventions, archive shape, auth model, and folder
layout. All six phases from the spec, one pass.

## Files added (high-level)

- **Top-level:** `launcher.py`, `setup.bat`, `webapp.bat`,
  `webapp_tunnel_named.bat`, `tray.bat`, `requirements.txt`,
  `requirements-dev.txt`, `pytest.ini`, `package.json`, `.gitignore`,
  `CLAUDE.md`, `AGENTS.md`, `README.md`, `LICENSE`.
- **`src/`:** `app_config.py`, `webapp_config.py`, `image_utils.py`,
  `ocr_client.py`, `ocr_prompts.py`, `archive.py`, `diagnostics.py`.
- **`app/webapp/`:** `server.py` (all routes + bearer-token middleware +
  lifespan cleanup), `manager.py` (adopt-or-spawn uvicorn),
  `static/index.html`, `static/app.js`, `static/styles.css`,
  `static/manifest.webmanifest`.
- **`app/cli/`:** dispatcher + `tray`, `webapp`, `extract` subcommands.
- **`app/tray/`:** `tray.py` — pystray icon that owns the webapp lifecycle.
- **`config/`:** `config.json`, `webapp_config.sample.json`, `ocr_prompts.json`.
- **`scripts/`:** `gen_app_icons.py`, `gen_ssl_cert.py`, `gen_token.py`,
  `set_password.py`, `run_named_tunnel.py`.
- **`webapp/cloudflared.sample.yml`** (committed tunnel config template).
- **`tests/`:** `test_app_config.py`, `test_webapp_config.py`,
  `test_image_utils.py`, `test_ocr_prompts.py`, `test_ocr_client.py`,
  `test_archive.py`, `test_webapp_api_basics.py`,
  `test_webapp_api_sessions.py`, `test_webapp_api_extract.py`,
  `test_webapp_api_auth.py`, `test_static_app_js.py`.

Moved planning artefacts into `docs/`:
- `docs/photo-ocr-app.md` (the spec)
- `docs/photo_ocr_validation.py` (feasibility probe)

## Differences from the spec

- The spec proposed putting the repo at `E:\automation\photo-transcriber\`
  as a new sibling. We reused the existing `E:\automation\photo-ocr\`
  folder per user preference and named the GitHub repo to match.
- Frontend drag-to-reorder ships as ◀ / ▶ buttons in v1 (visual reorder
  only — extract still uses stored upload order; the spec's Phase 5
  bullet about drag-to-reorder is deferred).
- HEIC support is best-effort: `pillow-heif` is in `requirements.txt` so
  it's available, but a missing wheel surfaces as a clear
  `ImageValidationError` rather than a crash.

## Validation run

- `.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt`
- `.\.venv\Scripts\python.exe -m pytest`
- `.\.venv\Scripts\python.exe scripts\gen_app_icons.py` to seed the PWA icons.
- Smoke check: `.\.venv\Scripts\python.exe launcher.py webapp` then
  `curl http://127.0.0.1:8444/healthz` returns `{"ok": true}`.
