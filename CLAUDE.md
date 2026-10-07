# Project Instructions

Canonical agent instructions; other agents reach it via the one-line `AGENTS.md` pointer.

## This repository
Mobile-first photo OCR — capture/upload N photos of a document, screen, or page; a vision-capable model on the local LLM hub returns one clean, deduplicated, copy-ready text. Sister project to `voice-transcriber` (same conventions, archive shape, auth model), for pixels instead of audio. See `README.md` for setup, layout, and usage.

**Project specifics:**

- **Stack:** FastAPI + vanilla JS — **not** Streamlit; do not introduce Streamlit.
- **Config & secrets:** there is no `.env`. Project config lives in `config/config.json` (committed) and runtime UI prefs + secrets (`auth_token`, `auth_password`) in `config/webapp_config.json` (gitignored).
- **Verification — webapp boot check:** `& .\.venv\Scripts\python.exe -m uvicorn app.webapp.server:app --host 127.0.0.1 --port 8444` then `curl http://127.0.0.1:8444/healthz`.
- **Pre-ship gate:** any change under `app/webapp/` (or the webapp-facing `src/` modules) must pass `powershell.exe -File scripts/verify-before-ship.ps1` before it is declared done. Use Windows PowerShell 5.1 (`powershell.exe`, or absolute `C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe`), not `pwsh` (a 0-byte WindowsApps stub that fails non-interactively).
- **e2e runtime contract:** the e2e leg of the pre-ship gate collects 32 nodes (Chromium + WebKit/iPhone, auto-booted): 25 executed, 7 skipped by design (the server-side cache-busting and cert checks run once on Chromium, the iPhone-projection check on WebKit only). Measured 2026-10-07 (one warm run, box shared with the cleanup job): 23 s session time, of which the rendered-geometry node (`test_geometry.py`, 8 viewport x theme legs per engine) is ~6 s (whole gate, byte-compile + 231 unit tests + e2e, ~33 s); a cold first run adds several seconds. Investigate a gate that takes over ~30 s; a test that sleeps or polls in real time belongs on the page clock (`page.clock`), not a timer. The gate writes JUnit to `webapp/e2e-junit.xml` (gitignored), declared as `[e2e] junit_xml` in `.fleet.toml` for `/e2e-audit`. The gate routes the e2e leg on the branch diff vs `origin/main` through the vendored `scripts/classify_e2e.py` (rules in `.fleet.toml` `[e2e]`): `skip` (docs / markdown / desktop assets only) skips the leg and prints the tier and reason, `static` runs the declared smoke slice (`tests/e2e/test_smoke.py`, Chromium), `full` runs the whole suite; a classifier error, a missing tier or an unknown tier falls back to `full` with its own message, and CI always runs `full`. Counts and timings describe a `full` run; a skipped leg writes no JUnit, so `webapp/e2e-junit.xml` is then the last run that did.
- **Restart and verify before hand-off:** no hot-reload — edits do nothing until the `:8444` process restarts. Canonical restart: **`tray.bat --restart`** (orphan-proof: kills the tray subtree, reclaims `:8444` by PID scoped to this repo's `.venv`, starts fresh). Don't hand-roll the kill (misses an orphaned port holder). By-hand fallback only: kill the process listening on `:8444` (`Get-NetTCPConnection -LocalPort 8444`) — never a blanket `pythonw`/`python` kill, sister apps must survive — then relaunch via `tray.bat`. **Confirm the new build is live** via `curl -k https://127.0.0.1:8444/healthz` (200) before handing off. (The tray-launched webapp serves **HTTPS** with a `.ts.net` cert from `tailscale cert`, hence `-k`/`https` — plain `http://` fails at TLS and reads as a false "not live". The manual boot-check above is plain `http` on purpose: no cert flags.)

## UX surface
*Design-conformance gate read by `/issue-{start,finish,yolo}` (`project-scaffolding#83`); live, parseable block.*

- design spec applies: yes        # `no` = permanent no-op; this is a real PWA
- paths:
  - app/webapp/static/**/*.css
  - app/webapp/static/**/*.{js,html}
- key views:                      # single tabbed SPA served at `/`
  - /          (Capture / History tabs behind the vendored bottom-tab nav; Settings opens from the header gear)

## Internal architecture

[`docs/architecture.mmd`](docs/architecture.mmd) is a hand-authored Mermaid diagram of this repo's structure (`app/cli`, `app/tray`, `app/webapp`, `src/`, `config/`, `scripts/`, `archive/`, `tests/`, external `local-llm-hub`/`cloudflared`/Tailscale). Update it in the same PR as any material structural change (router added/moved, `src/` module relocated, new external dependency) — same anti-staleness contract as `.fleet.toml`'s description (`ferraroroberto/fleet-config#256`). Not auto-generated, not covered by `scripts/verify-before-ship.ps1`.
