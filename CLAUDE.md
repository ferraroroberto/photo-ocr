# Project Instructions

Canonical instructions for AI coding agents working in this repository. Claude Code reads this file directly as project memory. Other agents (Cursor, Codex, etc.) reach it via the one-line `AGENTS.md` pointer.

## This repository
Mobile-first photo OCR — capture/upload N photos of a document, screen, or page; a vision-capable model on the local LLM hub returns one clean, deduplicated, copy-ready text. Sister project to `voice-transcriber` (same conventions, same archive shape, same auth model), but for pixels instead of audio. See `README.md` for setup, layout, and usage.

**Project specifics:**

- **Stack:** FastAPI + vanilla JS — **not** Streamlit; do not introduce Streamlit.
- **Config & secrets:** there is no `.env`. Project config lives in `config/config.json` (committed) and runtime UI prefs + secrets (`auth_token`, `auth_password`) in `config/webapp_config.json` (gitignored).
- **Verification — webapp boot check:** `& .\.venv\Scripts\python.exe -m uvicorn app.webapp.server:app --host 127.0.0.1 --port 8444` then `curl http://127.0.0.1:8444/healthz`.
- **Pre-ship gate:** any change under `app/webapp/` (or the webapp-facing `src/` modules) must pass `powershell.exe -File scripts/verify-before-ship.ps1` before it is declared done. Use Windows PowerShell 5.1 (`powershell.exe`, or the absolute `C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe`), not `pwsh` — the default `pwsh` on PATH is a 0-byte WindowsApps reparse stub that fails non-interactively.
- **Restart and verify before hand-off:** the running webapp has no hot-reload — code edits do nothing until the `:8444` process is restarted. The canonical restart is **`tray.bat --restart`** — the orphan-proof reclaim-then-start that kills the tray subtree, then reclaims `:8444` by PID scoped to this repo's `.venv` (CommandLine-matched), then starts fresh. Run that, don't hand-roll the kill (a by-hand kill misses an orphaned port holder). As a by-hand fallback only, kill the process listening on `:8444` (`Get-NetTCPConnection -LocalPort 8444`) — never a blanket `pythonw`/`python` kill, sister apps must survive — then relaunch via `tray.bat`. **Confirm the new build is live** via `curl -k https://127.0.0.1:8444/healthz` (200) before handing off; don't leave a stale process serving. (The tray-launched webapp serves **HTTPS** with a self-signed cert, hence `-k` and `https` — a plain `http://` probe fails at the TLS layer and reads as a false "not live". The manual boot-check above is plain `http` on purpose: it starts uvicorn without the cert flags.)

## UX surface
*The design-conformance gate the `/issue-{start,finish,yolo}` skills read (convention: `project-scaffolding#83`). This is a live, parseable block — the product is the FastAPI + static PWA under `app/webapp/`.*

- design spec applies: yes        # `no` would make the gate a permanent no-op; this repo serves a real PWA
- paths:
  - app/webapp/static/**/*.css
  - app/webapp/static/**/*.{js,html}
- key views:                      # single tabbed SPA served at `/`
  - /          (capture → extract → history panels)
